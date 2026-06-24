"""stage 1 core learning loop tables

Revision ID: 20260621_0001
Revises:
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260621_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "learner_profiles",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("weekly_hours", sa.Integer(), nullable=False),
        sa.Column("available_slots", sa.JSON(), nullable=False),
        sa.Column("learning_preferences", sa.JSON(), nullable=False),
        sa.Column("baseline_notes", sa.Text(), nullable=True),
        sa.Column("privacy_settings", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "curricula",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("curriculum_id", sa.String(), sa.ForeignKey("curricula.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("mastery_threshold", sa.Float(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("curriculum_id", sa.String(), sa.ForeignKey("curricula.id"), nullable=False),
        sa.Column("from_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("to_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("relation_type", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "curriculum_id",
            "from_node_id",
            "to_node_id",
            "relation_type",
            name="uq_knowledge_edges_relation",
        ),
    )
    op.create_table(
        "learning_goals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("target_outcome", sa.Text(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("weekly_hours_target", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("learning_preferences", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learning_goals_user_id", "learning_goals", ["user_id"])
    op.create_table(
        "baseline_diagnostics",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("submitted_answers", sa.JSON(), nullable=False),
        sa.Column("baseline_summary", sa.Text(), nullable=False),
        sa.Column("entry_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=True),
        sa.Column("knowledge_gaps", sa.JSON(), nullable=False),
        sa.Column("initial_mastery", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_baseline_diagnostics_user_id", "baseline_diagnostics", ["user_id"])
    op.create_index("ix_baseline_diagnostics_goal_id", "baseline_diagnostics", ["goal_id"])
    op.create_table(
        "learning_plans",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("curriculum_id", sa.String(), sa.ForeignKey("curricula.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("generated_by", sa.String(), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "goal_id", "version", name="uq_learning_plans_user_goal_version"),
    )
    op.create_index("ix_learning_plans_user_id", "learning_plans", ["user_id"])
    op.create_index("ix_learning_plans_goal_id", "learning_plans", ["goal_id"])
    op.create_table(
        "plan_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("knowledge_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("knowledge_node_code", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("scheduled_day", sa.Integer(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
    )
    op.create_index("ix_plan_tasks_plan_id", "plan_tasks", ["plan_id"])
    op.create_index("ix_plan_tasks_user_id", "plan_tasks", ["user_id"])
    op.create_index("ix_plan_tasks_goal_id", "plan_tasks", ["goal_id"])
    op.create_index("ix_plan_tasks_scheduled_date", "plan_tasks", ["scheduled_date"])
    op.create_table(
        "mastery_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("knowledge_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("mastery_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("source_breakdown", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "goal_id", "knowledge_node_id", name="uq_mastery_user_goal_node"),
    )
    op.create_index("ix_mastery_records_user_id", "mastery_records", ["user_id"])
    op.create_index("ix_mastery_records_goal_id", "mastery_records", ["goal_id"])
    op.create_table(
        "learning_state_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("active_plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=False),
        sa.Column("active_plan_version", sa.Integer(), nullable=False),
        sa.Column("baseline_diagnostic_id", sa.String(), sa.ForeignKey("baseline_diagnostics.id"), nullable=False),
        sa.Column("phase_assessment_state_id", sa.String(), nullable=True),
        sa.Column("latest_plan_adjustment_id", sa.String(), nullable=True),
        sa.Column("mastery_summary", sa.JSON(), nullable=False),
        sa.Column("current_state", sa.JSON(), nullable=False),
        sa.Column("generated_from", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "goal_id", name="uq_learning_state_snapshots_user_goal"),
    )
    op.create_index("ix_learning_state_snapshots_user_id", "learning_state_snapshots", ["user_id"])
    op.create_index("ix_learning_state_snapshots_goal_id", "learning_state_snapshots", ["goal_id"])


def downgrade() -> None:
    op.drop_table("learning_state_snapshots")
    op.drop_table("mastery_records")
    op.drop_table("plan_tasks")
    op.drop_table("learning_plans")
    op.drop_table("baseline_diagnostics")
    op.drop_table("learning_goals")
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
    op.drop_table("curricula")
    op.drop_table("learner_profiles")
    op.drop_table("users")
