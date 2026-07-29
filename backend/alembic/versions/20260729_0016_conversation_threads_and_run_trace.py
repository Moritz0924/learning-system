"""add application conversation threads and agent run trace fields

Revision ID: 20260729_0016
Revises: 20260718_0015
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0016"
down_revision = "20260718_0015"
branch_labels = None
depends_on = None


def _started_at_backfill_sql(dialect_name: str) -> str:
    if dialect_name == "postgresql":
        return (
            "UPDATE agent_runs SET started_at = created_at AT TIME ZONE 'UTC' "
            "WHERE started_at IS NULL"
        )
    return "UPDATE agent_runs SET started_at = created_at WHERE started_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        sa.Column("legacy_key", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_conversation_threads_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["user_id", "goal_id"],
            ["learning_goals.user_id", "learning_goals.id"],
            name="fk_conversation_threads_user_goal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "goal_id",
            "id",
            name="uq_conversation_threads_user_goal_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "goal_id",
            "legacy_key",
            name="uq_conversation_threads_user_goal_legacy_key",
        ),
    )
    op.create_index(
        "ix_conversation_threads_user_goal_status",
        "conversation_threads",
        ["user_id", "goal_id", "status", "updated_at"],
    )

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("goal_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("request_hash", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "node_trace",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_agent_runs_conversation_thread",
            "conversation_threads",
            ["user_id", "goal_id", "thread_id"],
            ["user_id", "goal_id", "id"],
        )
        batch.create_unique_constraint(
            "uq_agent_runs_correlation_id", ["correlation_id"]
        )

    bind = op.get_bind()
    op.execute(_started_at_backfill_sql(bind.dialect.name))
    with op.batch_alter_table("agent_runs") as batch:
        batch.alter_column(
            "started_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.create_index(
        "ix_agent_runs_user_thread_created",
        "agent_runs",
        ["user_id", "thread_id", "created_at"],
    )
    op.create_index(
        "uq_agent_runs_active_thread",
        "agent_runs",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('running', 'cancellation_requested')"),
        postgresql_where=sa.text("status IN ('running', 'cancellation_requested')"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_active_thread", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_thread_created", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("uq_agent_runs_correlation_id", type_="unique")
        batch.drop_constraint("fk_agent_runs_conversation_thread", type_="foreignkey")
        batch.drop_column("cancelled_at")
        batch.drop_column("cancel_requested_at")
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("node_trace")
        batch.drop_column("request_hash")
        batch.drop_column("correlation_id")
        batch.drop_column("goal_id")

    op.drop_index(
        "ix_conversation_threads_user_goal_status", table_name="conversation_threads"
    )
    op.drop_table("conversation_threads")
