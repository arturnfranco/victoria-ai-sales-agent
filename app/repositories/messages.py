"""Message persistence operations."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Message
from app.schemas import ConversationStage, MessageRole


class MessageRepository:
    def add(
        self,
        db: Session,
        *,
        conversation_id: uuid.UUID,
        position: int,
        role: MessageRole,
        content: str,
        stage: ConversationStage,
        channel: str,
        external_message_id: str | None = None,
        delivery_status: str | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            position=position,
            external_message_id=external_message_id,
            role=role.value,
            content=content,
            stage=stage.value,
            channel=channel,
            delivery_status=delivery_status,
        )
        db.add(message)
        return message

    def list_for_conversation(
        self, db: Session, conversation_id: uuid.UUID
    ) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position)
        )
        return list(db.scalars(statement))
