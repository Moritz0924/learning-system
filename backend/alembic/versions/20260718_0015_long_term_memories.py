"""add long-term memory persistence schema

Revision ID: 20260718_0015
Revises: 20260718_0014
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_0015"
down_revision = "20260718_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learning_goals") as batch:
        batch.create_unique_constraint("uq_learning_goals_user_id_id", ["user_id", "id"])

    op.create_table(
        "memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_ref_id", sa.String(), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["user_id", "goal_id"],
            ["learning_goals.user_id", "learning_goals.id"],
            name="fk_memories_user_goal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_memories_user_idempotency"),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_memories_importance_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memories_confidence_range",
        ),
    )
    op.create_index(
        "ix_memories_user_scope_type",
        "memories",
        ["user_id", "goal_id", "memory_type"],
    )
    op.create_index(
        "ix_memories_user_enabled_expiry",
        "memories",
        ["user_id", "is_enabled", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memories_user_enabled_expiry", table_name="memories")
    op.drop_index("ix_memories_user_scope_type", table_name="memories")
    op.drop_table("memories")

    with op.batch_alter_table("learning_goals") as batch:
        batch.drop_constraint("uq_learning_goals_user_id_id", type_="unique")
