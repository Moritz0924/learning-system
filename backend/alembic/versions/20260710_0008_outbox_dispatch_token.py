"""add claim-specific outbox dispatch token

Revision ID: 20260710_0008
Revises: 20260710_0007
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710_0008"
down_revision = "20260710_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("dispatch_token", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox_events", "dispatch_token")
