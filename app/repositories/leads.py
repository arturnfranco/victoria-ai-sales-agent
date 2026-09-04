"""Lead persistence operations."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lead


class LeadRepository:
    def create(
        self,
        db: Session,
        *,
        name: str | None,
        email: str | None,
        phone_number: str | None,
        channel: str,
    ) -> Lead:
        lead = Lead(
            name=name,
            email=email,
            phone_number=phone_number,
            channel=channel,
        )
        db.add(lead)
        db.flush()
        return lead

    def get(self, db: Session, lead_id: uuid.UUID) -> Lead | None:
        return db.get(Lead, lead_id)

    def list(self, db: Session) -> list[Lead]:
        statement = select(Lead).order_by(Lead.created_at.desc(), Lead.id)
        return list(db.scalars(statement))

    def update_qualification(
        self,
        lead: Lead,
        *,
        service_interest: str | None,
        qualification_status: str,
        lead_score: int,
    ) -> None:
        lead.service_interest = service_interest
        lead.qualification_status = qualification_status
        lead.lead_score = lead_score
