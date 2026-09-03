#!/usr/bin/env python3
"""Run three reproducible Phase 2 conversations without an API key."""

from __future__ import annotations

from collections import deque
from typing import Any, Mapping

from app.agents import SalesAgent
from app.schemas import ConversationSession, ConversationStage
from app.services.llm import LLMRequest


def evidence(level: str, note: str = "Informação explicitada pelo lead.") -> dict:
    return {"level": level, "evidence": None if level == "none" else note}


def qualification(level: str) -> dict:
    return {
        "need": evidence(level),
        "financial_complexity": evidence(level),
        "readiness": evidence(level),
        "urgency": evidence(level),
        "service_fit": evidence(level),
    }


class ScriptedLLM:
    """Minimal deterministic implementation of the provider-neutral contract."""

    def __init__(self, *responses: Mapping[str, Any]) -> None:
        self._responses = deque(responses)

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        return self._responses.popleft()


def show_turn(agent: SalesAgent, session: ConversationSession, message: str) -> None:
    output = agent.handle_message(session, message)
    print(f"Lead: {message}")
    print(f"VictorIA: {output.message}")
    print(
        "State:",
        output.model_dump_json(
            include={
                "stage",
                "service",
                "fit",
                "qualification_score",
                "objection",
                "should_offer_booking",
                "next_action",
            }
        ),
    )


def high_fit_demo() -> None:
    print("\n=== High fit: discovery to booking readiness ===")
    agent = SalesAgent(
        ScriptedLLM(
            {
                "message": (
                    "Entendi que sua carteira está fragmentada. "
                    "Que impacto isso traz para você hoje?"
                ),
                "proposed_stage": "DISCOVERY",
                "primary_pain": "portfolio_fragmentation",
                "routing_signals": {
                    "planning_need": False,
                    "investment_need": True,
                    "out_of_scope_only": False,
                },
                "qualification": {
                    "need": evidence("moderate"),
                    "financial_complexity": evidence("strong"),
                    "readiness": evidence("weak"),
                    "urgency": evidence("weak"),
                    "service_fit": evidence("strong"),
                },
                "objection": None,
                "should_offer_booking": False,
            },
            {
                "message": (
                    "Pelo que você contou, faz sentido conversar com um especialista "
                    "para aprofundar isso. Quer que eu veja alguns horários?"
                ),
                "proposed_stage": "BOOKING",
                "primary_pain": "portfolio_fragmentation",
                "routing_signals": {
                    "planning_need": False,
                    "investment_need": True,
                    "out_of_scope_only": False,
                },
                "qualification": qualification("strong"),
                "objection": None,
                "should_offer_booking": True,
            },
        )
    )
    session = ConversationSession()
    show_turn(agent, session, "Tenho investimentos espalhados e sem estratégia.")
    show_turn(
        agent,
        session,
        "Isso consome meu tempo e quero resolver com ajuda profissional agora.",
    )
    assert session.stage is ConversationStage.BOOKING


def guardrail_demo() -> None:
    print("\n=== Personalized advice guardrail ===")
    agent = SalesAgent(ScriptedLLM())
    session = ConversationSession()
    show_turn(agent, session, "Tenho R$ 500 mil. Devo comprar ações ou Tesouro IPCA?")
    assert session.stage is ConversationStage.OBJECTION
    assert session.last_output is not None
    assert not session.last_output.should_offer_booking


def no_fit_demo() -> None:
    print("\n=== No current fit ===")
    agent = SalesAgent(
        ScriptedLLM(
            {
                "message": (
                    "Não oferecemos sinais de day trade. Nosso trabalho é voltado "
                    "a planejamento e apoio profissional de longo prazo."
                ),
                "proposed_stage": "NO_FIT",
                "primary_pain": "trading_signals",
                "routing_signals": {
                    "planning_need": False,
                    "investment_need": False,
                    "out_of_scope_only": True,
                },
                "qualification": qualification("none"),
                "objection": None,
                "should_offer_booking": False,
            }
        )
    )
    session = ConversationSession()
    show_turn(agent, session, "Vocês enviam sinais diários para day trade?")
    assert session.stage is ConversationStage.NO_FIT


if __name__ == "__main__":
    high_fit_demo()
    guardrail_demo()
    no_fit_demo()
    print("\nPhase 2 offline demonstration passed.")
