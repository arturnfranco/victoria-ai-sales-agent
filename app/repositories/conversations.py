"""Conversation persistence operations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation
from app.schemas import ConversationSession, ConversationStage, FitLevel


class ConversationRepository:
    def create(
        self,
        db: Session,
        *,
        lead_id: uuid.UUID,
        channel: str,
        prompt_version: str,
        session: ConversationSession,
        external_conversation_id: str | None = None,
    ) -> Conversation:
        conversation = Conversation(
            lead_id=lead_id,
            channel=channel,
            external_conversation_id=external_conversation_id,
            prompt_version=prompt_version,
            current_stage=session.stage.value,
            session_snapshot=session.model_dump(mode="json"),
        )
        db.add(conversation)
        db.flush()
        return conversation

    def get(self, db: Session, conversation_id: uuid.UUID) -> Conversation | None:
        return db.get(Conversation, conversation_id)

    def list(self, db: Session) -> list[Conversation]:
        statement = select(Conversation).order_by(
            Conversation.started_at.desc(), Conversation.id
        )
        return list(db.scalars(statement))

    def save_session(
        self,
        conversation: Conversation,
        session: ConversationSession,
        *,
        ended_at: datetime | None = None,
    ) -> None:
        output = session.last_output
        conversation.current_stage = session.stage.value
        conversation.session_snapshot = session.model_dump(mode="json")
        conversation.status = (
            "closed" if session.stage is ConversationStage.CLOSED else "active"
        )
        conversation.ended_at = ended_at
        conversation.qualified = bool(
            output and output.fit in {FitLevel.MEDIUM, FitLevel.HIGH}
        )
        conversation.meeting_booked = session.stage is ConversationStage.BOOKED
