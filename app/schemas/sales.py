"""Validated LLM draft and final Sales Agent contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.conversation import (
    ConversationMessage,
    ConversationStage,
)
from app.schemas.objection import ObjectionType
from app.schemas.qualification import FitLevel, QualificationEvidence
from app.schemas.routing import RoutingSignals, ServiceRoute
from app.schemas.booking import BookingState


class NextAction(str, Enum):
    """Machine-readable action selected for the next commercial step."""

    CONTINUE_DISCOVERY = "continue_discovery"
    ADDRESS_OBJECTION = "address_objection"
    OFFER_BOOKING = "offer_booking"
    CLOSE_HELPFULLY = "close_helpfully"
    RETRY_LATER = "retry_later"
    PRESENT_SLOTS = "present_slots"
    CONFIRM_BOOKING = "confirm_booking"
    BOOK_MEETING = "book_meeting"


class SalesAgentDraft(BaseModel):
    """Strict structured output requested from the LLM."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1)
    proposed_stage: ConversationStage
    primary_pain: str | None
    routing_signals: RoutingSignals
    qualification: QualificationEvidence
    objection: ObjectionType | None
    should_offer_booking: bool


class SalesAgentOutput(BaseModel):
    """Validated and deterministically reconciled Sales Agent response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1)
    stage: ConversationStage
    service: ServiceRoute | None
    fit: FitLevel
    primary_pain: str | None
    objection: ObjectionType | None
    qualification: QualificationEvidence
    qualification_score: int = Field(ge=0, le=100)
    should_offer_booking: bool
    next_action: NextAction


class ConversationSession(BaseModel):
    """In-memory state owned by channels until persistence is introduced."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    stage: ConversationStage = ConversationStage.OPENING
    prompt_version: str = "sales_v1"
    messages: list[ConversationMessage] = Field(default_factory=list)
    last_output: SalesAgentOutput | None = None
    booking: BookingState = Field(default_factory=BookingState)
