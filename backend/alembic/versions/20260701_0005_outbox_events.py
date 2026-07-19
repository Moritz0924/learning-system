"""add outbox events

Revision ID: 20260701_0005
Revises: 20260626_0004
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260701_0005"
down_revision = "20260626_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_events_dedupe_key"),
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
