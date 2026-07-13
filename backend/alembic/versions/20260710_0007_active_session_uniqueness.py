"""enforce one active learning session per user and task

Revision ID: 20260710_0007
Revises: 20260702_0006
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710_0007"
down_revision = "20260702_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE learning_sessions
            SET status = 'superseded',
                ended_at = COALESCE(ended_at, CURRENT_TIMESTAMP)
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id, task_id
                               ORDER BY started_at DESC, id DESC
                           ) AS duplicate_rank
                    FROM learning_sessions
                    WHERE status = 'active'
                ) AS ranked_sessions
                WHERE duplicate_rank > 1
            )
            """
        )
    )
    op.create_index(
        "uq_learning_sessions_active_user_task",
        "learning_sessions",
        ["user_id", "task_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    # Duplicate-session reconciliation is forward-only; downgrade removes only the constraint.
    op.drop_index("uq_learning_sessions_active_user_task", table_name="learning_sessions")
