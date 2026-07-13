"""enforce one active learning plan per user and goal

Revision ID: 20260710_0009
Revises: 20260710_0008
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710_0009"
down_revision = "20260710_0008"
branch_labels = None
depends_on = None


def _reconcile_duplicate_active_plans() -> None:
    learning_plans = sa.table(
        "learning_plans",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("goal_id", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )
    snapshots = sa.table(
        "learning_state_snapshots",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("goal_id", sa.String()),
        sa.column("active_plan_id", sa.String()),
        sa.column("active_plan_version", sa.Integer()),
        sa.column("generated_from", sa.JSON()),
    )
    connection = op.get_bind()
    active_plans = connection.execute(
        sa.select(
            learning_plans.c.id,
            learning_plans.c.user_id,
            learning_plans.c.goal_id,
            learning_plans.c.version,
            learning_plans.c.created_at,
        )
        .where(learning_plans.c.status == "active")
        .order_by(
            learning_plans.c.user_id,
            learning_plans.c.goal_id,
            learning_plans.c.version.desc(),
            learning_plans.c.created_at.desc(),
            learning_plans.c.id.desc(),
        )
    ).mappings()

    grouped_plans: dict[tuple[str, str], list[dict]] = {}
    for plan in active_plans:
        grouped_plans.setdefault((plan["user_id"], plan["goal_id"]), []).append(plan)

    for (user_id, goal_id), plans in grouped_plans.items():
        if len(plans) < 2:
            continue
        winner = plans[0]
        loser_ids = [plan["id"] for plan in plans[1:]]
        snapshot_rows = connection.execute(
            sa.select(snapshots.c.id, snapshots.c.generated_from).where(
                snapshots.c.user_id == user_id,
                snapshots.c.goal_id == goal_id,
            )
        ).mappings()
        for snapshot in snapshot_rows:
            generated_from = snapshot["generated_from"]
            if isinstance(generated_from, dict):
                generated_from = {**generated_from, "active_plan_id": winner["id"]}
            connection.execute(
                snapshots.update()
                .where(snapshots.c.id == snapshot["id"])
                .values(
                    active_plan_id=winner["id"],
                    active_plan_version=winner["version"],
                    generated_from=generated_from,
                )
            )
        connection.execute(
            learning_plans.update()
            .where(learning_plans.c.id.in_(loser_ids))
            .values(status="replaced")
        )


def upgrade() -> None:
    _reconcile_duplicate_active_plans()
    op.create_index(
        "uq_learning_plans_active_user_goal",
        "learning_plans",
        ["user_id", "goal_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    # Duplicate-plan reconciliation is forward-only; downgrade removes only the constraint.
    op.drop_index("uq_learning_plans_active_user_goal", table_name="learning_plans")
