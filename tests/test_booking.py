"""Deterministic Phase 4 booking tests."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, time, timezone
from typing import Any, Mapping

import pytest
from sqlalchemy import select

from app.agents import SalesAgent
from app.db.base import Base
from app.db.models import Booking, Conversation
from app.db.session import create_session_factory
from app.schemas import (
    AvailabilityPreference,
    BookingRequest,
    BookingStatus,
    ConversationSession,
    ConversationStage,
)
from app.services.llm import LLMRequest
from app.services.sales import SalesService
from app.services.scheduling import (
    DeterministicSchedulingService,
    GoogleCalendarSchedulingService,
    SchedulingError,
    _load_json_setting,
)


def assessment(level: str) -> dict[str, str | None]:
    return {"level": level, "evidence": None if level == "none" else "Explicit."}


def qualified_draft(message: str, *, booking: bool) -> dict[str, Any]:
    return {
        "message": message,
        "proposed_stage": "BOOKING",
        "primary_pain": "lack_of_strategy",
        "routing_signals": {
            "planning_need": True,
            "investment_need": True,
            "out_of_scope_only": False,
        },
        "qualification": {
            "need": assessment("strong"),
            "financial_complexity": assessment("strong"),
            "readiness": assessment("strong"),
            "urgency": assessment("strong"),
            "service_fit": assessment("strong"),
        },
        "objection": None,
        "should_offer_booking": booking,
        "request_scope": "in_scope",
    }


def discovery_draft() -> dict[str, Any]:
    payload = qualified_draft("O que você gostaria de organizar primeiro?", booking=False)
    payload["proposed_stage"] = "DISCOVERY"
    payload["qualification"] = {
        key: assessment("moderate") for key in payload["qualification"]
    }
    payload["qualification"]["readiness"] = assessment("weak")
    return payload


class QueueLLM:
    def __init__(self, *responses: Mapping[str, Any]) -> None:
        self.responses = deque(responses)

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        return self.responses.popleft()


@pytest.fixture
def session_factory(tmp_path):
    factory = create_session_factory(f"sqlite+pysqlite:///{tmp_path / 'booking.db'}")
    Base.metadata.create_all(factory.kw["bind"])
    return factory


def fixed_scheduler() -> DeterministicSchedulingService:
    return DeterministicSchedulingService(
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    )


class RecordingScheduler(DeterministicSchedulingService):
    last_request: BookingRequest | None = None

    def book_meeting(self, request: BookingRequest):
        self.last_request = request
        return super().book_meeting(request)


def make_service(session_factory, llm, scheduler=None) -> SalesService:
    return SalesService(
        session_factory=session_factory,
        agent=SalesAgent(llm),
        scheduler=scheduler or fixed_scheduler(),
    )


def force_closed(session_factory, conversation_id, *, reset_booking=False) -> None:
    with session_factory.begin() as db:
        conversation = db.get(Conversation, conversation_id)
        assert conversation is not None
        session = ConversationSession.model_validate(conversation.session_snapshot)
        session.stage = ConversationStage.CLOSED
        if reset_booking:
            session.booking.status = BookingStatus.NOT_OFFERED
            session.booking.offered_slots = []
            session.booking.selected_slot = None
            session.booking.operation_id = None
        conversation.current_stage = ConversationStage.CLOSED.value
        conversation.status = "closed"
        conversation.session_snapshot = session.model_dump(mode="json")


def test_mock_slots_follow_business_policy_and_skip_busy_time() -> None:
    scheduler = fixed_scheduler()
    slots = scheduler.get_available_slots()
    assert len(slots) == 3
    assert [slot.starts_at.date() for slot in slots] == [date(2026, 9, 8)] * 3
    assert [slot.starts_at.hour for slot in slots] == [9, 10, 11]
    assert all((slot.ends_at - slot.starts_at).seconds == 3600 for slot in slots)

    blocked = DeterministicSchedulingService(
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        busy=[(slots[0].starts_at, slots[0].ends_at)],
    )
    assert blocked.get_available_slots()[0].starts_at.hour == 10


def test_mock_slots_honor_weekday_period_and_custom_blackout_dates() -> None:
    scheduler = DeterministicSchedulingService(
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
        blackout_dates={date(2026, 9, 8)},
    )
    slots = scheduler.get_available_slots(
        preference=AvailabilityPreference(
            start_date=date(2026, 9, 9),
            end_date=date(2026, 9, 9),
            earliest_time=time(13),
            latest_time=time(18),
        )
    )
    assert [slot.starts_at.date() for slot in slots] == [date(2026, 9, 9)] * 3
    assert [slot.starts_at.hour for slot in slots] == [13, 14, 15]


@pytest.mark.parametrize(
    "acceptance",
    ["Quero", "Seria ótimo", "Ok, quando seria?", "Qual é a disponibilidade?"],
)
def test_booking_acceptance_and_availability_questions_present_slots(
    session_factory, acceptance
) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft("Quer que eu veja alguns horários disponíveis?", booking=True),
        ),
    )
    started = service.start_conversation(name="Rafael")
    service.handle_message(started.conversation.id, "Quero me organizar.")
    service.handle_message(started.conversation.id, "Quero avançar.")

    result = service.handle_message(started.conversation.id, acceptance)

    assert result.view.session.booking.status is BookingStatus.SLOTS_PRESENTED
    assert "1. 08/09/2026" in result.output.message


def test_booking_understands_wednesday_afternoon_without_calling_llm(
    session_factory,
) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft("Quer que eu veja alguns horários disponíveis?", booking=True),
        ),
    )
    started = service.start_conversation(name="Rafael")
    service.handle_message(started.conversation.id, "Quero me organizar.")
    service.handle_message(started.conversation.id, "Quero avançar.")
    service.handle_message(started.conversation.id, "Seria ótimo")

    result = service.handle_message(
        started.conversation.id, "Quarta-feira à tarde seria melhor"
    )

    slots = result.view.session.booking.offered_slots
    assert [slot.starts_at.date() for slot in slots] == [date(2026, 9, 9)] * 3
    assert [slot.starts_at.hour for slot in slots] == [13, 14, 15]
    assert "quarta-feira à tarde" in result.output.message


def test_booking_cta_is_not_repeated_while_answering_question(session_factory) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft(
                "Quer que eu veja alguns horários disponíveis?", booking=True
            ),
        ),
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    offered = service.handle_message(started.conversation.id, "Estou pronto para avançar.")
    assert offered.view.session.booking.status is BookingStatus.OFFERED

    answered = service.handle_message(
        started.conversation.id, "Como funciona o agendamento e quanto tempo dura?"
    )
    assert answered.output.stage is ConversationStage.BOOKING
    assert "cerca de 45 minutos" in answered.output.message
    assert "horários disponíveis" not in answered.output.message
    assert answered.view.session.booking.status is BookingStatus.OFFERED


def test_meeting_duration_preserves_presented_slots_and_clears_time_objection(
    session_factory,
) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft(
                "Quer que eu veja alguns horários disponíveis?", booking=True
            ),
        ),
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    service.handle_message(started.conversation.id, "Estou pronto para avançar.")
    slots = service.handle_message(started.conversation.id, "sim")
    offered_slots = slots.view.session.booking.offered_slots

    answered = service.handle_message(
        started.conversation.id,
        "Quanto tempo dura a reunião? Pois não tenho muito tempo",
    )

    assert answered.output.stage is ConversationStage.BOOKING
    assert answered.output.objection is None
    assert "cerca de 45 minutos" in answered.output.message
    assert "opções que enviei" in answered.output.message
    assert answered.view.session.booking.status is BookingStatus.SLOTS_PRESENTED
    assert answered.view.session.booking.offered_slots == offered_slots


def test_meeting_information_preserves_pending_confirmation(session_factory) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft(
                "Quer que eu veja alguns horários disponíveis?", booking=True
            ),
        ),
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    service.handle_message(started.conversation.id, "Estou pronto para avançar.")
    service.handle_message(started.conversation.id, "sim")
    pending = service.handle_message(started.conversation.id, "1")
    selected = pending.view.session.booking.selected_slot

    answered = service.handle_message(
        started.conversation.id, "Como funciona essa reunião?"
    )

    assert answered.view.session.booking.status is BookingStatus.AWAITING_CONFIRMATION
    assert answered.view.session.booking.selected_slot == selected
    assert "Posso confirmar o horário" in answered.output.message


def test_closed_booking_reengagement_uses_transcript_and_fetches_fresh_slots(
    session_factory,
) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft(
                "Quer que eu veja alguns horários disponíveis?", booking=True
            ),
        ),
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    qualified = service.handle_message(
        started.conversation.id, "Estou pronto para avançar."
    )
    qualification = qualified.output.qualification
    force_closed(session_factory, started.conversation.id, reset_booking=True)

    reopened = service.handle_message(started.conversation.id, "Mudei de ideia")

    assert reopened.output.stage is ConversationStage.BOOKING
    assert reopened.view.conversation.status == "active"
    assert reopened.view.session.booking.status is BookingStatus.SLOTS_PRESENTED
    assert len(reopened.view.session.booking.offered_slots) == 3
    assert reopened.output.qualification == qualification


def test_relevant_message_resumes_last_nonterminal_stage(session_factory) -> None:
    service = make_service(
        session_factory, QueueLLM(discovery_draft(), discovery_draft())
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    force_closed(session_factory, started.conversation.id)

    reopened = service.handle_message(
        started.conversation.id, "Quero retomar minha organização financeira."
    )

    assert reopened.output.stage is ConversationStage.DISCOVERY
    assert reopened.view.conversation.status == "active"


def test_closed_lead_without_prior_offer_cannot_bypass_qualification(
    session_factory,
) -> None:
    service = make_service(
        session_factory, QueueLLM(discovery_draft(), discovery_draft())
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    force_closed(session_factory, started.conversation.id)

    reopened = service.handle_message(
        started.conversation.id, "Mudei de ideia, quero marcar uma reunião."
    )

    assert reopened.output.stage is ConversationStage.DISCOVERY
    assert reopened.view.session.booking.status is BookingStatus.NOT_OFFERED
    assert reopened.view.session.booking.offered_slots == []


def test_out_of_scope_message_does_not_reopen_closed_conversation(
    session_factory,
) -> None:
    service = make_service(session_factory, QueueLLM(discovery_draft()))
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    force_closed(session_factory, started.conversation.id)

    result = service.handle_message(
        started.conversation.id, "Faça um script Python para somar dois números."
    )

    assert result.output.stage is ConversationStage.CLOSED
    assert result.view.conversation.status == "closed"


def test_complete_booking_requires_selection_and_confirmation(session_factory) -> None:
    scheduler = RecordingScheduler(
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    )
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft("Quer que eu veja alguns horários disponíveis?", booking=True)
        ),
        scheduler=scheduler,
    )
    started = service.start_conversation(
        name="Rafael", email="rafael@example.com"
    )
    service.handle_message(started.conversation.id, "Quero organizar tudo.")
    service.handle_message(started.conversation.id, "Estou pronto para avançar.")
    slots = service.handle_message(started.conversation.id, "sim, por favor")
    assert slots.view.session.booking.status is BookingStatus.SLOTS_PRESENTED
    assert len(slots.view.session.booking.offered_slots) == 3
    assert not slots.view.lead.meeting_booked

    pending = service.handle_message(started.conversation.id, "a primeira opção")
    assert pending.view.session.booking.status is BookingStatus.AWAITING_CONFIRMATION
    assert not pending.view.lead.meeting_booked

    booked = service.handle_message(started.conversation.id, "confirmo")
    assert booked.output.stage is ConversationStage.BOOKED
    assert booked.view.session.booking.status is BookingStatus.BOOKED
    assert booked.view.lead.meeting_booked
    assert booked.view.lead.meeting_datetime is not None
    assert booked.view.session.booking.meeting_url in booked.output.message
    assert scheduler.last_request is not None
    assert scheduler.last_request.lead_email == "rafael@example.com"
    with session_factory() as db:
        record = db.scalar(select(Booking))
        assert record is not None
        assert record.status == "confirmed"
    restarted = make_service(session_factory, QueueLLM())
    restored = restarted.get_conversation(started.conversation.id)
    assert restored.session.booking.status is BookingStatus.BOOKED
    assert restored.session.booking.meeting_url == booked.view.session.booking.meeting_url


def test_closed_booked_conversation_does_not_create_duplicate_booking(
    session_factory,
) -> None:
    scheduler = RecordingScheduler(
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    )
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft(
                "Quer que eu veja alguns horários disponíveis?", booking=True
            ),
        ),
        scheduler=scheduler,
    )
    started = service.start_conversation(name="Mariana")
    service.handle_message(started.conversation.id, "Quero ajuda.")
    service.handle_message(started.conversation.id, "Estou pronta para avançar.")
    service.handle_message(started.conversation.id, "sim")
    service.handle_message(started.conversation.id, "1")
    service.handle_message(started.conversation.id, "confirmo")
    force_closed(session_factory, started.conversation.id)

    reopened = service.handle_message(
        started.conversation.id, "Quero marcar outra reunião."
    )

    assert reopened.output.stage is ConversationStage.BOOKED
    assert "já tem uma reunião confirmada" in reopened.output.message
    with session_factory() as db:
        assert len(list(db.scalars(select(Booking)))) == 1


def test_mock_booking_is_idempotent() -> None:
    scheduler = fixed_scheduler()
    slot = scheduler.get_available_slots()[0]
    request = BookingRequest(
        operation_id="9e926315-688f-46e4-b958-360c942e9468",
        slot=slot,
        lead_name="Ana",
    )
    assert scheduler.book_meeting(request) == scheduler.book_meeting(request)


class FailingScheduler(DeterministicSchedulingService):
    def book_meeting(self, request: BookingRequest):
        raise SchedulingError("simulated")


def test_provider_failure_never_claims_booking(session_factory) -> None:
    service = make_service(
        session_factory,
        QueueLLM(
            discovery_draft(),
            qualified_draft("Quer que eu veja alguns horários disponíveis?", booking=True)
        ),
        scheduler=FailingScheduler(
            now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
        ),
    )
    started = service.start_conversation(name="Ana")
    service.handle_message(started.conversation.id, "Preciso de ajuda.")
    service.handle_message(started.conversation.id, "Estou pronta para avançar.")
    service.handle_message(started.conversation.id, "sim")
    service.handle_message(started.conversation.id, "1")
    failed = service.handle_message(started.conversation.id, "sim")
    assert failed.output.stage is ConversationStage.BOOKING
    assert "nenhuma reunião foi marcada" in failed.output.message
    assert not failed.view.lead.meeting_booked


class Executable:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeNotFound(Exception):
    status_code = 404


class FakeGoogleClient:
    def __init__(self) -> None:
        self.insert_kwargs = None
        self.event = None

    def freebusy(self):
        return self

    def query(self, *, body):
        return Executable({"calendars": {"advisor@example.com": {"busy": []}}})

    def events(self):
        return self

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        self.event = {
            "id": kwargs["body"]["id"],
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        }
        return Executable(self.event)

    def get(self, **kwargs):
        if self.event is None:
            raise FakeNotFound()
        return Executable(self.event)


def test_google_adapter_creates_idempotent_event_and_meet() -> None:
    client = FakeGoogleClient()
    scheduler = GoogleCalendarSchedulingService(
        calendar_id="advisor@example.com",
        client=client,
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    slot = scheduler.get_available_slots()[0]
    request = BookingRequest(
        operation_id="9e926315-688f-46e4-b958-360c942e9468",
        slot=slot,
        lead_name="Mariana",
        lead_email="mariana@example.com",
    )
    result = scheduler.book_meeting(request)
    assert result.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert client.insert_kwargs["conferenceDataVersion"] == 1
    assert client.insert_kwargs["sendUpdates"] == "all"
    assert client.insert_kwargs["body"]["attendees"] == [
        {"email": "mariana@example.com"}
    ]
    first_insert = client.insert_kwargs
    assert scheduler.book_meeting(request) == result
    assert client.insert_kwargs is first_insert


def test_google_adapter_books_without_invitation_when_email_is_missing() -> None:
    client = FakeGoogleClient()
    scheduler = GoogleCalendarSchedulingService(
        calendar_id="advisor@example.com",
        client=client,
        now=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    slot = scheduler.get_available_slots()[0]
    scheduler.book_meeting(
        BookingRequest(
            operation_id="b37d48d4-e829-438b-af81-e0307c9c97f4",
            slot=slot,
            lead_name="Rafael",
        )
    )
    assert client.insert_kwargs["sendUpdates"] == "none"
    assert "attendees" not in client.insert_kwargs["body"]


def test_json_environment_credentials_take_precedence_over_file(
    monkeypatch, tmp_path
) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text('{"source": "file"}', encoding="utf-8")
    monkeypatch.setenv("TEST_TOKEN_JSON", '{"source": "environment"}')
    monkeypatch.setenv("TEST_TOKEN_FILE", str(token_file))

    loaded = _load_json_setting(
        json_name="TEST_TOKEN_JSON",
        file_name="TEST_TOKEN_FILE",
        description="Test token",
    )

    assert loaded == {"source": "environment"}


def test_json_credentials_load_from_file(monkeypatch, tmp_path) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text('{"source": "file"}', encoding="utf-8")
    monkeypatch.delenv("TEST_TOKEN_JSON", raising=False)
    monkeypatch.setenv("TEST_TOKEN_FILE", str(token_file))

    loaded = _load_json_setting(
        json_name="TEST_TOKEN_JSON",
        file_name="TEST_TOKEN_FILE",
        description="Test token",
    )

    assert loaded == {"source": "file"}


def test_json_credentials_report_missing_configuration(monkeypatch) -> None:
    monkeypatch.delenv("TEST_TOKEN_JSON", raising=False)
    monkeypatch.delenv("TEST_TOKEN_FILE", raising=False)

    with pytest.raises(ValueError, match="TEST_TOKEN_JSON or TEST_TOKEN_FILE"):
        _load_json_setting(
            json_name="TEST_TOKEN_JSON",
            file_name="TEST_TOKEN_FILE",
            description="Test token",
        )
