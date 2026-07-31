from __future__ import annotations

from sqlalchemy import UniqueConstraint

from backend.app.models import Base


def test_thread3_persistence_contracts_are_declared() -> None:
    tables = Base.metadata.tables
    assert "user_feedback" in tables
    assert {"submission_id", "payload_hash"} <= set(tables["assessment_attempts"].columns.keys())
    assert {
        "base_plan_version",
        "expires_at",
        "risk_level",
        "requires_confirmation",
        "decision_request_id",
        "decision_payload_hash",
    } <= set(tables["plan_adjustments"].columns.keys())
    assert {"cache_hit", "truncated", "error_code"} <= set(tables["tool_calls"].columns.keys())


def test_feedback_has_user_run_unique_constraint() -> None:
    table = Base.metadata.tables["user_feedback"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("user_id", "run_id") in unique_columns
