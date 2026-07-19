"""add document processing metadata

Revision ID: 20260718_0014
Revises: 20260718_0013
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_0014"
down_revision = "20260718_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("size_bytes", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("parse_error_code", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("page_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("block_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("parser_version", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("processing_completed_at")
        batch.drop_column("processing_started_at")
        batch.drop_column("parser_version")
        batch.drop_column("block_count")
        batch.drop_column("page_count")
        batch.drop_column("parse_error_code")
        batch.drop_column("size_bytes")
