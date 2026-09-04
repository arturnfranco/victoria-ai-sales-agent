"""Deterministic tests for VictorIA's Phase 2 Sales Core."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from pydantic import ValidationError

from app.agents import SalesAgent
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
    RoutingSignals,
    SalesAgentDraft,
    ServiceRoute,
)
from app.services.guardrails import GuardrailKind, inspect_message
from app.services.llm import (
    LLMJSONDecodeError,
    LLMProviderError,
    LLMRequest,
    LLMServiceError,
    OpenAIResponsesService,
)
from app.services.prompts import PromptLoadError, PromptLoader
from app.services.sales_rules import (
    calculate_qualification,
    classify_fit,
    detect_objection,
    is_transition_allowed,
    message_offers_booking,
    route_service,
    should_offer_booking,
)


def assessment(
    level: EvidenceLevel, evidence: str = "Explicitly stated by the lead."
) -> dict[str, str | None]:
    return {
        "level": level.value,
        "evidence": None if level is EvidenceLevel.NONE else evidence,
    }


def qualification(
    *,
    need: EvidenceLevel = EvidenceLevel.NONE,
    complexity: EvidenceLevel = EvidenceLevel.NONE,
    readiness: EvidenceLevel = EvidenceLevel.NONE,
    urgency: EvidenceLevel = EvidenceLevel.NONE,
    service_fit: EvidenceLevel = EvidenceLevel.NONE,
) -> dict[str, Any]:
    return {
        "need": assessment(need),
        "financial_complexity": assessment(complexity),
        "readiness": assessment(readiness),
        "urgency": assessment(urgency),
        "service_fit": assessment(service_fit),
    }


def draft(
    *,
    message: str = "Entendi. O que mais incomoda você nessa situação?",
    stage: str = "DISCOVERY",
    planning: bool = False,
    investment: bool = True,
    out_of_scope: bool = False,
    pain: str | None = "lack_of_strategy",
    objection: str | None = None,
    booking: bool = False,
    evidence: dict[str, Any] | None = None,
    request_scope: str = "in_scope",
) -> dict[str, Any]:
    return {
        "message": message,
        "proposed_stage": stage,
        "primary_pain": pain,
        "routing_signals": {
            "planning_need": planning,
            "investment_need": investment,
            "out_of_scope_only": out_of_scope,
        },
        "qualification": evidence or qualification(
            need=EvidenceLevel.MODERATE,
            service_fit=EvidenceLevel.MODERATE,
        ),
        "objection": objection,
        "should_offer_booking": booking,
        "request_scope": request_scope,
    }


class QueueLLM:
    def __init__(self, *responses: Mapping[str, Any] | Exception) -> None:
        self.responses = deque(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def test_structured_draft_forbids_extra_fields() -> None:
    payload = draft()
    payload["invented"] = True
    with pytest.raises(ValidationError):
        SalesAgentDraft.model_validate(payload)


def test_non_zero_evidence_requires_a_note() -> None:
    with pytest.raises(ValidationError):
        EvidenceAssessment(level=EvidenceLevel.STRONG, evidence=None)


def test_none_evidence_rejects_a_note() -> None:
    with pytest.raises(ValidationError):
        EvidenceAssessment(level=EvidenceLevel.NONE, evidence="unsupported")


def test_routing_signals_reject_conflicting_scope() -> None:
    with pytest.raises(ValidationError):
        RoutingSignals(
            planning_need=True,
            investment_need=False,
            out_of_scope_only=True,
        )


def test_qualification_uses_half_up_weighted_contributions() -> None:
    evidence = QualificationEvidence.model_validate(
        qualification(
            need=EvidenceLevel.WEAK,
            complexity=EvidenceLevel.WEAK,
            readiness=EvidenceLevel.WEAK,
            urgency=EvidenceLevel.WEAK,
            service_fit=EvidenceLevel.WEAK,
        )
    )
    result = calculate_qualification(evidence)
    assert result.contributions == {
        "need": 8,
        "financial_complexity": 5,
        "readiness": 5,
        "urgency": 4,
        "service_fit": 4,
    }
    assert result.score == 26
    assert result.fit is FitLevel.LOW


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, FitLevel.NO_FIT),
        (24, FitLevel.NO_FIT),
        (25, FitLevel.LOW),
        (49, FitLevel.LOW),
        (50, FitLevel.MEDIUM),
        (74, FitLevel.MEDIUM),
        (75, FitLevel.HIGH),
        (100, FitLevel.HIGH),
    ],
)
def test_fit_threshold_boundaries(score: int, expected: FitLevel) -> None:
    assert classify_fit(score) is expected


@pytest.mark.parametrize("score", [-1, 101])
def test_fit_rejects_out_of_range_scores(score: int) -> None:
    with pytest.raises(ValueError):
        classify_fit(score)


@pytest.mark.parametrize(
    ("planning", "investment", "out_of_scope", "expected"),
    [
        (False, False, False, None),
        (True, False, False, ServiceRoute.FINANCIAL_PLANNING),
        (False, True, False, ServiceRoute.INVESTMENT_ADVISORY),
        (True, True, False, ServiceRoute.BOTH),
        (False, False, True, ServiceRoute.NO_CURRENT_FIT),
    ],
)
def test_service_routing(
    planning: bool,
    investment: bool,
    out_of_scope: bool,
    expected: ServiceRoute | None,
) -> None:
    signals = RoutingSignals(
        planning_need=planning,
        investment_need=investment,
        out_of_scope_only=out_of_scope,
    )
    assert route_service(signals) is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Quanto custa o serviço?", ObjectionType.PRICE),
        ("Não confio em assessores.", ObjectionType.TRUST),
        ("Não tenho tempo para uma reunião.", ObjectionType.TIME),
        (
            "Já tenho assessor e estou satisfeito, não preciso de outro.",
            ObjectionType.EXISTING_ADVISOR,
        ),
        ("Prefiro cuidar sozinho.", ObjectionType.DO_IT_MYSELF),
        ("Preciso falar com minha esposa.", ObjectionType.PARTNER_DECISION),
        ("Isso não é prioridade agora.", ObjectionType.NOT_PRIORITY),
        ("Já tive uma experiência ruim.", ObjectionType.BAD_PREVIOUS_EXPERIENCE),
        ("Quero uma análise gratuita.", ObjectionType.WANTS_FREE_ADVICE),
        ("Qual ação devo comprar?", ObjectionType.WANTS_IMMEDIATE_RECOMMENDATION),
    ],
)
def test_every_objection_category(message: str, expected: ObjectionType) -> None:
    assert detect_objection(message) is expected


def test_existing_advisor_alone_is_not_an_objection() -> None:
    assert detect_objection("Hoje eu tenho um assessor.") is None


def test_semantic_objection_is_used_as_fallback() -> None:
    assert (
        detect_objection("Talvez.", ObjectionType.TRUST) is ObjectionType.TRUST
    )


@pytest.mark.parametrize(
    ("fit", "readiness", "objection", "expected"),
    [
        (FitLevel.HIGH, EvidenceLevel.MODERATE, None, True),
        (FitLevel.MEDIUM, EvidenceLevel.STRONG, None, True),
        (FitLevel.MEDIUM, EvidenceLevel.MODERATE, None, False),
        (FitLevel.LOW, EvidenceLevel.VERY_STRONG, None, False),
        (FitLevel.HIGH, EvidenceLevel.STRONG, ObjectionType.TIME, False),
    ],
)
def test_booking_readiness(
    fit: FitLevel,
    readiness: EvidenceLevel,
    objection: ObjectionType | None,
    expected: bool,
) -> None:
    assert (
        should_offer_booking(
            fit=fit,
            service=ServiceRoute.INVESTMENT_ADVISORY,
            primary_pain="lack_of_strategy",
            readiness=readiness,
            objection=objection,
        )
        is expected
    )


def test_booking_requires_in_scope_service_and_pain() -> None:
    assert not should_offer_booking(
        fit=FitLevel.HIGH,
        service=ServiceRoute.NO_CURRENT_FIT,
        primary_pain="pain",
        readiness=EvidenceLevel.STRONG,
        objection=None,
    )
    assert not should_offer_booking(
        fit=FitLevel.HIGH,
        service=ServiceRoute.BOTH,
        primary_pain=None,
        readiness=EvidenceLevel.STRONG,
        objection=None,
    )


def test_visible_booking_cta_detection() -> None:
    assert message_offers_booking("Quer que eu veja alguns horários disponíveis?")
    assert message_offers_booking("Vamos agendar uma conversa.")
    assert not message_offers_booking(
        "Ainda precisamos entender melhor seu contexto antes de falar em reunião."
    )


def test_state_transition_rules_block_early_booking_and_terminal_reopen() -> None:
    assert not is_transition_allowed(
        ConversationStage.OPENING, ConversationStage.BOOKING
    )
    assert is_transition_allowed(
        ConversationStage.DISCOVERY, ConversationStage.BOOKING
    )
    assert not is_transition_allowed(
        ConversationStage.CLOSED, ConversationStage.DISCOVERY
    )


def test_guardrail_detects_personalized_advice_but_not_general_education() -> None:
    decision = inspect_message(
        "Tenho R$ 500 mil. Devo colocar em Tesouro IPCA ou ações?"
    )
    assert decision is not None
    assert decision.kind is GuardrailKind.PERSONALIZED_FINANCIAL_ADVICE
    assert inspect_message("O que é planejamento financeiro?") is None


def test_guardrail_detects_credential_request() -> None:
    decision = inspect_message("Você recupera a senha da minha conta no banco?")
    assert decision is not None
    assert decision.kind is GuardrailKind.SENSITIVE_CREDENTIALS


def test_prompt_loader_loads_exact_version() -> None:
    loaded = PromptLoader().load("sales_v1")
    assert loaded.version == "sales_v1"
    assert "VictorIA" in loaded.content


@pytest.mark.parametrize("version", ["../sales_v1", "sales_v0", "evaluator_v1", ""])
def test_prompt_loader_rejects_unsafe_versions(version: str) -> None:
    with pytest.raises(PromptLoadError):
        PromptLoader().load(version)


def test_prompt_loader_rejects_missing_and_empty_prompts(tmp_path: Path) -> None:
    loader = PromptLoader(tmp_path)
    with pytest.raises(PromptLoadError):
        loader.load("sales_v2")
    (tmp_path / "sales_v2.md").write_text("  ", encoding="utf-8")
    with pytest.raises(PromptLoadError):
        loader.load("sales_v2")


def test_openai_adapter_requires_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIResponsesService(client=object())
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        OpenAIResponsesService(client=object(), api_key="test-key")


def test_openai_adapter_sends_strict_schema_without_storage() -> None:
    captured: dict[str, Any] = {}

    class Responses:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(output_text=json.dumps({"ok": True}))

    client = SimpleNamespace(responses=Responses())
    service = OpenAIResponsesService(
        client=client, api_key="test-key", model="test-model"
    )
    request = LLMRequest(
        instructions="instructions",
        messages=[
            ConversationMessage(role=MessageRole.USER, content="Olá"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="Olá!"),
        ],
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        schema_name="test_schema",
    )
    assert service.complete(request) == {"ok": True}
    assert captured["model"] == "test-model"
    assert captured["store"] is False
    assert captured["input"] == [
        {"role": "user", "content": "Olá"},
        {"role": "assistant", "content": "Olá!"},
    ]
    assert captured["text"]["format"]["strict"] is True
    assert captured["text"]["format"]["type"] == "json_schema"


def test_openai_adapter_wraps_provider_and_json_errors() -> None:
    class Responses:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            raise RuntimeError("secret provider detail")

    service = OpenAIResponsesService(
        client=SimpleNamespace(responses=Responses()),
        api_key="test-key",
        model="test-model",
    )
    request = LLMRequest("i", [], {}, "schema")
    with pytest.raises(LLMProviderError, match="provider call failed"):
        service.complete(request)

    service = OpenAIResponsesService(
        client=SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(output_text="not-json")
            )
        ),
        api_key="test-key",
        model="test-model",
    )
    with pytest.raises(LLMJSONDecodeError, match="not valid JSON"):
        service.complete(request)


def test_agent_preserves_ordered_history_and_structured_state() -> None:
    llm = QueueLLM(draft())
    session = ConversationSession()
    output = SalesAgent(llm).handle_message(
        session, "Minha carteira parece sem estratégia."
    )
    assert output.stage is ConversationStage.DISCOVERY
    assert output.service is ServiceRoute.INVESTMENT_ADVISORY
    assert output.qualification_score == 23
    assert [message.role for message in session.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert llm.requests[0].messages[-1].content.startswith("Minha carteira")
    assert session.last_output == output


def test_agent_uses_prior_history_on_next_turn() -> None:
    llm = QueueLLM(draft(), draft(message="E qual seria o resultado desejado?"))
    session = ConversationSession()
    agent = SalesAgent(llm)
    agent.handle_message(session, "Minha carteira está fragmentada.")
    agent.handle_message(session, "Isso toma muito do meu tempo.")
    assert len(llm.requests[1].messages) == 3
    assert [message.role for message in llm.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]


def test_agent_reconciles_high_fit_to_booking() -> None:
    high_evidence = qualification(
        need=EvidenceLevel.STRONG,
        complexity=EvidenceLevel.STRONG,
        readiness=EvidenceLevel.STRONG,
        urgency=EvidenceLevel.STRONG,
        service_fit=EvidenceLevel.STRONG,
    )
    llm = QueueLLM(
        draft(
            message="Faz sentido conversar com um especialista. Quer ver horários?",
            stage="BOOKING",
            planning=True,
            investment=True,
            booking=True,
            evidence=high_evidence,
        )
    )
    session = ConversationSession(stage=ConversationStage.DISCOVERY)
    output = SalesAgent(llm).handle_message(session, "Quero resolver isso agora.")
    assert output.fit is FitLevel.HIGH
    assert output.qualification_score == 75
    assert output.stage is ConversationStage.BOOKING
    assert output.next_action is NextAction.OFFER_BOOKING


def test_agent_adds_canonical_cta_when_booking_wording_is_not_detected() -> None:
    high_evidence = qualification(
        need=EvidenceLevel.STRONG,
        complexity=EvidenceLevel.STRONG,
        readiness=EvidenceLevel.STRONG,
        urgency=EvidenceLevel.STRONG,
        service_fit=EvidenceLevel.STRONG,
    )
    llm = QueueLLM(
        draft(
            message="Faz sentido avançarmos para uma conversa com um especialista?",
            stage="BOOKING",
            planning=True,
            investment=True,
            booking=True,
            evidence=high_evidence,
        )
    )
    output = SalesAgent(llm).handle_message(
        ConversationSession(stage=ConversationStage.DISCOVERY),
        "Quero organizar isso com ajuda profissional.",
    )
    assert len(llm.requests) == 1
    assert output.stage is ConversationStage.BOOKING
    assert output.should_offer_booking
    assert output.message.endswith("Quer que eu veja alguns horários disponíveis?")
    assert message_offers_booking(output.message)


def test_explicit_objection_blocks_booking_and_sets_objection_state() -> None:
    strong = qualification(
        need=EvidenceLevel.VERY_STRONG,
        complexity=EvidenceLevel.STRONG,
        readiness=EvidenceLevel.STRONG,
        urgency=EvidenceLevel.STRONG,
        service_fit=EvidenceLevel.STRONG,
    )
    llm = QueueLLM(draft(booking=False, evidence=strong))
    session = ConversationSession(stage=ConversationStage.DISCOVERY)
    output = SalesAgent(llm).handle_message(
        session, "Não tenho tempo para uma reunião."
    )
    assert output.objection is ObjectionType.TIME
    assert output.stage is ConversationStage.OBJECTION
    assert not output.should_offer_booking


def test_booking_during_time_objection_retries_with_specific_correction() -> None:
    strong = qualification(
        need=EvidenceLevel.VERY_STRONG,
        complexity=EvidenceLevel.STRONG,
        readiness=EvidenceLevel.STRONG,
        urgency=EvidenceLevel.STRONG,
        service_fit=EvidenceLevel.STRONG,
    )
    invalid = draft(
        message="Quer que eu veja alguns horários disponíveis?",
        stage="BOOKING",
        planning=True,
        investment=True,
        objection="TIME",
        booking=True,
        evidence=strong,
    )
    corrected = draft(
        message="Entendo. O que ajudaria a tornar esse cuidado viável na sua rotina?",
        stage="OBJECTION",
        planning=True,
        investment=True,
        objection="TIME",
        booking=False,
        evidence=strong,
    )
    llm = QueueLLM(invalid, corrected)
    output = SalesAgent(llm).handle_message(
        ConversationSession(stage=ConversationStage.OBJECTION),
        "Minha rotina continua sem espaço para uma reunião.",
    )
    assert len(llm.requests) == 2
    assert "A objeção TIME está ativa" in llm.requests[1].instructions
    assert "should_offer_booking=false" in llm.requests[1].instructions
    assert output.stage is ConversationStage.OBJECTION
    assert output.objection is ObjectionType.TIME
    assert not output.should_offer_booking


def test_no_fit_routing_overrides_numeric_fit() -> None:
    llm = QueueLLM(
        draft(
            message="Não trabalhamos com sinais de day trade.",
            stage="NO_FIT",
            investment=False,
            out_of_scope=True,
            pain="trading_signals",
        )
    )
    output = SalesAgent(llm).handle_message(
        ConversationSession(), "Vocês oferecem sinais de day trade?"
    )
    assert output.service is ServiceRoute.NO_CURRENT_FIT
    assert output.fit is FitLevel.NO_FIT
    assert output.stage is ConversationStage.NO_FIT
    assert output.next_action is NextAction.CLOSE_HELPFULLY


def test_guardrail_bypasses_llm_and_never_offers_booking() -> None:
    llm = QueueLLM()
    output = SalesAgent(llm).handle_message(
        ConversationSession(), "Devo comprar ações ou Tesouro IPCA?"
    )
    assert not llm.requests
    assert output.objection is ObjectionType.WANTS_IMMEDIATE_RECOMMENDATION
    assert output.service is ServiceRoute.INVESTMENT_ADVISORY
    assert not output.should_offer_booking
    assert "não posso recomendar" in output.message


def test_programming_request_is_declined_without_losing_sales_state() -> None:
    previous = SalesAgent(QueueLLM(draft())).handle_message(
        ConversationSession(), "Quero organizar meus investimentos."
    )
    session = ConversationSession(
        stage=previous.stage,
        last_output=previous,
        discovery_questions_asked=1,
    )
    llm = QueueLLM()

    output = SalesAgent(llm).handle_message(
        session, "Faça um script Python para somar dois números"
    )

    assert not llm.requests
    assert output.next_action is NextAction.REDIRECT_TO_SCOPE
    assert output.stage is previous.stage
    assert output.qualification == previous.qualification
    assert "script" not in output.message.casefold()


def test_structured_out_of_scope_request_preserves_state() -> None:
    session = ConversationSession(stage=ConversationStage.DISCOVERY)
    output = SalesAgent(
        QueueLLM(draft(request_scope="out_of_scope"))
    ).handle_message(session, "Conte uma receita de bolo")

    assert output.next_action is NextAction.REDIRECT_TO_SCOPE
    assert output.stage is ConversationStage.DISCOVERY
    assert not output.should_offer_booking


def test_discovery_question_limit_pauses_instead_of_falling_back() -> None:
    session = ConversationSession(discovery_questions_asked=5)
    llm = QueueLLM(draft(), draft())

    output = SalesAgent(llm).handle_message(session, "Ainda não sei.")

    assert len(llm.requests) == 2
    assert output.next_action is NextAction.PAUSE_DISCOVERY
    assert "?" not in output.message
    assert session.discovery_questions_asked == 5


def test_provider_failure_logs_stack_and_returns_reference(caplog) -> None:
    llm = QueueLLM(LLMProviderError("failed"), LLMProviderError("failed"))

    with caplog.at_level("WARNING"):
        output = SalesAgent(llm).handle_message(
            ConversationSession(),
            "Olá",
            turn_id="8f31abcd1234",
            conversation_id="conversation-test",
        )

    assert "REF-8F31ABCD" in output.message
    records = [record for record in caplog.records if "category=openai_call" in record.message]
    assert len(records) == 2
    assert all(record.exc_info is not None for record in records)
    assert all("conversation-test" in record.message for record in records)


def test_validation_failure_is_traceable_and_uses_interpretation_reply(caplog) -> None:
    with caplog.at_level("WARNING"):
        output = SalesAgent(QueueLLM({}, {})).handle_message(
            ConversationSession(), turn_id="aa11bb22cc33", user_message="Olá"
        )

    assert "REF-AA11BB22" in output.message
    assert "interpretar" in output.message
    records = [
        record for record in caplog.records
        if "category=pydantic_validation" in record.message
    ]
    assert len(records) == 2
    assert all(record.exc_info is not None for record in records)


def test_invalid_output_retries_once_then_recovers() -> None:
    invalid = draft()
    invalid.pop("qualification")
    llm = QueueLLM(invalid, draft())
    output = SalesAgent(llm).handle_message(ConversationSession(), "Olá")
    assert len(llm.requests) == 2
    assert "saída anterior foi inválida" in llm.requests[1].instructions
    assert output.next_action is NextAction.CONTINUE_DISCOVERY


def test_invalid_output_twice_fails_safely_without_false_booking() -> None:
    llm = QueueLLM({}, {})
    session = ConversationSession()
    output = SalesAgent(llm).handle_message(session, "Olá")
    assert len(llm.requests) == 2
    assert output.next_action is NextAction.RETRY_LATER
    assert output.stage is ConversationStage.OPENING
    assert not output.should_offer_booking
    assert len(session.messages) == 2


def test_provider_failure_twice_fails_safely() -> None:
    llm = QueueLLM(LLMServiceError("failed"), LLMServiceError("failed"))
    output = SalesAgent(llm).handle_message(ConversationSession(), "Olá")
    assert output.next_action is NextAction.RETRY_LATER
    assert not output.should_offer_booking


def test_inappropriate_booking_cta_is_retried() -> None:
    inconsistent = draft(booking=True)
    llm = QueueLLM(inconsistent, draft())
    output = SalesAgent(llm).handle_message(ConversationSession(), "Olá")
    assert len(llm.requests) == 2
    assert not output.should_offer_booking


def test_hidden_visible_booking_cta_is_retried() -> None:
    visible_cta_with_false_flag = draft(
        message="Quer que eu veja alguns horários?", booking=False
    )
    llm = QueueLLM(visible_cta_with_false_flag, draft())
    output = SalesAgent(llm).handle_message(ConversationSession(), "Olá")
    assert len(llm.requests) == 2
    assert not output.should_offer_booking


def test_invalid_early_booking_transition_is_retried() -> None:
    high_evidence = qualification(
        need=EvidenceLevel.STRONG,
        complexity=EvidenceLevel.STRONG,
        readiness=EvidenceLevel.STRONG,
        urgency=EvidenceLevel.STRONG,
        service_fit=EvidenceLevel.STRONG,
    )
    early_booking = draft(stage="BOOKING", booking=True, evidence=high_evidence)
    llm = QueueLLM(early_booking, draft())
    output = SalesAgent(llm).handle_message(ConversationSession(), "Olá")
    assert len(llm.requests) == 2
    assert output.stage is ConversationStage.DISCOVERY
