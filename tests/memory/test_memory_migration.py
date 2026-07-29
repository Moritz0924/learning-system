from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


REQUIRED_COLUMNS = {
    "id": False,
    "user_id": False,
    "goal_id": True,
    "memory_type": False,
    "schema_version": False,
    "content_json": False,
    "content_hash": False,
    "source_kind": False,
    "source_ref_id": True,
    "source_metadata": False,
    "importance": False,
    "confidence": False,
    "is_enabled": False,
    "expires_at": True,
    "disabled_at": True,
    "disabled_reason": True,
    "idempotency_key": False,
    "created_at": False,
    "updated_at": False,
}


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_user(connection, user_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO users (
                id, email, display_name, status, created_at, normalized_email, role, token_version
            ) VALUES (
                :id, :email, 'Memory User', 'active', :created_at, :email, 'learner', 1
            )
            """
        ),
        {
            "id": user_id,
            "email": f"{user_id}@example.com",
            "created_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        },
    )


def test_memory_migration_schema_and_lifecycle(tmp_path) -> None:
    db_path = tmp_path / "memory-migration.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = _config(database_url)

    upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert "memories" in inspector.get_table_names()
    columns = {column["name"]: column for column in inspector.get_columns("memories")}
    assert {name: columns[name]["nullable"] for name in REQUIRED_COLUMNS} == REQUIRED_COLUMNS

    memory_unique_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("memories")
    }
    assert "uq_memories_user_idempotency" in memory_unique_names
    goal_unique_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("learning_goals")
    }
    assert "uq_learning_goals_user_id_id" in goal_unique_names

    memory_index_names = {index["name"] for index in inspector.get_indexes("memories")}
    assert {"ix_memories_user_scope_type", "ix_memories_user_enabled_expiry"} <= memory_index_names

    check_constraints = inspector.get_check_constraints("memories")
    if check_constraints:
        assert {"ck_memories_importance_range", "ck_memories_confidence_range"} <= {
            constraint["name"] for constraint in check_constraints
        }

    composite_foreign_key = next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys("memories")
        if foreign_key["name"] == "fk_memories_user_goal"
    )
    assert composite_foreign_key["constrained_columns"] == ["user_id", "goal_id"]
    assert composite_foreign_key["referred_columns"] == ["user_id", "id"]

    downgrade(config, "20260718_0014")
    downgraded_inspector = inspect(engine)
    assert "memories" not in downgraded_inspector.get_table_names()
    assert "uq_learning_goals_user_id_id" not in {
        constraint["name"]
        for constraint in downgraded_inspector.get_unique_constraints("learning_goals")
    }

    upgrade(config, "head")
    reupgraded_inspector = inspect(engine)
    assert "memories" in reupgraded_inspector.get_table_names()
    assert "uq_learning_goals_user_id_id" in {
        constraint["name"]
        for constraint in reupgraded_inspector.get_unique_constraints("learning_goals")
    }
    engine.dispose()


def test_memory_migration_rejects_cross_user_goal_scope(tmp_path) -> None:
    db_path = tmp_path / "memory-scope.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = _config(database_url)
    upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.commit()
        with connection.begin():
            _insert_user(connection, "memory-user-a")
            _insert_user(connection, "memory-user-b")
            connection.execute(
                text(
                    """
                    INSERT INTO learning_goals (
                        id, user_id, title, domain, target_outcome, deadline,
                        weekly_hours_target, status, learning_preferences, created_at
                    ) VALUES (
                        'memory-goal-b', 'memory-user-b', 'Goal', 'ai_app_dev', 'Outcome', NULL,
                        5, 'active', '{}', :created_at
                    )
                    """
                ),
                {"created_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)},
            )

        with pytest.raises(IntegrityError):
            with connection.begin():
                connection.execute(
                    text(
                        """
                        INSERT INTO memories (
                            id, user_id, goal_id, memory_type, schema_version, content_json,
                            content_hash, source_kind, source_ref_id, source_metadata, importance,
                            confidence, is_enabled, expires_at, disabled_at, disabled_reason,
                            idempotency_key, created_at, updated_at
                        ) VALUES (
                            'memory-cross-user', 'memory-user-a', 'memory-goal-b',
                            'learning_preference', 'memory-v1', '{}', 'hash', 'explicit_user',
                            NULL, '{}', 0.5, 0.5, 1, NULL, NULL, NULL, 'scope-test',
                            :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "created_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
                        "updated_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
                    },
                )
    engine.dispose()


def test_versioned_document_index_migration_is_the_only_head() -> None:
    config = _config("sqlite+pysqlite:///:memory:")

    assert ScriptDirectory.from_config(config).get_heads() == ["20260729_0017"]
