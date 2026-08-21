from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect


REQUIRED_COLUMNS = {
    "assessments": {
        "generation_request_id",
        "generation_input_hash",
        "schema_version",
        "generation_mode",
        "generator_version",
        "generation_metadata",
    },
    "assessment_attempts": {
        "request_id",
        "answer_payload_hash",
        "submitted_answers_json",
        "grader_mode",
        "grading_metadata",
        "claim_token",
        "lease_expires_at",
        "attempt_count",
        "completed_at",
    },
    "assessment_answers": {"confidence", "needs_review"},
    "mastery_records": {"calculation_version", "last_evidence_at"},
    "plan_adjustments": {"policy_version", "automation_allowed"},
}
REQUIRED_INDEXES = {
    "uq_assessments_user_generation_request",
    "uq_assessment_attempts_user_assessment_request",
}


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            schema = inspect(connection)
            for table, required in REQUIRED_COLUMNS.items():
                actual = {column["name"] for column in schema.get_columns(table)}
                missing = required - actual
                if missing:
                    raise AssertionError(f"{table} missing Assessment V2 columns: {sorted(missing)!r}")
            indexes = {
                row["indexname"]
                for row in connection.exec_driver_sql(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
                ).mappings()
            }
            missing_indexes = REQUIRED_INDEXES - indexes
            if missing_indexes:
                raise AssertionError(f"missing Assessment V2 indexes: {sorted(missing_indexes)!r}")
    finally:
        engine.dispose()

    print("postgres assessment-v2 schema verification: passed")


if __name__ == "__main__":
    main()
