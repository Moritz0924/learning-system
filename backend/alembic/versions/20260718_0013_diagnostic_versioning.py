"""add versioned idempotent baseline diagnostics

Revision ID: 20260718_0013
Revises: 20260716_0012
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_0013"
down_revision = "20260716_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("baseline_diagnostics") as batch:
        batch.add_column(sa.Column("request_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("template_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("template_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("score_breakdown", sa.JSON(), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE baseline_diagnostics
            SET template_version = 'legacy_unversioned', score_breakdown = '{}'
            WHERE template_version IS NULL OR score_breakdown IS NULL
            """
        )
    )

    with op.batch_alter_table("baseline_diagnostics") as batch:
        batch.alter_column(
            "template_version",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.alter_column(
            "score_breakdown",
            existing_type=sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        )

    op.create_index(
        "uq_baseline_diagnostics_user_request_id",
        "baseline_diagnostics",
        ["user_id", "request_id"],
        unique=True,
        sqlite_where=sa.text("request_id IS NOT NULL"),
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_baseline_diagnostics_user_request_id",
        table_name="baseline_diagnostics",
    )
    with op.batch_alter_table("baseline_diagnostics") as batch:
        batch.drop_column("score_breakdown")
        batch.drop_column("template_hash")
        batch.drop_column("template_version")
        batch.drop_column("request_id")
