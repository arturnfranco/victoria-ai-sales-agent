"""Commercial objection taxonomy."""

from enum import Enum


class ObjectionType(str, Enum):
    """Supported objections that must be resolved before booking."""

    PRICE = "PRICE"
    TRUST = "TRUST"
    TIME = "TIME"
    EXISTING_ADVISOR = "EXISTING_ADVISOR"
    DO_IT_MYSELF = "DO_IT_MYSELF"
    PARTNER_DECISION = "PARTNER_DECISION"
    NOT_PRIORITY = "NOT_PRIORITY"
    BAD_PREVIOUS_EXPERIENCE = "BAD_PREVIOUS_EXPERIENCE"
    WANTS_FREE_ADVICE = "WANTS_FREE_ADVICE"
    WANTS_IMMEDIATE_RECOMMENDATION = "WANTS_IMMEDIATE_RECOMMENDATION"
