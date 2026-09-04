"""Channel-independent Sales Agent orchestration."""

from __future__ import annotations

import logging
import uuid

from pydantic import ValidationError

from app.schemas import (
    BookingStatus,
    ConversationMessage,
    ConversationSession,
    ConversationStage,
    EvidenceAssessment,
    EvidenceLevel,
    FitLevel,
    MessageRole,
    NextAction,
    ObjectionType,
    QualificationEvidence,
    RequestScope,
    SalesAgentDraft,
    SalesAgentOutput,
    ServiceRoute,
)
from app.services.guardrails import GuardrailKind, inspect_message
from app.services.llm import (
    LLMJSONDecodeError,
    LLMProviderError,
    LLMRequest,
    LLMService,
    LLMServiceError,
)
from app.services.prompts import PromptLoadError, PromptLoader
from app.services.sales_rules import (
    calculate_qualification,
    detect_objection,
    is_transition_allowed,
    message_offers_booking,
    route_service,
    should_offer_booking,
)


logger = logging.getLogger(__name__)


class DraftConsistencyError(ValueError):
    """Raised when generated metadata conflicts with deterministic policy."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retry_instruction: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_instruction = retry_instruction
        self.details = details or {}


class LoggedAgentFailure(RuntimeError):
    """Sanitized exception used to emit a stack trace without provider payloads."""


class SalesAgent:
    """Orchestrate one safe, structured sales turn at a time."""

    def __init__(
        self,
        llm: LLMService,
        *,
        prompt_loader: PromptLoader | None = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts != 2:
            raise ValueError("SalesAgent requires exactly two structured-output attempts")
        self._llm = llm
        self._prompt_loader = prompt_loader or PromptLoader()
        self._max_attempts = max_attempts

    def handle_message(
        self,
        session: ConversationSession,
        user_message: str,
        *,
        turn_id: str | None = None,
        conversation_id: str | None = None,
    ) -> SalesAgentOutput:
        """Process one user message and update in-memory conversation state."""

        resolved_turn_id = turn_id or uuid.uuid4().hex[:12]
        inbound = ConversationMessage(role=MessageRole.USER, content=user_message)
        guardrail = inspect_message(inbound.content)
        if guardrail is not None:
            output = self._guardrail_output(session, guardrail.kind)
            self._record_turn(session, inbound, output)
            return output

        messages = [*session.messages, inbound]
        try:
            prompt = self._prompt_loader.load(session.prompt_version)
        except PromptLoadError:
            logger.exception(
                "sales_agent_failure turn_id=%s conversation_id=%s category=prompt_load",
                resolved_turn_id,
                conversation_id or "standalone",
            )
            output = self._fallback_output(
                session, resolved_turn_id, category="configuration"
            )
            self._record_turn(session, inbound, output)
            return output

        base_instructions = (
            f"{prompt.content}\n\n"
            f"Estado estrutural atual: {session.stage.value}. "
            f"Estado do agendamento: {session.booking.status.value}. "
            f"Perguntas de descoberta já feitas: {session.discovery_questions_asked}; "
            f"restantes até o limite: {max(0, 5 - session.discovery_questions_asked)}. "
            "Busque concluir em até 3 perguntas e nunca ultrapasse 5. "
            "Use apenas fatos presentes no histórico fornecido."
        )
        last_error: Exception | None = None
        retry_instruction: str | None = None
        for attempt in range(self._max_attempts):
            instructions = base_instructions
            if attempt:
                instructions += (
                    "\nA saída anterior foi inválida ou contrariou regras "
                    "determinísticas. Corrija-a sem inventar informações."
                )
                if retry_instruction:
                    instructions += f"\nCorreção obrigatória: {retry_instruction}"
            request = LLMRequest(
                instructions=instructions,
                messages=messages,
                output_schema=SalesAgentDraft.model_json_schema(),
                schema_name="sales_agent_draft",
            )
            try:
                raw = self._llm.complete(request)
                draft = SalesAgentDraft.model_validate(raw)
                output = self._finalize(session, inbound.content, draft)
            except DraftConsistencyError as exc:
                last_error = exc
                retry_instruction = exc.retry_instruction
                logger.warning(
                    "sales_agent_failure turn_id=%s conversation_id=%s "
                    "category=draft_consistency code=%s attempt=%d stage=%s "
                    "booking_status=%s details=%s",
                    resolved_turn_id,
                    conversation_id or "standalone",
                    exc.code,
                    attempt + 1,
                    session.stage.value,
                    session.booking.status.value,
                    exc.details,
                    exc_info=True,
                )
                continue
            except ValidationError as exc:
                last_error = exc
                self._log_validation_failure(
                    exc,
                    resolved_turn_id,
                    conversation_id,
                    session,
                    attempt + 1,
                )
                continue
            except LLMJSONDecodeError as exc:
                last_error = exc
                self._log_llm_failure(
                    exc, resolved_turn_id, conversation_id, session, attempt + 1,
                    "json_decode",
                )
                continue
            except (LLMProviderError, LLMServiceError) as exc:
                last_error = exc
                self._log_llm_failure(
                    exc, resolved_turn_id, conversation_id, session, attempt + 1,
                    "openai_call",
                )
                continue
            self._record_turn(session, inbound, output)
            if output.should_offer_booking:
                session.booking.status = BookingStatus.OFFERED
            return output

        category = (
            "provider"
            if isinstance(last_error, LLMServiceError)
            and not isinstance(last_error, LLMJSONDecodeError)
            else "validation"
        )
        if isinstance(last_error, DraftConsistencyError) and last_error.code == "question_limit":
            output = self._pause_output(session)
        else:
            output = self._fallback_output(session, resolved_turn_id, category=category)
        logger.error(
            "sales_agent_failure turn_id=%s conversation_id=%s category=safe_fallback "
            "last_error_type=%s",
            resolved_turn_id,
            conversation_id or "standalone",
            type(last_error).__name__ if last_error else "unknown",
        )
        self._record_turn(session, inbound, output)
        return output

    def _finalize(
        self,
        session: ConversationSession,
        user_message: str,
        draft: SalesAgentDraft,
    ) -> SalesAgentOutput:
        current_stage = session.stage
        if draft.request_scope is RequestScope.OUT_OF_SCOPE:
            return self._out_of_scope_output(session)
        result = calculate_qualification(draft.qualification)
        service = route_service(draft.routing_signals)
        objection = detect_objection(user_message, draft.objection)
        booking_eligible = should_offer_booking(
            fit=result.fit,
            service=service,
            primary_pain=draft.primary_pain,
            readiness=draft.qualification.readiness.level,
            objection=objection,
        )
        booking_ready = (
            booking_eligible
            and session.booking.status is BookingStatus.NOT_OFFERED
        )
        visible_booking_cta = message_offers_booking(draft.message)
        if draft.should_offer_booking is not booking_ready:
            retry = None
            if objection is not None:
                retry = (
                    f"A objeção {objection.value} está ativa. Use proposed_stage="
                    "OBJECTION, should_offer_booking=false, reconheça e trate a "
                    "objeção sem convidar para reunião, agendamento ou horários."
                )
            elif booking_eligible and not booking_ready:
                retry = (
                    "O convite de agendamento já foi apresentado. Use "
                    "should_offer_booking=false, responda apenas à mensagem atual "
                    "e não repita o convite nem mencione horários."
                )
            raise DraftConsistencyError(
                "booking CTA conflicts with deterministic policy",
                code="booking_cta_conflict",
                retry_instruction=retry,
                details={
                    "current_stage": current_stage.value,
                    "booking_status": session.booking.status.value,
                    "booking_ready": booking_ready,
                    "draft_booking_flag": draft.should_offer_booking,
                },
            )

        message = draft.message
        if booking_ready and not visible_booking_cta:
            message = f"{message.rstrip()} Quer que eu veja alguns horários disponíveis?"
        elif not booking_ready and visible_booking_cta:
            retry = None
            if objection is not None:
                retry = (
                    f"A objeção {objection.value} está ativa. Use proposed_stage="
                    "OBJECTION, should_offer_booking=false, reconheça e trate a "
                    "objeção sem convidar para reunião, agendamento ou horários."
                )
            raise DraftConsistencyError(
                "booking CTA conflicts with deterministic policy",
                code="visible_booking_cta_conflict",
                retry_instruction=retry,
                details={
                    "current_stage": current_stage.value,
                    "booking_ready": booking_ready,
                    "visible_booking_cta": visible_booking_cta,
                },
            )

        fit = result.fit
        if service is ServiceRoute.NO_CURRENT_FIT:
            stage = ConversationStage.NO_FIT
            fit = FitLevel.NO_FIT
            next_action = NextAction.CLOSE_HELPFULLY
        elif objection is not None:
            stage = ConversationStage.OBJECTION
            next_action = NextAction.ADDRESS_OBJECTION
        elif booking_ready:
            stage = ConversationStage.BOOKING
            next_action = NextAction.OFFER_BOOKING
        elif current_stage is ConversationStage.BOOKING:
            stage = ConversationStage.BOOKING
            next_action = NextAction.CONTINUE_DISCOVERY
        else:
            stage = draft.proposed_stage
            next_action = NextAction.CONTINUE_DISCOVERY

        if (
            next_action is NextAction.CONTINUE_DISCOVERY
            and "?" in message
            and session.discovery_questions_asked >= 5
        ):
            raise DraftConsistencyError(
                "discovery question limit exceeded",
                code="question_limit",
                retry_instruction=(
                    "Não faça outra pergunta. Resuma brevemente o que foi entendido "
                    "e pause a descoberta sem oferecer reunião sem elegibilidade."
                ),
                details={"questions_asked": session.discovery_questions_asked},
            )
        if (
            next_action is NextAction.CONTINUE_DISCOVERY
            and session.discovery_questions_asked >= 5
        ):
            next_action = NextAction.PAUSE_DISCOVERY

        if not is_transition_allowed(current_stage, stage):
            raise DraftConsistencyError(
                f"invalid transition from {current_stage.value} to {stage.value}",
                code="invalid_transition",
                details={
                    "current_stage": current_stage.value,
                    "proposed_stage": stage.value,
                },
            )

        return SalesAgentOutput(
            message=message,
            stage=stage,
            service=service,
            fit=fit,
            primary_pain=draft.primary_pain,
            objection=objection,
            qualification=draft.qualification,
            qualification_score=result.score,
            should_offer_booking=booking_ready,
            next_action=next_action,
        )

    @staticmethod
    def _guardrail_output(
        session: ConversationSession, kind: GuardrailKind
    ) -> SalesAgentOutput:
        if kind is GuardrailKind.OUT_OF_SCOPE:
            return SalesAgent._out_of_scope_output(session)
        if kind is GuardrailKind.SENSITIVE_CREDENTIALS:
            evidence = QualificationEvidence.empty()
            return SalesAgentOutput(
                message=(
                    "Não consigo acessar contas nem receber ou recuperar senhas. "
                    "Não compartilhe credenciais por aqui; procure diretamente os "
                    "canais oficiais da sua instituição financeira."
                ),
                stage=ConversationStage.NO_FIT,
                service=ServiceRoute.NO_CURRENT_FIT,
                fit=FitLevel.NO_FIT,
                primary_pain="account_access_request",
                objection=None,
                qualification=evidence,
                qualification_score=0,
                should_offer_booking=False,
                next_action=NextAction.CLOSE_HELPFULLY,
            )

        empty = EvidenceAssessment(level=EvidenceLevel.NONE, evidence=None)
        evidence = QualificationEvidence(
            need=EvidenceAssessment(
                level=EvidenceLevel.MODERATE,
                evidence="Pedido explícito de orientação sobre investimentos.",
            ),
            financial_complexity=empty,
            readiness=empty,
            urgency=empty,
            service_fit=EvidenceAssessment(
                level=EvidenceLevel.MODERATE,
                evidence="O pedido indica uma possível necessidade de apoio em investimentos.",
            ),
        )
        result = calculate_qualification(evidence)
        return SalesAgentOutput(
            message=(
                "Essa decisão depende dos seus objetivos, prazo e contexto, então "
                "não posso recomendar um investimento específico por aqui. "
                "O que você busca alcançar com esses recursos?"
            ),
            stage=ConversationStage.OBJECTION,
            service=ServiceRoute.INVESTMENT_ADVISORY,
            fit=result.fit,
            primary_pain="specific_investment_recommendation",
            objection=ObjectionType.WANTS_IMMEDIATE_RECOMMENDATION,
            qualification=evidence,
            qualification_score=result.score,
            should_offer_booking=False,
            next_action=NextAction.ADDRESS_OBJECTION,
        )

    @staticmethod
    def _fallback_output(
        session: ConversationSession, turn_id: str, *, category: str
    ) -> SalesAgentOutput:
        previous = session.last_output
        reference = f"REF-{turn_id[:8].upper()}"
        if category == "provider":
            message = (
                "Estou com uma instabilidade temporária e não consegui responder agora. "
                f"Tente novamente em instantes. Referência: {reference}."
            )
        else:
            message = (
                "Tive dificuldade para interpretar sua resposta com segurança. "
                f"Você pode reformular em uma frase? Referência: {reference}."
            )
        return SalesAgentOutput(
            message=message,
            stage=session.stage,
            service=previous.service if previous else None,
            fit=previous.fit if previous else FitLevel.NO_FIT,
            primary_pain=previous.primary_pain if previous else None,
            objection=previous.objection if previous else None,
            qualification=(
                previous.qualification if previous else QualificationEvidence.empty()
            ),
            qualification_score=previous.qualification_score if previous else 0,
            should_offer_booking=False,
            next_action=NextAction.RETRY_LATER,
        )

    @staticmethod
    def _out_of_scope_output(session: ConversationSession) -> SalesAgentOutput:
        previous = session.last_output
        return SalesAgentOutput(
            message=(
                "Posso ajudar apenas com organização financeira, necessidades de "
                "investimentos, nossos serviços e agendamento. Não consigo executar "
                "esse pedido, mas podemos retomar sua necessidade financeira."
            ),
            stage=session.stage,
            service=previous.service if previous else None,
            fit=previous.fit if previous else FitLevel.NO_FIT,
            primary_pain=previous.primary_pain if previous else None,
            objection=previous.objection if previous else None,
            qualification=previous.qualification if previous else QualificationEvidence.empty(),
            qualification_score=previous.qualification_score if previous else 0,
            should_offer_booking=False,
            next_action=NextAction.REDIRECT_TO_SCOPE,
        )

    @staticmethod
    def _pause_output(session: ConversationSession) -> SalesAgentOutput:
        previous = session.last_output
        return SalesAgentOutput(
            message=(
                "Obrigado por compartilhar esse contexto. Já tenho o suficiente para "
                "não prolongar a descoberta, mas ainda não há elementos para indicar "
                "o próximo passo com segurança. Se quiser, você pode acrescentar o "
                "que considerar mais importante."
            ),
            stage=session.stage,
            service=previous.service if previous else None,
            fit=previous.fit if previous else FitLevel.NO_FIT,
            primary_pain=previous.primary_pain if previous else None,
            objection=previous.objection if previous else None,
            qualification=previous.qualification if previous else QualificationEvidence.empty(),
            qualification_score=previous.qualification_score if previous else 0,
            should_offer_booking=False,
            next_action=NextAction.PAUSE_DISCOVERY,
        )

    @staticmethod
    def _log_llm_failure(
        exc: Exception,
        turn_id: str,
        conversation_id: str | None,
        session: ConversationSession,
        attempt: int,
        category: str,
    ) -> None:
        root = exc.__cause__
        root_type = type(root).__name__ if root else type(exc).__name__
        status = getattr(root, "status_code", None)
        try:
            raise LoggedAgentFailure(f"{category}:{root_type}") from None
        except LoggedAgentFailure:
            logger.exception(
                "sales_agent_failure turn_id=%s conversation_id=%s category=%s "
                "attempt=%d stage=%s booking_status=%s error_type=%s status=%s",
                turn_id,
                conversation_id or "standalone",
                category,
                attempt,
                session.stage.value,
                session.booking.status.value,
                root_type,
                status,
            )

    @staticmethod
    def _log_validation_failure(
        exc: ValidationError,
        turn_id: str,
        conversation_id: str | None,
        session: ConversationSession,
        attempt: int,
    ) -> None:
        errors = [(item["loc"], item["type"]) for item in exc.errors(include_input=False)]
        try:
            raise LoggedAgentFailure("pydantic_validation") from None
        except LoggedAgentFailure:
            logger.exception(
                "sales_agent_failure turn_id=%s conversation_id=%s "
                "category=pydantic_validation attempt=%d stage=%s errors=%s",
                turn_id,
                conversation_id or "standalone",
                attempt,
                session.stage.value,
                errors,
            )

    @staticmethod
    def _record_turn(
        session: ConversationSession,
        inbound: ConversationMessage,
        output: SalesAgentOutput,
    ) -> None:
        session.messages.append(inbound)
        session.messages.append(
            ConversationMessage(role=MessageRole.ASSISTANT, content=output.message)
        )
        session.stage = output.stage
        session.last_output = output
        if output.next_action is NextAction.CONTINUE_DISCOVERY and "?" in output.message:
            session.discovery_questions_asked += 1
