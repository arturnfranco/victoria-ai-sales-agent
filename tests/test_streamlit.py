"""Smoke test for the Phase 3 Streamlit console."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import create_session_factory


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
    assert "Mariana" in [item.value for item in app.subheader]

    app.radio[0].set_value("Leads").run(timeout=20)
    assert app.header[0].value == "Leads"
    assert not app.info

    app.radio[0].set_value("Conversas").run(timeout=20)
    assert app.header[0].value == "Conversas"
    assert not app.info
    assert app.selectbox[0].label == "Inspecionar histórico"
