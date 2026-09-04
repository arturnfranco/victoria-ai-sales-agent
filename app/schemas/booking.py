"""Provider-neutral booking state and scheduling contracts."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BookingStatus(str, Enum):
    NOT_OFFERED = "not_offered"
    OFFERED = "offered"
    SLOTS_PRESENTED = "slots_presented"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BOOKED = "booked"
    DEFERRED = "deferred"


class BookingSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime


class AvailabilityPreference(BaseModel):
    """Deterministic boundaries extracted from a scheduling request."""

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    earliest_time: time | None = None
    latest_time: time | None = None
    excluded_dates: set[date] = Field(default_factory=set)


class BookingState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: BookingStatus = BookingStatus.NOT_OFFERED
    offered_slots: list[BookingSlot] = Field(default_factory=list)
    selected_slot: BookingSlot | None = None
    operation_id: str | None = None
    provider_event_id: str | None = None
    meeting_url: str | None = None


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    slot: BookingSlot
    lead_name: str
    lead_email: str | None = None


class BookingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_event_id: str
    slot: BookingSlot
    meeting_url: str
