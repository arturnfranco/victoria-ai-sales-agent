"""Tests for Phase 3 persistence and the shared Sales Service."""

from __future__ import annotations

from collections import deque
from typing import Any, Mapping

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.agents import SalesAgent
from app.db.base import Base
from app.db.session import (
    create_database_engine,
    create_session_factory,
    normalize_database_url,
)
from app.repositories.messages import MessageRepository
from app.schemas import ConversationStage
from app.services.llm import LLMRequest
from app.services.sales import SalesService


def assessment(level: str, note: str = "Informação explícita do lead.") -> dict:
    return {"level": level, "evidence": None if level == "none" else note}


def draft(message: str, *, level: str = "moderate") -> dict[str, Any]:
    return {
        "message": message,
        "proposed_stage": "DISCOVERY",
        "primary_pain": "lack_of_strategy",
        "routing_signals": {
            "planning_need": False,
            "investment_need": True,
            "out_of_scope_only": False,
        },
        "qualification": {
            "need": assessment(level),
            "financial_complexity": assessment(level),
            "readiness": assessment("weak"),
            "urgency": assessment("weak"),
            "service_fit": assessment(level),
        },
        "objection": None,
        "should_offer_booking": False,
    }


class QueueLLM:
    def __init__(self, *responses: Mapping[str, Any]) -> None:
        self.responses = deque(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        return self.responses.popleft()


@pytest.fixture
def database_url(tmp_path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'victoria.db'}"


@pytest.fixture
def session_factory(database_url):
    factory = create_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
    return factory


def make_service(session_factory, llm, **kwargs) -> SalesService:
    return SalesService(
        session_factory=session_factory,
        agent=SalesAgent(llm),
        **kwargs,
    )


def test_database_url_normalization() -> None:
    assert (
        normalize_database_url("postgres://user:pass@host/db")
        == "postgresql+psycopg://user:pass@host/db"
    )
    assert (
        normalize_database_url("postgresql://user:pass@host/db")
        == "postgresql+psycopg://user:pass@host/db"
    )
    assert normalize_database_url("sqlite:///test.db") == "sqlite:///test.db"


def test_four_primary_tables_are_created(session_factory) -> None:
    names = set(inspect(session_factory.kw["bind"]).get_table_names())
    assert names == {"leads", "conversations", "messages", "evaluations"}


def test_start_and_handle_persist_complete_turn(session_factory) -> None:
    service = make_service(session_factory, QueueLLM(draft("Qual é seu objetivo?")))
    view = service.start_conversation(
        name="  Mariana  ",
        email=" mariana@example.com ",
        phone_number=" ",
    )

    result = service.handle_message(
        view.conversation.id, "Minha carteira está sem estratégia."
    )

    assert result.view.lead.name == "Mariana"
    assert result.view.lead.email == "mariana@example.com"
    assert result.view.lead.phone_number is None
    assert result.view.lead.service_interest == "investment_advisory"
    assert result.view.lead.qualification_status == result.output.fit.value
    assert result.view.lead.lead_score == result.output.qualification_score
    assert result.view.conversation.prompt_version == "sales_v1"
    assert result.view.conversation.current_stage == "DISCOVERY"
    assert result.view.session.last_output == result.output
    assert [message.position for message in result.view.messages] == [0, 1]
    assert [message.role for message in result.view.messages] == [
        "user",
        "assistant",
    ]
    assert result.view.messages[0].stage == "OPENING"
    assert result.view.messages[1].stage == "DISCOVERY"


def test_restart_reopens_and_continues_validated_state(
    database_url, session_factory
) -> None:
    first_llm = QueueLLM(draft("O que isso causa hoje?"))
    first_service = make_service(session_factory, first_llm)
    started = first_service.start_conversation(name="Rafael")
    first_service.handle_message(started.conversation.id, "Não tenho uma estratégia.")
    session_factory.kw["bind"].dispose()

    restarted_factory = create_session_factory(database_url)
    second_llm = QueueLLM(draft("Qual resultado você deseja?"))
    restarted_service = make_service(restarted_factory, second_llm)
    restored = restarted_service.get_conversation(started.conversation.id)

    assert restored.session.stage is ConversationStage.DISCOVERY
    assert len(restored.session.messages) == 2

    continued = restarted_service.handle_message(
        started.conversation.id, "Quero organizar os investimentos."
    )
    assert len(second_llm.requests[0].messages) == 3
    assert len(continued.view.messages) == 4
    assert [message.position for message in continued.view.messages] == [0, 1, 2, 3]


def test_lists_return_persisted_records(session_factory) -> None:
    service = make_service(session_factory, QueueLLM())
    created = service.start_conversation(name="Ana")

    assert [lead.id for lead in service.list_leads()] == [created.lead.id]
    assert [item.id for item in service.list_conversations()] == [
        created.conversation.id
    ]


def test_blank_lead_name_and_message_are_rejected(session_factory) -> None:
    service = make_service(session_factory, QueueLLM())
    with pytest.raises(ValueError, match="name"):
        service.start_conversation(name=" ")
    view = service.start_conversation(name="Ana")
    with pytest.raises(ValueError, match="message"):
        service.handle_message(view.conversation.id, " ")


class FailingMessageRepository(MessageRepository):
    def add(self, db: Session, **kwargs):
        record = super().add(db, **kwargs)
        if kwargs["role"].value == "assistant":
            raise RuntimeError("simulated persistence failure")
        return record


def test_failed_turn_rolls_back_both_messages_and_snapshot(session_factory) -> None:
    service = make_service(
        session_factory,
        QueueLLM(draft("Resposta segura.")),
        message_repository=FailingMessageRepository(),
    )
    view = service.start_conversation(name="Carlos")

    with pytest.raises(RuntimeError, match="simulated"):
        service.handle_message(view.conversation.id, "Olá")

    restored = service.get_conversation(view.conversation.id)
    assert restored.messages == ()
    assert restored.session.messages == []
    assert restored.session.stage is ConversationStage.OPENING
