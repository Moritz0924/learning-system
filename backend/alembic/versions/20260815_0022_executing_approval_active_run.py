"""Keep an executing approved tool inside the active run lock."""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0022"
down_revision = "20260815_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_agent_runs_active_thread", table_name="agent_runs")
    op.create_index(
        "uq_agent_runs_active_thread",
        "agent_runs",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('running', 'awaiting_approval', 'executing_approval', 'cancellation_requested')"),
        postgresql_where=sa.text("status IN ('running', 'awaiting_approval', 'executing_approval', 'cancellation_requested')"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_active_thread", table_name="agent_runs")
    op.create_index(
        "uq_agent_runs_active_thread",
        "agent_runs",
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('running', 'awaiting_approval', 'cancellation_requested')"),
        postgresql_where=sa.text("status IN ('running', 'awaiting_approval', 'cancellation_requested')"),
    )
