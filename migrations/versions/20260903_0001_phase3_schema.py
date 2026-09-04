"""Create Phase 3 commercial persistence schema.

Revision ID: 20260903_0001
Revises:
Create Date: 2026-09-03
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260903_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = sa.Uuid()
json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("specialty", sa.String(length=120), nullable=True),
        sa.Column("service_interest", sa.String(length=40), nullable=True),
        sa.Column("qualification_status", sa.String(length=20), nullable=True),
        sa.Column("lead_score", sa.Integer(), nullable=False),
        sa.Column("meeting_booked", sa.Boolean(), nullable=False),
        sa.Column("meeting_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lead_score >= 0 AND lead_score <= 100",
            name=op.f("ck_leads_lead_score_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    op.create_table(
        "conversations",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("lead_id", uuid_type, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_stage", sa.String(length=24), nullable=False),
        sa.Column("session_snapshot", json_type, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualified", sa.Boolean(), nullable=False),
        sa.Column("meeting_booked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_conversations_lead_id_leads")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "channel",
            "external_conversation_id",
            name=op.f("uq_conversations_external_conversation"),
        ),
    )
    op.create_index(
        "ix_conversations_lead_started",
        "conversations",
        ["lead_id", "started_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("conversation_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("delivery_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "channel",
            "external_message_id",
            name=op.f("uq_messages_external_message"),
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "position",
            name=op.f("uq_messages_message_position"),
        ),
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "evaluations",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("conversation_id", uuid_type, nullable=False),
        sa.Column("discovery_score", sa.Integer(), nullable=False),
        sa.Column("qualification_score", sa.Integer(), nullable=False),
        sa.Column("objection_score", sa.Integer(), nullable=False),
        sa.Column("cta_score", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("critical_violation", sa.Boolean(), nullable=False),
        sa.Column("main_failure", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discovery_score >= 0 AND discovery_score <= 10",
            name=op.f("ck_evaluations_discovery_score_range"),
        ),
        sa.CheckConstraint(
            "qualification_score >= 0 AND qualification_score <= 10",
            name=op.f("ck_evaluations_qualification_score_range"),
        ),
        sa.CheckConstraint(
            "objection_score >= 0 AND objection_score <= 10",
            name=op.f("ck_evaluations_objection_score_range"),
        ),
        sa.CheckConstraint(
            "cta_score >= 0 AND cta_score <= 10",
            name=op.f("ck_evaluations_cta_score_range"),
        ),
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 10",
            name=op.f("ck_evaluations_overall_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_evaluations_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
    )


def downgrade() -> None:
    op.drop_table("evaluations")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_lead_started", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_leads_created_at", table_name="leads")
    op.drop_table("leads")
