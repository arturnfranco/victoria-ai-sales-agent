"""Channel-independent Sales Agent orchestration."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.schemas import (
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
    SalesAgentDraft,
    SalesAgentOutput,
    ServiceRoute,
)
from app.services.guardrails import GuardrailKind, inspect_message
from app.services.llm import LLMRequest, LLMService, LLMServiceError
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
        self, session: ConversationSession, user_message: str
    ) -> SalesAgentOutput:
        """Process one user message and update in-memory conversation state."""

        inbound = ConversationMessage(role=MessageRole.USER, content=user_message)
        guardrail = inspect_message(inbound.content)
        if guardrail is not None:
            output = self._guardrail_output(guardrail.kind)
            self._record_turn(session, inbound, output)
            return output

        messages = [*session.messages, inbound]
        try:
            prompt = self._prompt_loader.load(session.prompt_version)
        except PromptLoadError:
            logger.exception("sales_agent_failure category=prompt_load")
            output = self._fallback_output(session)
            self._record_turn(session, inbound, output)
            return output

        base_instructions = (
            f"{prompt.content}\n\n"
            f"Estado estrutural atual: {session.stage.value}. "
            "Use apenas fatos presentes no histórico fornecido."
        )
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            instructions = base_instructions
            if attempt:
                instructions += (
                    "\nA saída anterior foi inválida ou contrariou regras "
                    "determinísticas. Corrija-a sem inventar informações."
                )
            request = LLMRequest(
                instructions=instructions,
                messages=messages,
                output_schema=SalesAgentDraft.model_json_schema(),
                schema_name="sales_agent_draft",
            )
            try:
                raw = self._llm.complete(request)
                draft = SalesAgentDraft.model_validate(raw)
                output = self._finalize(session.stage, inbound.content, draft)
            except (
                DraftConsistencyError,
                LLMServiceError,
                ValidationError,
            ) as exc:
                last_error = exc
                logger.warning(
                    "sales_agent_failure category=structured_output attempt=%d",
                    attempt + 1,
                )
                continue
            self._record_turn(session, inbound, output)
            return output

        if last_error is not None:
            logger.error("sales_agent_failure category=safe_fallback")
        output = self._fallback_output(session)
        self._record_turn(session, inbound, output)
        return output

    def _finalize(
        self,
        current_stage: ConversationStage,
        user_message: str,
        draft: SalesAgentDraft,
    ) -> SalesAgentOutput:
        result = calculate_qualification(draft.qualification)
        service = route_service(draft.routing_signals)
        objection = detect_objection(user_message, draft.objection)
        booking_ready = should_offer_booking(
            fit=result.fit,
            service=service,
            primary_pain=draft.primary_pain,
            readiness=draft.qualification.readiness.level,
            objection=objection,
        )
        visible_booking_cta = message_offers_booking(draft.message)
        if (
            draft.should_offer_booking is not booking_ready
            or visible_booking_cta is not booking_ready
        ):
            raise DraftConsistencyError("booking CTA conflicts with deterministic policy")

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
        else:
            stage = draft.proposed_stage
            next_action = NextAction.CONTINUE_DISCOVERY

        if not is_transition_allowed(current_stage, stage):
            raise DraftConsistencyError(
                f"invalid transition from {current_stage.value} to {stage.value}"
            )

        return SalesAgentOutput(
            message=draft.message,
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
    def _guardrail_output(kind: GuardrailKind) -> SalesAgentOutput:
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
    def _fallback_output(session: ConversationSession) -> SalesAgentOutput:
        previous = session.last_output
        return SalesAgentOutput(
            message=(
                "Não consegui processar sua mensagem com segurança agora. "
                "Podemos tentar novamente em instantes?"
            ),
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
