"""add document parse error

Revision ID: 20260702_0006
Revises: 20260701_0005
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0006"
down_revision = "20260701_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("parse_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "parse_error")
