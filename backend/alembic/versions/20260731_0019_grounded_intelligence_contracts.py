"""additive T3 grounded-intelligence contracts and feedback storage

Revision ID: 20260731_0019
Revises: 20260730_0018
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260731_0019"
down_revision = "20260730_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessment_attempts", sa.Column("submission_id", sa.String(), nullable=True))
    op.add_column("assessment_attempts", sa.Column("payload_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "assessment_attempts",
        sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.execute(sa.text("UPDATE assessment_attempts SET submission_id = id WHERE submission_id IS NULL"))
    op.execute(sa.text("UPDATE assessment_attempts SET payload_hash = '' WHERE payload_hash IS NULL"))
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assessment_attempts", recreate="always") as batch:
            batch.alter_column("submission_id", existing_type=sa.String(), nullable=False)
            batch.alter_column("payload_hash", existing_type=sa.String(length=64), nullable=False)
            batch.create_unique_constraint(
                "uq_assessment_submission_idempotency",
                ["user_id", "assessment_id", "submission_id"],
            )
    else:
        op.alter_column("assessment_attempts", "submission_id", nullable=False)
        op.alter_column("assessment_attempts", "payload_hash", nullable=False)
        op.create_unique_constraint(
            "uq_assessment_submission_idempotency",
            "assessment_attempts",
            ["user_id", "assessment_id", "submission_id"],
        )

    for name, column in (
        ("base_plan_version", sa.Column("base_plan_version", sa.Integer(), nullable=True)),
        ("expires_at", sa.Column("expires_at", sa.DateTime(), nullable=True)),
        ("risk_level", sa.Column("risk_level", sa.String(), nullable=False, server_default="low")),
        (
            "requires_confirmation",
            sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        ("decision_request_id", sa.Column("decision_request_id", sa.String(), nullable=True)),
        ("decision_payload_hash", sa.Column("decision_payload_hash", sa.String(length=64), nullable=True)),
    ):
        op.add_column("plan_adjustments", column)

    op.add_column("tool_calls", sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tool_calls", sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tool_calls", sa.Column("error_code", sa.String(), nullable=True))

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("citation_correct", sa.Boolean(), nullable=True),
        sa.Column("difficulty_fit", sa.Boolean(), nullable=True),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("optional_comment", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_status", sa.String(), nullable=False, server_default="pending_review"),
        sa.Column("sanitized_case_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("dataset_version", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "run_id", name="uq_user_feedback_user_run"),
    )


def downgrade() -> None:
    op.drop_table("user_feedback")
    op.drop_column("tool_calls", "error_code")
    op.drop_column("tool_calls", "truncated")
    op.drop_column("tool_calls", "cache_hit")
    for name in (
        "decision_payload_hash",
        "decision_request_id",
        "requires_confirmation",
        "risk_level",
        "expires_at",
        "base_plan_version",
    ):
        op.drop_column("plan_adjustments", name)
    op.drop_column("assessment_attempts", "result_json")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assessment_attempts", recreate="always") as batch:
            batch.drop_constraint("uq_assessment_submission_idempotency", type_="unique")
            batch.drop_column("payload_hash")
            batch.drop_column("submission_id")
    else:
        op.drop_constraint("uq_assessment_submission_idempotency", "assessment_attempts", type_="unique")
        op.drop_column("assessment_attempts", "payload_hash")
        op.drop_column("assessment_attempts", "submission_id")
