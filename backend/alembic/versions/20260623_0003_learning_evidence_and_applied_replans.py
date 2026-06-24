"""learning evidence and applied replans

Revision ID: 20260623_0003
Revises: 20260621_0002
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0003"
down_revision = "20260621_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("plan_tasks.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_learning_sessions_user_goal", "learning_sessions", ["user_id", "goal_id"])
    op.create_index("ix_learning_sessions_task_status", "learning_sessions", ["task_id", "status"])

    op.create_table(
        "learning_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("session_id", sa.String(), sa.ForeignKey("learning_sessions.id"), nullable=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("plan_tasks.id"), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event_payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learning_events_user_goal", "learning_events", ["user_id", "goal_id"])
    op.create_index("ix_learning_events_task_id", "learning_events", ["task_id"])
    op.create_index("ix_learning_events_event_type", "learning_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("learning_events")
    op.drop_table("learning_sessions")
