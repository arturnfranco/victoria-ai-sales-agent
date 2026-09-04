"""Persistent, channel-independent orchestration for sales conversations."""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents import SalesAgent
from app.db.models import Conversation, Lead, Message
from app.repositories import (
    BookingRepository,
    ConversationRepository,
    LeadRepository,
    MessageRepository,
)
from app.schemas import (
    AvailabilityPreference,
    BookingRequest,
    BookingResult,
    BookingStatus,
    ConversationMessage,
    ConversationSession,
    ConversationStage,
    MessageRole,
    NextAction,
    SalesAgentOutput,
)
from app.services.guardrails import GuardrailKind, inspect_message
from app.services.llm import LLMService, OpenAIResponsesService
from app.services.prompts import PromptLoader
from app.services.sales_rules import message_offers_booking
from app.services.scheduling import (
    MEETING_EXPECTED_DURATION_MINUTES,
    SchedulingError,
    SchedulingService,
    SlotUnavailableError,
    build_scheduling_service,
)


logger = logging.getLogger(__name__)


class ConversationNotFoundError(LookupError):
    """Raised when a requested persisted conversation does not exist."""


class PersistenceStateError(RuntimeError):
    """Raised when relational history and the validated snapshot disagree."""


@dataclass(frozen=True)
class ConversationView:
    conversation: Conversation
    lead: Lead
    messages: tuple[Message, ...]
    session: ConversationSession


@dataclass(frozen=True)
class SalesTurnResult:
    output: SalesAgentOutput
    view: ConversationView


class SalesService:
    """Use the same Sales Agent while owning all persistence concerns."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        agent: SalesAgent,
        prompt_loader: PromptLoader | None = None,
        lead_repository: LeadRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        message_repository: MessageRepository | None = None,
        booking_repository: BookingRepository | None = None,
        scheduler: SchedulingService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent
        self._prompt_loader = prompt_loader or PromptLoader()
        self._leads = lead_repository or LeadRepository()
        self._conversations = conversation_repository or ConversationRepository()
        self._messages = message_repository or MessageRepository()
        self._bookings = booking_repository or BookingRepository()
        self._scheduler = scheduler or build_scheduling_service()

    def start_conversation(
        self,
        *,
        name: str,
        email: str | None = None,
        phone_number: str | None = None,
        prompt_version: str = "sales_v1",
        channel: str = "streamlit",
        external_conversation_id: str | None = None,
    ) -> ConversationView:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")
        self._prompt_loader.load(prompt_version)
        session = ConversationSession(prompt_version=prompt_version)
        with self._session_factory.begin() as db:
            lead = self._leads.create(
                db,
                name=clean_name,
                email=self._clean_optional(email),
                phone_number=self._clean_optional(phone_number),
                channel=channel,
            )
            conversation = self._conversations.create(
                db,
                lead_id=lead.id,
                channel=channel,
                prompt_version=prompt_version,
                session=session,
                external_conversation_id=external_conversation_id,
            )
            conversation_id = conversation.id
        return self.get_conversation(conversation_id)

    def handle_message(
        self, conversation_id: uuid.UUID | str, content: str
    ) -> SalesTurnResult:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("message content is required")
        resolved_id = self._as_uuid(conversation_id)
        view = self.get_conversation(resolved_id)
        previous_stage = view.session.stage
        previous_snapshot = view.session.model_dump(mode="json")
        previous_message_count = len(previous_snapshot["messages"])

        booking_result: BookingResult | None = None
        turn_id = uuid.uuid4().hex[:12]
        booking_turn = self._handle_closed_reengagement(
            view.session,
            view.messages,
            clean_content,
            turn_id=turn_id,
            conversation_id=str(resolved_id),
        )
        if booking_turn is None:
            booking_turn = self._handle_booking_workflow(
                view.session,
                clean_content,
                view.lead.name or "Lead",
                view.lead.email,
            )
        if booking_turn is None:
            output = self._agent.handle_message(
                view.session,
                clean_content,
                turn_id=turn_id,
                conversation_id=str(resolved_id),
            )
        else:
            output, booking_result = booking_turn

        with self._session_factory.begin() as db:
            conversation = self._conversations.get(db, resolved_id)
            if conversation is None:
                raise ConversationNotFoundError(str(resolved_id))
            lead = self._leads.get(db, conversation.lead_id)
            if lead is None:
                raise PersistenceStateError("conversation references a missing lead")

            current_snapshot = ConversationSession.model_validate(
                conversation.session_snapshot
            )
            if current_snapshot.model_dump(mode="json") != previous_snapshot:
                raise PersistenceStateError(
                    "conversation changed while the sales turn was generated"
                )

            inbound, outbound = view.session.messages[-2:]
            self._messages.add(
                db,
                conversation_id=resolved_id,
                position=previous_message_count,
                role=MessageRole.USER,
                content=inbound.content,
                stage=previous_stage,
                channel=conversation.channel,
            )
            self._messages.add(
                db,
                conversation_id=resolved_id,
                position=previous_message_count + 1,
                role=MessageRole.ASSISTANT,
                content=outbound.content,
                stage=output.stage,
                channel=conversation.channel,
                delivery_status="generated",
            )
            self._conversations.save_session(conversation, view.session)
            self._leads.update_qualification(
                lead,
                service_interest=output.service.value if output.service else None,
                qualification_status=output.fit.value,
                lead_score=output.qualification_score,
            )
            if booking_result is not None:
                operation_id = view.session.booking.operation_id
                if operation_id is None:
                    raise PersistenceStateError("booked session has no operation ID")
                self._bookings.save_confirmed(
                    db,
                    conversation_id=resolved_id,
                    lead_id=lead.id,
                    provider=self._scheduler.name,
                    operation_id=operation_id,
                    result=booking_result,
                )
                lead.meeting_booked = True
                lead.meeting_datetime = booking_result.slot.starts_at

        return SalesTurnResult(
            output=output, view=self.get_conversation(resolved_id)
        )

    def _handle_closed_reengagement(
        self,
        session: ConversationSession,
        messages: tuple[Message, ...],
        content: str,
        *,
        turn_id: str,
        conversation_id: str,
    ) -> tuple[SalesAgentOutput, BookingResult | None] | None:
        if session.stage is not ConversationStage.CLOSED:
            return None

        decision = inspect_message(content)
        if decision is not None and decision.kind in {
            GuardrailKind.OUT_OF_SCOPE,
            GuardrailKind.SENSITIVE_CREDENTIALS,
        }:
            return None

        state = session.booking
        normalized = _normalize(content)
        booking_intent = _requests_booking(normalized)
        if state.status is BookingStatus.BOOKED:
            session.stage = ConversationStage.BOOKED
            logger.info(
                "sales_conversation_reopened turn_id=%s conversation_id=%s "
                "reason=existing_booking restored_stage=%s",
                turn_id,
                conversation_id,
                session.stage.value,
            )
            label = (
                _format_slot(state.selected_slot)
                if state.selected_slot is not None
                else "o horário já confirmado"
            )
            return (
                self._booking_output(
                    session,
                    content,
                    f"Você já tem uma reunião confirmada para {label}. "
                    "Não vou criar um segundo agendamento.",
                    NextAction.BOOK_MEETING,
                    stage=ConversationStage.BOOKED,
                ),
                None,
            )

        prior_offer = state.status in {
            BookingStatus.OFFERED,
            BookingStatus.SLOTS_PRESENTED,
            BookingStatus.AWAITING_CONFIRMATION,
            BookingStatus.DEFERRED,
        } or any(
            message.role == MessageRole.ASSISTANT.value
            and message_offers_booking(message.content)
            for message in messages
        )
        if booking_intent and prior_offer:
            state.status = BookingStatus.OFFERED
            state.offered_slots = []
            state.selected_slot = None
            state.operation_id = None
            state.provider_event_id = None
            state.meeting_url = None
            if session.last_output is not None:
                session.last_output = session.last_output.model_copy(
                    update={"objection": None}
                )
            session.stage = ConversationStage.BOOKING
            logger.info(
                "sales_conversation_reopened turn_id=%s conversation_id=%s "
                "reason=booking_intent restored_stage=%s",
                turn_id,
                conversation_id,
                session.stage.value,
            )
            return self._present_slots(session, content), None

        restored_stage = _last_resumable_stage(messages)
        session.stage = restored_stage
        logger.info(
            "sales_conversation_reopened turn_id=%s conversation_id=%s "
            "reason=relevant_message restored_stage=%s",
            turn_id,
            conversation_id,
            restored_stage.value,
        )
        return None

    def _handle_booking_workflow(
        self,
        session: ConversationSession,
        content: str,
        lead_name: str,
        lead_email: str | None,
    ) -> tuple[SalesAgentOutput, BookingResult | None] | None:
        state = session.booking
        normalized = _normalize(content)

        if _requests_meeting_information(normalized) and (
            session.stage in {ConversationStage.BOOKING, ConversationStage.OBJECTION}
            or state.status is not BookingStatus.NOT_OFFERED
        ):
            message = (
                f"A conversa costuma durar cerca de "
                f"{MEETING_EXPECTED_DURATION_MINUTES} minutos. O especialista vai "
                "entender seu contexto, explicar o serviço mais relevante, responder "
                "às suas perguntas e alinhar possíveis próximos passos."
            )
            next_action = NextAction.CONTINUE_DISCOVERY
            if state.status is BookingStatus.SLOTS_PRESENTED:
                message += " Alguma das opções que enviei funciona para você?"
                next_action = NextAction.PRESENT_SLOTS
            elif (
                state.status is BookingStatus.AWAITING_CONFIRMATION
                and state.selected_slot is not None
            ):
                message += (
                    f" Posso confirmar o horário de "
                    f"{_format_slot(state.selected_slot)}?"
                )
                next_action = NextAction.CONFIRM_BOOKING
            return (
                self._booking_output(
                    session,
                    content,
                    message,
                    next_action,
                    clear_objection=True,
                ),
                None,
            )

        wants_slots = _is_affirmative(normalized) or bool(
            re.search(
                r"\b(?:quero|gostaria de|pode|vamos|vamos ver).*"
                r"(?:horarios?|agenda|agendar|marcar)\b",
                normalized,
            )
        ) or _requests_availability(normalized)
        if (
            "feriado" in normalized
            and state.status
            in {BookingStatus.SLOTS_PRESENTED, BookingStatus.AWAITING_CONFIRMATION}
        ):
            excluded_dates = {
                slot.starts_at.astimezone(ZoneInfo("America/Recife")).date()
                for slot in state.offered_slots
            }
            state.selected_slot = None
            state.operation_id = None
            return self._present_slots(
                session,
                content,
                preference=AvailabilityPreference(excluded_dates=excluded_dates),
                prefix="Você tem razão — não devemos oferecer essa data. ",
            ), None

        availability_intent = _parse_availability_preference(
            normalized, state.offered_slots
        )
        if state.status in {BookingStatus.OFFERED, BookingStatus.DEFERRED}:
            if _is_negative(normalized):
                state.status = BookingStatus.DEFERRED
                return self._booking_output(
                    session,
                    content,
                    "Sem problema. Podemos retomar o agendamento quando fizer sentido para você.",
                    NextAction.CONTINUE_DISCOVERY,
                ), None
            if availability_intent is not None:
                preference, label = availability_intent
                return self._present_slots(
                    session, content, preference=preference, requested_label=label
                ), None
            if wants_slots:
                return self._present_slots(session, content), None

        if state.status is BookingStatus.SLOTS_PRESENTED:
            if availability_intent is not None:
                preference, label = availability_intent
                return self._present_slots(
                    session, content, preference=preference, requested_label=label
                ), None
            if re.search(r"\b(?:mais|outros?) horarios?\b", normalized):
                after = state.offered_slots[-1].ends_at if state.offered_slots else None
                return self._present_slots(session, content, after=after), None
            if _requests_availability(normalized):
                return self._present_slots(session, content), None
            selected = _selected_option(normalized, len(state.offered_slots))
            if selected is not None:
                state.selected_slot = state.offered_slots[selected]
                state.operation_id = state.operation_id or str(uuid.uuid4())
                state.status = BookingStatus.AWAITING_CONFIRMATION
                label = _format_slot(state.selected_slot)
                return self._booking_output(
                    session,
                    content,
                    f"Você escolheu {label}. Posso confirmar esse agendamento?",
                    NextAction.CONFIRM_BOOKING,
                ), None

        if state.status is BookingStatus.AWAITING_CONFIRMATION:
            if availability_intent is not None:
                state.selected_slot = None
                state.operation_id = None
                preference, label = availability_intent
                return self._present_slots(
                    session, content, preference=preference, requested_label=label
                ), None
            if _is_negative(normalized):
                state.status = BookingStatus.SLOTS_PRESENTED
                state.selected_slot = None
                return self._booking_output(
                    session,
                    content,
                    "Tudo bem. Escolha outra opção de 1 a 3 ou peça mais horários.",
                    NextAction.PRESENT_SLOTS,
                ), None
            if _is_affirmative(normalized) and state.selected_slot and state.operation_id:
                try:
                    result = self._scheduler.book_meeting(
                        BookingRequest(
                            operation_id=state.operation_id,
                            slot=state.selected_slot,
                            lead_name=lead_name,
                            lead_email=lead_email,
                        )
                    )
                except SlotUnavailableError:
                    state.selected_slot = None
                    state.operation_id = None
                    return self._present_slots(
                        session,
                        content,
                        prefix="Esse horário não está mais disponível. ",
                    ), None
                except SchedulingError:
                    return self._booking_output(
                        session,
                        content,
                        "Não consegui confirmar o agendamento agora e nenhuma reunião foi marcada. Podemos tentar novamente?",
                        NextAction.CONFIRM_BOOKING,
                    ), None
                state.status = BookingStatus.BOOKED
                state.provider_event_id = result.provider_event_id
                state.meeting_url = result.meeting_url
                label = _format_slot(result.slot)
                return self._booking_output(
                    session,
                    content,
                    f"Reunião confirmada para {label}. Link: {result.meeting_url}",
                    NextAction.BOOK_MEETING,
                    stage=ConversationStage.BOOKED,
                ), result
        return None

    def _present_slots(
        self,
        session: ConversationSession,
        content: str,
        *,
        after: datetime | None = None,
        prefix: str = "",
        preference: AvailabilityPreference | None = None,
        requested_label: str | None = None,
    ) -> SalesAgentOutput:
        try:
            slots = self._scheduler.get_available_slots(
                after=after, limit=3, preference=preference
            )
        except SchedulingError:
            return self._booking_output(
                session,
                content,
                "Não consegui consultar horários agora. Podemos tentar novamente em instantes?",
                NextAction.PRESENT_SLOTS,
            )
        if not slots and preference is not None:
            alternative_after = after
            boundary = preference.end_date or preference.start_date
            if boundary is not None:
                alternative_after = datetime.combine(
                    boundary, time.max, ZoneInfo("America/Recife")
                )
            try:
                slots = self._scheduler.get_available_slots(
                    after=alternative_after, limit=3
                )
            except SchedulingError:
                slots = []
            if slots:
                label = requested_label or "nesse período"
                prefix = (
                    f"Não encontrei horários disponíveis {label}. "
                    "Estas são as opções mais próximas: "
                )
        if not slots:
            return self._booking_output(
                session,
                content,
                "Não consegui consultar horários agora. Podemos tentar novamente em instantes?",
                NextAction.PRESENT_SLOTS,
            )
        if requested_label and not prefix:
            prefix = f"Considerando sua preferência {requested_label}, "
        session.booking.offered_slots = slots
        session.booking.selected_slot = None
        session.booking.status = BookingStatus.SLOTS_PRESENTED
        options = "\n".join(
            f"{index}. {_format_slot(slot)}" for index, slot in enumerate(slots, 1)
        )
        return self._booking_output(
            session,
            content,
            f"{prefix}Encontrei estas opções:\n{options}\nQual opção você prefere?",
            NextAction.PRESENT_SLOTS,
        )

    @staticmethod
    def _booking_output(
        session: ConversationSession,
        content: str,
        message: str,
        next_action: NextAction,
        *,
        stage: ConversationStage = ConversationStage.BOOKING,
        clear_objection: bool = False,
    ) -> SalesAgentOutput:
        previous = session.last_output
        if previous is None:
            raise PersistenceStateError("booking flow requires prior sales state")
        updates = {
            "message": message,
            "stage": stage,
            "should_offer_booking": False,
            "next_action": next_action,
        }
        if clear_objection:
            updates["objection"] = None
        output = previous.model_copy(update=updates)
        session.messages.extend(
            [
                ConversationMessage(role=MessageRole.USER, content=content),
                ConversationMessage(role=MessageRole.ASSISTANT, content=message),
            ]
        )
        session.stage = stage
        session.last_output = output
        return output

    def get_conversation(
        self, conversation_id: uuid.UUID | str
    ) -> ConversationView:
        resolved_id = self._as_uuid(conversation_id)
        with self._session_factory() as db:
            conversation = self._conversations.get(db, resolved_id)
            if conversation is None:
                raise ConversationNotFoundError(str(resolved_id))
            lead = self._leads.get(db, conversation.lead_id)
            if lead is None:
                raise PersistenceStateError("conversation references a missing lead")
            messages = self._messages.list_for_conversation(db, resolved_id)
            try:
                session = ConversationSession.model_validate(
                    conversation.session_snapshot
                )
            except ValidationError as exc:
                raise PersistenceStateError(
                    "conversation snapshot is invalid"
                ) from exc
            if conversation.prompt_version != session.prompt_version:
                raise PersistenceStateError("stored prompt versions disagree")
            if conversation.current_stage != session.stage.value:
                raise PersistenceStateError("stored conversation stages disagree")
            if len(messages) != len(session.messages):
                raise PersistenceStateError(
                    "message history and session snapshot disagree"
                )
            db.expunge(conversation)
            db.expunge(lead)
            for message in messages:
                db.expunge(message)
        return ConversationView(
            conversation=conversation,
            lead=lead,
            messages=tuple(messages),
            session=session,
        )

    def list_leads(self) -> list[Lead]:
        with self._session_factory() as db:
            records = self._leads.list(db)
            for record in records:
                db.expunge(record)
            return records

    def list_conversations(self) -> list[Conversation]:
        with self._session_factory() as db:
            records = self._conversations.list(db)
            for record in records:
                db.expunge(record)
            return records

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ConversationNotFoundError(str(value)) from exc


def build_sales_service(
    session_factory: Callable[[], Session],
    *,
    llm: LLMService | None = None,
    scheduler: SchedulingService | None = None,
) -> SalesService:
    """Build the production service while allowing deterministic test clients."""

    resolved_llm = llm or OpenAIResponsesService()
    return SalesService(
        session_factory=session_factory,
        agent=SalesAgent(resolved_llm),
        scheduler=scheduler,
    )


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _is_affirmative(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:sim(?:,? por favor)?|claro|quero(?: sim)?|gostaria|por favor|"
            r"pode(?: sim| ser)?|seria otimo|otimo|"
            r"perfeito|confirmo|"
            r"confirmado|correto|vamos)\s*[.!]?\s*",
            value,
        )
    )


def _requests_availability(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:disponibilidade|quando (?:seria|tem|poderia)|"
            r"quais? horarios?|que horarios?|horarios? disponiveis)\b",
            value,
        )
    )


def _requests_booking(value: str) -> bool:
    changed_mind = bool(re.fullmatch(r"\s*mudei de ideia\s*[.!]?\s*", value))
    return changed_mind or _is_affirmative(value) or bool(
        re.search(
            r"\b(?:mudei de ideia|quero|gostaria de|vamos|pode).*(?:agendar|marcar|"
            r"horarios?|agenda|conversa|reuniao)\b",
            value,
        )
    ) or _requests_availability(value)


def _requests_meeting_information(value: str) -> bool:
    subject = bool(
        re.search(r"\b(?:reuniao|conversa|encontro|agendamento|chamada|call)\b", value)
    )
    duration = bool(
        re.search(
            r"\b(?:quanto tempo|qual (?:e )?a duracao|duracao|quanto dura|demora)\b",
            value,
        )
    )
    process = bool(
        re.search(r"\b(?:como funciona|como (?:e|sera)|o que acontece)\b", value)
    )
    return subject and (duration or process)


def _last_resumable_stage(messages: tuple[Message, ...]) -> ConversationStage:
    resumable = {
        ConversationStage.OPENING,
        ConversationStage.DISCOVERY,
        ConversationStage.QUALIFICATION,
        ConversationStage.OBJECTION,
        ConversationStage.BOOKING,
    }
    for message in reversed(messages):
        try:
            stage = ConversationStage(message.stage)
        except ValueError:
            continue
        if stage in resumable:
            return stage
        if stage is ConversationStage.NO_FIT:
            return ConversationStage.DISCOVERY
    return ConversationStage.OPENING


def _parse_availability_preference(
    value: str, offered_slots: list
) -> tuple[AvailabilityPreference, str] | None:
    weekday_names = {
        "segunda": 0,
        "terca": 1,
        "quarta": 2,
        "quinta": 3,
        "sexta": 4,
    }
    weekday_match = re.search(
        r"\b(segunda|terca|quarta|quinta|sexta)(?:-feira)?\b", value
    )
    numeric_date = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", value)
    morning = bool(re.search(r"\b(?:de manha|manha)\b", value))
    afternoon = bool(re.search(r"\b(?:a tarde|de tarde|tarde)\b", value))
    after_hour = re.search(r"\b(?:depois|a partir) das? (\d{1,2})(?::(\d{2}))?\b", value)
    if not any((weekday_match, numeric_date, morning, afternoon, after_hour)):
        return None

    reference = (
        min(slot.starts_at for slot in offered_slots)
        .astimezone(ZoneInfo("America/Recife"))
        .date()
        if offered_slots
        else datetime.now(ZoneInfo("America/Recife")).date()
    )
    target_date: date | None = None
    if numeric_date:
        year = int(numeric_date.group(3) or reference.year)
        try:
            target_date = date(year, int(numeric_date.group(2)), int(numeric_date.group(1)))
        except ValueError:
            return None
        if not numeric_date.group(3) and target_date < reference:
            target_date = target_date.replace(year=year + 1)
    elif weekday_match:
        weekday = weekday_names[weekday_match.group(1)]
        target_date = reference + timedelta(days=(weekday - reference.weekday()) % 7)

    earliest_time = None
    latest_time = None
    period_label = ""
    if morning:
        earliest_time, latest_time, period_label = time(9), time(12), " pela manhã"
    elif afternoon:
        earliest_time, latest_time, period_label = time(13), time(18), " à tarde"
    if after_hour:
        hour = int(after_hour.group(1))
        minute = int(after_hour.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        earliest_time = time(hour, minute)

    preference = AvailabilityPreference(
        start_date=target_date,
        end_date=target_date,
        earliest_time=earliest_time,
        latest_time=latest_time,
    )
    if weekday_match:
        label = f"na {weekday_match.group(1)}-feira{period_label}"
    elif target_date:
        label = f"em {target_date:%d/%m}{period_label}"
    else:
        label = period_label.strip() or "nesse período"
    return preference, label


def _is_negative(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:nao(?:,? obrigad[oa])?|agora nao|depois|deixa para depois|"
            r"prefiro nao)\s*[.!]?\s*",
            value,
        )
    )


def _selected_option(value: str, count: int) -> int | None:
    words = {"primeira": 0, "primeiro": 0, "segunda": 1, "segundo": 1, "terceira": 2, "terceiro": 2}
    word_match = re.fullmatch(
        r"\s*(?:a\s+|o\s+|opcao\s+)?"
        r"(primeir[oa]|segund[oa]|terceir[oa])(?:\s+opcao)?\s*[.!]?\s*",
        value,
    )
    if word_match is not None:
        index = words[word_match.group(1)]
        return index if index < count else None
    match = re.fullmatch(r"\s*(?:opcao\s*)?([1-9])\s*[.!]?\s*", value)
    if match is None:
        return None
    index = int(match.group(1)) - 1
    return index if 0 <= index < count else None


def _format_slot(slot) -> str:
    return slot.starts_at.astimezone(ZoneInfo("America/Recife")).strftime(
        "%d/%m/%Y às %H:%M"
    )
