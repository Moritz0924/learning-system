from __future__ import annotations

from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_document_goal_scope_migration_round_trips_and_preserves_legacy_rows(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'document-goal-scope.db'}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260821_0025")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, normalized_email, display_name, status, role, token_version, created_at) "
                "VALUES ('user-migration', 'migration@example.test', 'migration@example.test', "
                "'Migration', 'active', 'learner', 1, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO learning_goals "
                "(id, user_id, title, domain, target_outcome, weekly_hours_target, status, "
                "learning_preferences, created_at) VALUES "
                "('goal-migration', 'user-migration', 'Goal', 'ai_app_dev', 'Outcome', 4, "
                "'active', '{}', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, owner_user_id, corpus_type, filename, object_key, mime_type, "
                "parse_status, sha256, trusted_level, created_at) VALUES "
                "('doc-legacy', 'user-migration', 'user_uploaded', 'legacy.txt', "
                "'legacy/doc.txt', 'text/plain', 'success', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1, "
                "CURRENT_TIMESTAMP)"
            )
        )

    upgrade(config, "20260831_0026")
    schema = inspect(engine)
    assert "goal_id" in {column["name"] for column in schema.get_columns("documents")}
    assert "ix_documents_owner_goal_created" in {
        index["name"] for index in schema.get_indexes("documents")
    }
    assert "fk_documents_owner_goal" in {
        constraint["name"] for constraint in schema.get_foreign_keys("documents")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT goal_id FROM documents WHERE id = 'doc-legacy'")
        ) is None

    downgrade(config, "20260821_0025")
    assert "goal_id" not in {
        column["name"] for column in inspect(engine).get_columns("documents")
    }
    upgrade(config, "20260831_0026")
    assert "goal_id" in {
        column["name"] for column in inspect(engine).get_columns("documents")
    }
    engine.dispose()
