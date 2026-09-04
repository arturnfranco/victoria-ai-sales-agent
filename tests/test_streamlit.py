"""Smoke test for the Phase 3 Streamlit console."""

from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import create_session_factory
from app.services.llm import OpenAIResponsesService


def valid_discovery_response() -> dict[str, Any]:
    return {
        "message": "Entendi. Qual é sua principal prioridade financeira hoje?",
        "proposed_stage": "DISCOVERY",
        "primary_pain": "lack_of_organization",
        "routing_signals": {
            "planning_need": True,
            "investment_need": False,
            "out_of_scope_only": False,
        },
        "qualification": {
            "need": {"level": "moderate", "evidence": "Busca organização."},
            "financial_complexity": {"level": "none", "evidence": None},
            "readiness": {"level": "none", "evidence": None},
            "urgency": {"level": "none", "evidence": None},
            "service_fit": {
                "level": "moderate",
                "evidence": "Necessidade de planejamento.",
            },
        },
        "objection": None,
        "should_offer_booking": False,
    }


def test_streamlit_creates_lead_and_navigates_persisted_views(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'streamlit.db'}"
    factory = create_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
    factory.kw["bind"].dispose()

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    app_path = Path(__file__).parents[1] / "dashboard" / "streamlit_app.py"
    app = AppTest.from_file(app_path).run(timeout=20)
    assert not app.exception
    assert app.header[0].value == "Playground"

    app.text_input[0].set_value("Mariana")
    app.text_input[1].set_value("mariana@example.com")
    app.button[0].click().run(timeout=20)

    assert not app.exception
    assert app.selectbox[0].label == "Reabrir conversa"
    assert any("Mariana · OPENING" in option for option in app.selectbox[0].options)
    assert not any("Mariana · OPENING ·" in option for option in app.selectbox[0].options)
    assert "Mariana" in [item.value for item in app.subheader]

    monkeypatch.setattr(
        OpenAIResponsesService,
        "complete",
        lambda self, request: valid_discovery_response(),
    )
    app.chat_input[0].set_value("Quero organizar minhas finanças.").run(timeout=20)
    assert not app.exception
    rendered_text = [item.value for item in app.markdown]
    assert "Quero organizar minhas finanças." in rendered_text
    assert valid_discovery_response()["message"] in rendered_text
    assert any(metric.value == "DISCOVERY" for metric in app.metric)

    app.radio[0].set_value("Leads").run(timeout=20)
    assert app.header[0].value == "Leads"
    assert not app.info

    app.radio[0].set_value("Conversas").run(timeout=20)
    assert app.header[0].value == "Conversas"
    assert not app.info
    assert app.selectbox[0].label == "Inspecionar histórico"
