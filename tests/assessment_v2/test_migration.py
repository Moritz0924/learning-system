from __future__ import annotations

from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_assessment_v2_migration_adds_metadata_and_downgrades(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'assessment-v2.db'}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)

    upgrade(config, "20260718_0015")
    upgrade(config, "head")

    engine = create_engine(database_url)
    schema = inspect(engine)
    assert {
        "generation_request_id",
        "generation_input_hash",
        "schema_version",
        "generation_mode",
        "generator_version",
        "generator_model",
        "generation_metadata",
    } <= {column["name"] for column in schema.get_columns("assessments")}
    assert {
        "request_id",
        "answer_payload_hash",
        "submitted_answers_json",
        "grader_mode",
        "claim_token",
        "lease_expires_at",
        "attempt_count",
        "completed_at",
    } <= {column["name"] for column in schema.get_columns("assessment_attempts")}
    assert "uq_assessments_user_generation_request" in {
        index["name"] for index in schema.get_indexes("assessments")
    }

    downgrade(config, "20260718_0015")
    schema = inspect(engine)
    assert "generation_request_id" not in {column["name"] for column in schema.get_columns("assessments")}
    engine.dispose()
