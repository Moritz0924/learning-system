"""add private dynamic diagnostic roadmaps

Revision ID: 20260819_0023
Revises: 20260815_0022, 20260818_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_0023"
down_revision = ("20260815_0022", "20260818_0022")
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("curricula") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_curricula_owner_user_id_users",
            "users",
            ["owner_user_id"],
            ["id"],
        )
    op.create_table(
        "user_diagnostic_drafts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("locale", sa.String(16), nullable=False),
        sa.Column("goal_input", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("public_questions", sa.JSON(), nullable=False),
        sa.Column("scoring_key", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "request_id",
            name="uq_user_diagnostic_drafts_user_request",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_diagnostic_drafts")
    with op.batch_alter_table("curricula") as batch_op:
        batch_op.drop_constraint("fk_curricula_owner_user_id_users", type_="foreignkey")
        batch_op.drop_column("owner_user_id")
