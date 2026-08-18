"""add assessment v2 metadata and idempotency persistence

Revision ID: 20260720_0016
Revises: 20260718_0015
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_0016"
down_revision = "20260718_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("generation_request_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("generation_input_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("schema_version", sa.String(length=32), nullable=False, server_default="assessment-v1"))
        batch.add_column(sa.Column("generation_mode", sa.String(length=32), nullable=False, server_default="legacy_rule"))
        batch.add_column(sa.Column("generator_version", sa.String(length=64), nullable=False, server_default="phase2-v1"))
        batch.add_column(sa.Column("generator_model", sa.String(), nullable=True))
        batch.add_column(sa.Column("generation_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index(
        "uq_assessments_user_generation_request",
        "assessments",
        ["user_id", "generation_request_id"],
        unique=True,
        sqlite_where=sa.text("generation_request_id IS NOT NULL"),
        postgresql_where=sa.text("generation_request_id IS NOT NULL"),
    )

    with op.batch_alter_table("assessment_attempts") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column("request_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("answer_payload_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("submitted_answers_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("grader_mode", sa.String(length=32), nullable=False, server_default="legacy_rule"))
        batch.add_column(sa.Column("grader_version", sa.String(length=64), nullable=False, server_default="phase2-rubric-v1"))
        batch.add_column(sa.Column("grader_model", sa.String(), nullable=True))
        batch.add_column(sa.Column("grading_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("grading_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("claim_token", sa.String(), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("error_code", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_assessment_attempts_user_assessment_request",
        "assessment_attempts",
        ["user_id", "assessment_id", "request_id"],
        unique=True,
        sqlite_where=sa.text("request_id IS NOT NULL"),
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )

    with op.batch_alter_table("assessment_answers") as batch:
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("mastery_records") as batch:
        batch.add_column(sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="phase2-mastery-v1"))
        batch.add_column(sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("plan_adjustments") as batch:
        batch.add_column(sa.Column("policy_version", sa.String(length=64), nullable=False, server_default="phase2-observer-v1"))
        batch.add_column(sa.Column("automation_allowed", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_index("uq_assessment_attempts_user_assessment_request", table_name="assessment_attempts")
    op.drop_index("uq_assessments_user_generation_request", table_name="assessments")

    with op.batch_alter_table("plan_adjustments") as batch:
        batch.drop_column("automation_allowed")
        batch.drop_column("policy_version")

    with op.batch_alter_table("mastery_records") as batch:
        batch.drop_column("last_evidence_at")
        batch.drop_column("calculation_version")

    with op.batch_alter_table("assessment_answers") as batch:
        batch.drop_column("needs_review")
        batch.drop_column("confidence")

    with op.batch_alter_table("assessment_attempts") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("error_code")
        batch.drop_column("attempt_count")
        batch.drop_column("lease_expires_at")
        batch.drop_column("claim_token")
        batch.drop_column("grading_metadata")
        batch.drop_column("grading_confidence")
        batch.drop_column("grader_model")
        batch.drop_column("grader_version")
        batch.drop_column("grader_mode")
        batch.drop_column("submitted_answers_json")
        batch.drop_column("answer_payload_hash")
        batch.drop_column("request_id")
        batch.alter_column("score", existing_type=sa.Float(), nullable=False)

    with op.batch_alter_table("assessments") as batch:
        batch.drop_column("generation_metadata")
        batch.drop_column("generator_model")
        batch.drop_column("generator_version")
        batch.drop_column("generation_mode")
        batch.drop_column("schema_version")
        batch.drop_column("generation_input_hash")
        batch.drop_column("generation_request_id")
