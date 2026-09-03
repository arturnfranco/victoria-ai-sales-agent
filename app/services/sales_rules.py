"""Deterministic commercial rules applied after LLM extraction."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

from app.schemas import (
    ConversationStage,
    EvidenceLevel,
    FitLevel,
    ObjectionType,
    QualificationEvidence,
    QualificationResult,
    RoutingSignals,
    ServiceRoute,
)


DIMENSION_WEIGHTS = {
    "need": 30,
    "financial_complexity": 20,
    "readiness": 20,
    "urgency": 15,
    "service_fit": 15,
}

EVIDENCE_FRACTIONS = {
    EvidenceLevel.NONE: Decimal("0"),
    EvidenceLevel.WEAK: Decimal("0.25"),
    EvidenceLevel.MODERATE: Decimal("0.50"),
    EvidenceLevel.STRONG: Decimal("0.75"),
    EvidenceLevel.VERY_STRONG: Decimal("1"),
}


def calculate_qualification(evidence: QualificationEvidence) -> QualificationResult:
    """Calculate the auditable 0-100 qualification score."""

    contributions: dict[str, int] = {}
    for dimension, weight in DIMENSION_WEIGHTS.items():
        assessment = getattr(evidence, dimension)
        weighted = Decimal(weight) * EVIDENCE_FRACTIONS[assessment.level]
        contributions[dimension] = int(weighted.quantize(Decimal("1"), ROUND_HALF_UP))

    score = sum(contributions.values())
    return QualificationResult(
        score=score, fit=classify_fit(score), contributions=contributions
    )


def classify_fit(score: int) -> FitLevel:
    """Classify a validated 0-100 score using approved thresholds."""

    if not 0 <= score <= 100:
        raise ValueError("qualification score must be between 0 and 100")
    if score >= 75:
        return FitLevel.HIGH
    if score >= 50:
        return FitLevel.MEDIUM
    if score >= 25:
        return FitLevel.LOW
    return FitLevel.NO_FIT


def route_service(signals: RoutingSignals) -> ServiceRoute | None:
    """Map explicit need signals to the supported service proposition."""

    if signals.out_of_scope_only:
        return ServiceRoute.NO_CURRENT_FIT
    if signals.planning_need and signals.investment_need:
        return ServiceRoute.BOTH
    if signals.planning_need:
        return ServiceRoute.FINANCIAL_PLANNING
    if signals.investment_need:
        return ServiceRoute.INVESTMENT_ADVISORY
    return None


def should_offer_booking(
    *,
    fit: FitLevel,
    service: ServiceRoute | None,
    primary_pain: str | None,
    readiness: EvidenceLevel,
    objection: ObjectionType | None,
) -> bool:
    """Decide whether evidence supports a human-meeting invitation."""

    if (
        service in {None, ServiceRoute.NO_CURRENT_FIT}
        or not primary_pain
        or objection is not None
    ):
        return False
    if fit is FitLevel.HIGH:
        return readiness in {
            EvidenceLevel.MODERATE,
            EvidenceLevel.STRONG,
            EvidenceLevel.VERY_STRONG,
        }
    if fit is FitLevel.MEDIUM:
        return readiness in {EvidenceLevel.STRONG, EvidenceLevel.VERY_STRONG}
    return False


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


_OBJECTION_PATTERNS: tuple[tuple[ObjectionType, tuple[str, ...]], ...] = (
    (
        ObjectionType.WANTS_IMMEDIATE_RECOMMENDATION,
        (
            r"\bqual (?:acao|investimento|ativo)\b",
            r"\bonde (?:devo )?investir\b",
            r"\bdevo (?:comprar|vender|colocar)\b",
            r"\bme diga (?:uma|qual) (?:acao|investimento)\b",
        ),
    ),
    (
        ObjectionType.WANTS_FREE_ADVICE,
        (r"\bgratis\b", r"\bgratuit[oa]\b", r"\bde graca\b", r"\bsem pagar\b"),
    ),
    (
        ObjectionType.BAD_PREVIOUS_EXPERIENCE,
        (r"\bexperiencia ruim\b", r"\bme decepcionei\b", r"\bfui mal atendid[oa]\b"),
    ),
    (
        ObjectionType.EXISTING_ADVISOR,
        (
            r"\b(?:ja )?tenho (?:assessor|consultor|planejador).*(?:nao quero|nao preciso|satisfeit[oa])\b",
            r"\bnao quero trocar (?:de )?(?:assessor|consultor|planejador)\b",
        ),
    ),
    (
        ObjectionType.DO_IT_MYSELF,
        (r"\bprefiro (?:fazer|cuidar) sozinh[oa]\b", r"\bcuido sozinh[oa]\b"),
    ),
    (
        ObjectionType.PARTNER_DECISION,
        (
            r"\bpreciso (?:falar|decidir|ver) com (?:meu |minha )?(?:socio|socia|esposa|marido|parceir[oa])\b",
        ),
    ),
    (
        ObjectionType.NOT_PRIORITY,
        (r"\bnao (?:e|eh) prioridade\b", r"\bdeixo? para depois\b", r"\bmais para frente\b"),
    ),
    (
        ObjectionType.PRICE,
        (r"\bquanto custa\b", r"\bpreco\b", r"\bmuito caro\b", r"\bqual (?:o )?valor\b"),
    ),
    (
        ObjectionType.TRUST,
        (r"\bnao confio\b", r"\bcomo (?:posso )?confiar\b", r"\bcredibilidade\b", r"\be seguro\b"),
    ),
    (
        ObjectionType.TIME,
        (r"\bnao tenho tempo\b", r"\bsem tempo\b", r"\bagenda (?:esta )?lotada\b"),
    ),
)


def detect_objection(
    text: str, proposed: ObjectionType | None = None
) -> ObjectionType | None:
    """Detect explicit objections, using validated semantic output as fallback."""

    normalized = _normalize(text)
    for objection, patterns in _OBJECTION_PATTERNS:
        if any(re.search(pattern, normalized) for pattern in patterns):
            return objection
    return proposed


def message_offers_booking(text: str) -> bool:
    """Detect an explicit meeting CTA in user-facing Portuguese text."""

    normalized = _normalize(text)
    patterns = (
        r"\bquer (?:que eu )?(?:ver|veja|mostre|busque).*(?:horarios?|agenda)\b",
        r"\bquer (?:agendar|marcar|conversar)\b",
        r"\bposso (?:ver|buscar|mostrar).*(?:horarios?|agenda)\b",
        r"\bvamos (?:agendar|marcar)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


ALLOWED_TRANSITIONS: dict[ConversationStage, frozenset[ConversationStage]] = {
    ConversationStage.OPENING: frozenset(
        {
            ConversationStage.OPENING,
            ConversationStage.DISCOVERY,
            ConversationStage.OBJECTION,
            ConversationStage.NO_FIT,
            ConversationStage.CLOSED,
        }
    ),
    ConversationStage.DISCOVERY: frozenset(
        {
            ConversationStage.DISCOVERY,
            ConversationStage.QUALIFICATION,
            ConversationStage.OBJECTION,
            ConversationStage.BOOKING,
            ConversationStage.NO_FIT,
            ConversationStage.CLOSED,
        }
    ),
    ConversationStage.QUALIFICATION: frozenset(
        {
            ConversationStage.DISCOVERY,
            ConversationStage.QUALIFICATION,
            ConversationStage.OBJECTION,
            ConversationStage.BOOKING,
            ConversationStage.NO_FIT,
            ConversationStage.CLOSED,
        }
    ),
    ConversationStage.OBJECTION: frozenset(
        {
            ConversationStage.OBJECTION,
            ConversationStage.DISCOVERY,
            ConversationStage.QUALIFICATION,
            ConversationStage.BOOKING,
            ConversationStage.NO_FIT,
            ConversationStage.CLOSED,
        }
    ),
    ConversationStage.BOOKING: frozenset(
        {
            ConversationStage.BOOKING,
            ConversationStage.BOOKED,
            ConversationStage.QUALIFICATION,
            ConversationStage.OBJECTION,
            ConversationStage.CLOSED,
        }
    ),
    ConversationStage.BOOKED: frozenset(
        {ConversationStage.BOOKED, ConversationStage.CLOSED}
    ),
    ConversationStage.NO_FIT: frozenset(
        {ConversationStage.NO_FIT, ConversationStage.CLOSED}
    ),
    ConversationStage.CLOSED: frozenset({ConversationStage.CLOSED}),
}


def is_transition_allowed(
    current: ConversationStage, proposed: ConversationStage
) -> bool:
    """Return whether a state transition is valid."""

    return proposed in ALLOWED_TRANSITIONS[current]
