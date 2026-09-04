"""Relational persistence model for VictorIA's commercial data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "lead_score >= 0 AND lead_score <= 100", name="lead_score_range"
        ),
        Index("ix_leads_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320))
    phone_number: Mapped[str | None] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    specialty: Mapped[str | None] = mapped_column(String(120))
    service_interest: Mapped[str | None] = mapped_column(String(40))
    qualification_status: Mapped[str | None] = mapped_column(String(20))
    lead_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meeting_booked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    meeting_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="lead")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "external_conversation_id",
            name="uq_conversations_external_conversation",
        ),
        Index("ix_conversations_lead_started", "lead_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_conversation_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    current_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    session_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qualified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    meeting_booked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="conversation"
    )
    booking: Mapped["Booking | None"] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "position",
            name="uq_messages_message_position",
        ),
        UniqueConstraint(
            "channel",
            "external_message_id",
            name="uq_messages_external_message",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_bookings_conversation_id"),
        UniqueConstraint("operation_id", name="uq_bookings_operation_id"),
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_bookings_provider_event"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    meeting_url: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="booking")


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(
            "discovery_score >= 0 AND discovery_score <= 10",
            name="discovery_score_range",
        ),
        CheckConstraint(
            "qualification_score >= 0 AND qualification_score <= 10",
            name="qualification_score_range",
        ),
        CheckConstraint(
            "objection_score >= 0 AND objection_score <= 10",
            name="objection_score_range",
        ),
        CheckConstraint(
            "cta_score >= 0 AND cta_score <= 10", name="cta_score_range"
        ),
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 10",
            name="overall_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    discovery_score: Mapped[int] = mapped_column(Integer, nullable=False)
    qualification_score: Mapped[int] = mapped_column(Integer, nullable=False)
    objection_score: Mapped[int] = mapped_column(Integer, nullable=False)
    cta_score: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_score: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    critical_violation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    main_failure: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="evaluations")
