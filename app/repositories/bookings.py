"""Booking persistence operations."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import Booking
from app.schemas.booking import BookingResult


class BookingRepository:
    def save_confirmed(
        self,
        db: Session,
        *,
        conversation_id: uuid.UUID,
        lead_id: uuid.UUID,
        provider: str,
        operation_id: str,
        result: BookingResult,
    ) -> Booking:
        booking = db.query(Booking).filter_by(conversation_id=conversation_id).one_or_none()
        if booking is None:
            booking = Booking(
                conversation_id=conversation_id,
                lead_id=lead_id,
                status="confirmed",
                starts_at=result.slot.starts_at,
                ends_at=result.slot.ends_at,
                timezone="America/Recife",
                provider=provider,
                provider_event_id=result.provider_event_id,
                meeting_url=result.meeting_url,
                operation_id=operation_id,
            )
            db.add(booking)
        return booking
