"""Add Phase 4 booking persistence.

Revision ID: 20260904_0002
Revises: 20260903_0001
Create Date: 2026-09-04
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_0002"
down_revision: str | None = "20260903_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("meeting_url", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"],
            name=op.f("fk_bookings_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_bookings_lead_id_leads")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.UniqueConstraint("conversation_id", name="uq_bookings_conversation_id"),
        sa.UniqueConstraint("operation_id", name="uq_bookings_operation_id"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_bookings_provider_event"
        ),
    )


def downgrade() -> None:
    op.drop_table("bookings")
