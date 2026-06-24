"""stage 3 learning experience and gateway tables

Revision ID: 20260621_0002
Revises: 20260621_0001
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260621_0002"
down_revision = "20260621_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=True),
        sa.Column("assessment_type", sa.String(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("rubric_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "assessment_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("assessment_id", sa.String(), sa.ForeignKey("assessments.id"), nullable=False),
        sa.Column("knowledge_node_id", sa.String(), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("question_type", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("rubric_json", sa.JSON(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("source_chunk_ids", sa.JSON(), nullable=False),
    )
    op.create_table(
        "assessment_attempts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("assessment_id", sa.String(), sa.ForeignKey("assessments.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
    )
    op.create_table(
        "assessment_answers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("attempt_id", sa.String(), sa.ForeignKey("assessment_attempts.id"), nullable=False),
        sa.Column("item_id", sa.String(), sa.ForeignKey("assessment_items.id"), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("answer_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("grader_type", sa.String(), nullable=False),
        sa.Column("grader_reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "plan_adjustments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("previous_plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=True),
        sa.Column("new_plan_id", sa.String(), sa.ForeignKey("learning_plans.id"), nullable=True),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan_patch", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "phase_assessment_states",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("learning_goals.id"), nullable=False),
        sa.Column("assessment_id", sa.String(), sa.ForeignKey("assessments.id"), nullable=True),
        sa.Column("phase_code", sa.String(), nullable=False),
        sa.Column("covered_node_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("readiness_score", sa.Float(), nullable=False),
        sa.Column("last_result_json", sa.JSON(), nullable=False),
        sa.Column("next_action", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "goal_id", "phase_code", name="uq_phase_assessment_user_goal_phase"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("corpus_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("parse_status", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("trusted_level", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("citation_label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("graph_name", sa.String(), nullable=False),
        sa.Column("graph_version", sa.String(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_run_id", sa.String(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_summary", sa.JSON(), nullable=False),
        sa.Column("source_urls", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("agent_runs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("phase_assessment_states")
    op.drop_table("plan_adjustments")
    op.drop_table("assessment_answers")
    op.drop_table("assessment_attempts")
    op.drop_table("assessment_items")
    op.drop_table("assessments")
