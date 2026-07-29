import json

from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_alembic_migration_creates_stage1_tables(tmp_path):
    db_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{db_path}"

    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)

    upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert "learning_state_snapshots" in inspector.get_table_names()
    assert "baseline_diagnostics" in inspector.get_table_names()
    assert "knowledge_nodes" in inspector.get_table_names()
    assert "plan_tasks" in inspector.get_table_names()
    assert "assessments" in inspector.get_table_names()
    assert "assessment_items" in inspector.get_table_names()
    assert "assessment_attempts" in inspector.get_table_names()
    assert "assessment_answers" in inspector.get_table_names()
    assert "plan_adjustments" in inspector.get_table_names()
    assert "phase_assessment_states" in inspector.get_table_names()
    assert "documents" in inspector.get_table_names()
    assert "document_chunks" in inspector.get_table_names()
    assert "agent_runs" in inspector.get_table_names()
    assert "conversation_threads" in inspector.get_table_names()
    assert "tool_calls" in inspector.get_table_names()
    assert "outbox_events" in inspector.get_table_names()
    assert "learning_sessions" in inspector.get_table_names()
    assert "learning_events" in inspector.get_table_names()
    assert "memories" in inspector.get_table_names()
    assert {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }.isdisjoint(inspector.get_table_names())
    chunk_columns = {column["name"] for column in inspector.get_columns("document_chunks")}
    assert "embedding_vector" in chunk_columns
    document_columns = {column["name"] for column in inspector.get_columns("documents")}
    assert "parse_error" in document_columns
    diagnostic_columns = {
        column["name"]: column for column in inspector.get_columns("baseline_diagnostics")
    }
    assert diagnostic_columns["template_version"]["nullable"] is False
    assert diagnostic_columns["score_breakdown"]["nullable"] is False
    assert {"request_id", "template_hash"} <= diagnostic_columns.keys()
    diagnostic_index_names = {
        item["name"] for item in inspector.get_indexes("baseline_diagnostics")
    }
    assert "uq_baseline_diagnostics_user_request_id" in diagnostic_index_names
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert user_columns["normalized_email"]["nullable"] is False
    assert "password_hash" in user_columns
    assert "auth_sessions" in inspector.get_table_names()
    assert "refresh_tokens" in inspector.get_table_names()
    outbox_columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    assert {
        "event_type",
        "dedupe_key",
        "payload_json",
        "status",
        "attempts",
        "available_at",
        "dispatch_token",
    } <= outbox_columns

    indexes = inspector.get_indexes("learning_state_snapshots")
    constraints = inspector.get_unique_constraints("learning_state_snapshots")
    unique_names = {item["name"] for item in indexes + constraints}
    assert "uq_learning_state_snapshots_user_goal" in unique_names

    memory_unique_names = {item["name"] for item in inspector.get_unique_constraints("memories")}
    assert "uq_memories_user_idempotency" in memory_unique_names
    memory_index_names = {item["name"] for item in inspector.get_indexes("memories")}
    assert {"ix_memories_user_scope_type", "ix_memories_user_enabled_expiry"} <= memory_index_names
    memory_check_names = {item["name"] for item in inspector.get_check_constraints("memories")}
    assert {"ck_memories_importance_range", "ck_memories_confidence_range"} <= memory_check_names
    memory_foreign_key_names = {item["name"] for item in inspector.get_foreign_keys("memories")}
    assert "fk_memories_user_goal" in memory_foreign_key_names
    learning_goal_unique_names = {
        item["name"] for item in inspector.get_unique_constraints("learning_goals")
    }
    assert "uq_learning_goals_user_id_id" in learning_goal_unique_names

    thread_columns = {
        column["name"] for column in inspector.get_columns("conversation_threads")
    }
    assert {
        "id",
        "user_id",
        "goal_id",
        "legacy_key",
        "title",
        "status",
        "created_at",
        "updated_at",
        "archived_at",
    } <= thread_columns
    thread_unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints("conversation_threads")
    }
    assert "uq_conversation_threads_user_goal_id" in thread_unique_names
    assert "uq_conversation_threads_user_goal_legacy_key" in thread_unique_names
    thread_foreign_key_names = {
        item["name"] for item in inspector.get_foreign_keys("conversation_threads")
    }
    assert "fk_conversation_threads_user_goal" in thread_foreign_key_names

    inspected_run_columns = inspector.get_columns("agent_runs")
    run_columns = {column["name"] for column in inspected_run_columns}
    assert {
        "goal_id",
        "correlation_id",
        "request_hash",
        "node_trace",
        "started_at",
        "completed_at",
        "cancel_requested_at",
        "cancelled_at",
    } <= run_columns
    assert next(
        item for item in inspected_run_columns if item["name"] == "started_at"
    )["nullable"] is False
    run_index_names = {item["name"] for item in inspector.get_indexes("agent_runs")}
    assert "uq_agent_runs_active_thread" in run_index_names
    run_foreign_keys = {
        item["name"]: item for item in inspector.get_foreign_keys("agent_runs")
    }
    assert run_foreign_keys["fk_agent_runs_conversation_thread"][
        "constrained_columns"
    ] == ["user_id", "goal_id", "thread_id"]

    outbox_unique_names = {item["name"] for item in inspector.get_unique_constraints("outbox_events")}
    assert "uq_outbox_events_dedupe_key" in outbox_unique_names
    outbox_index_names = {item["name"] for item in inspector.get_indexes("outbox_events")}
    assert "ix_outbox_events_dispatch_due" in outbox_index_names

    learning_session_indexes = {item["name"] for item in inspector.get_indexes("learning_sessions")}
    assert "uq_learning_sessions_active_user_task" in learning_session_indexes

    learning_plan_indexes = {item["name"] for item in inspector.get_indexes("learning_plans")}
    assert "uq_learning_plans_active_user_goal" in learning_plan_indexes


def test_active_plan_uniqueness_migration_repoints_snapshot_to_winner(tmp_path):
    db_path = tmp_path / "active-plan-migration.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260710_0008")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, status, created_at)
                VALUES ('user-1', 'user@example.com', 'User', 'active', '2026-07-10 08:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO learning_goals (
                    id, user_id, title, domain, target_outcome, deadline,
                    weekly_hours_target, status, learning_preferences, created_at
                ) VALUES (
                    'goal-1', 'user-1', 'Goal', 'test', 'Outcome', NULL,
                    5, 'active', '{}', '2026-07-10 08:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO baseline_diagnostics (
                    id, user_id, goal_id, submitted_answers, baseline_summary,
                    entry_node_id, knowledge_gaps, initial_mastery, evidence_json, created_at
                ) VALUES (
                    'diagnostic-1', 'user-1', 'goal-1', '{}', 'Baseline',
                    NULL, '[]', '{}', '{}', '2026-07-10 08:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO learning_plans (
                    id, user_id, goal_id, curriculum_id, version, status, generated_by,
                    rationale_json, valid_from, valid_to, plan_json, created_at
                ) VALUES
                    ('plan-v1', 'user-1', 'goal-1', NULL, 1, 'active', 'planner',
                     '{}', '2026-07-10', '2026-07-20', '{}', '2026-07-10 08:00:00'),
                    ('plan-v2', 'user-1', 'goal-1', NULL, 2, 'active', 'planner',
                     '{}', '2026-07-10', '2026-07-20', '{}', '2026-07-10 09:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO learning_state_snapshots (
                    id, user_id, goal_id, active_plan_id, active_plan_version,
                    baseline_diagnostic_id, phase_assessment_state_id, latest_plan_adjustment_id,
                    mastery_summary, current_state, generated_from, updated_at
                ) VALUES (
                    'snapshot-1', 'user-1', 'goal-1', 'plan-v1', 1,
                    'diagnostic-1', NULL, NULL, '{}', '{}',
                    :generated_from, '2026-07-10 09:00:00'
                )
                """
            ),
            {"generated_from": json.dumps({"active_plan_id": "plan-v1", "source": "diagnosis"})},
        )

    upgrade(config, "20260710_0009")

    with engine.connect() as connection:
        statuses = dict(
            connection.execute(text("SELECT id, status FROM learning_plans ORDER BY id")).all()
        )
        snapshot = connection.execute(
            text(
                """
                SELECT active_plan_id, active_plan_version, generated_from
                FROM learning_state_snapshots
                WHERE id = 'snapshot-1'
                """
            )
        ).one()

    assert statuses == {"plan-v1": "replaced", "plan-v2": "active"}
    assert snapshot.active_plan_id == "plan-v2"
    assert snapshot.active_plan_version == 2
    assert json.loads(snapshot.generated_from)["active_plan_id"] == "plan-v2"


def test_pending_documents_are_backfilled_into_outbox(tmp_path):
    db_path = tmp_path / "pending-document-backfill.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260626_0004")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, status, created_at)
                VALUES ('pending-user', 'pending@example.com', 'Pending', 'active', '2026-07-01 08:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    id, owner_user_id, corpus_type, filename, object_key, mime_type,
                    parse_status, sha256, source_url, trusted_level, created_at
                ) VALUES (
                    'pending-doc', 'pending-user', 'user_uploaded', 'pending.md',
                    'uploads/pending.md', 'text/markdown', 'pending', 'abc123', NULL, 1,
                    '2026-07-01 08:00:00'
                )
                """
            )
        )

    upgrade(config, "head")

    with engine.connect() as connection:
        event = connection.execute(
            text(
                """
                SELECT event_type, dedupe_key, payload_json, status, attempts
                FROM outbox_events
                WHERE dedupe_key = 'document.process_upload:pending-doc'
                """
            )
        ).one()

    assert event.event_type == "document.process_upload"
    assert json.loads(event.payload_json) == {"document_id": "pending-doc"}
    assert event.status == "pending"
    assert event.attempts == 0


def test_diagnostic_versioning_backfills_legacy_rows_and_downgrades_cleanly(tmp_path):
    db_path = tmp_path / "diagnostic-versioning.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260716_0012")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, display_name, status, created_at, normalized_email, role, token_version
                ) VALUES (
                    'diagnostic-user', 'diagnostic@example.com', 'Diagnostic', 'active',
                    '2026-07-18 08:00:00', 'diagnostic@example.com', 'learner', 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO learning_goals (
                    id, user_id, title, domain, target_outcome, deadline,
                    weekly_hours_target, status, learning_preferences, created_at
                ) VALUES (
                    'diagnostic-goal', 'diagnostic-user', 'Goal', 'ai_app_dev', 'Outcome', NULL,
                    5, 'active', '{}', '2026-07-18 08:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO baseline_diagnostics (
                    id, user_id, goal_id, submitted_answers, baseline_summary,
                    entry_node_id, knowledge_gaps, initial_mastery, evidence_json, created_at
                ) VALUES (
                    'legacy-diagnostic', 'diagnostic-user', 'diagnostic-goal', '{}', 'Legacy',
                    NULL, '[]', '{}', '{}', '2026-07-18 08:00:00'
                )
                """
            )
        )

    upgrade(config, "20260718_0013")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT request_id, template_version, template_hash, score_breakdown
                FROM baseline_diagnostics WHERE id = 'legacy-diagnostic'
                """
            )
        ).one()
    assert row.request_id is None
    assert row.template_version == "legacy_unversioned"
    assert row.template_hash is None
    assert json.loads(row.score_breakdown) == {}

    downgrade(config, "20260716_0012")
    assert "template_version" not in {
        column["name"] for column in inspect(engine).get_columns("baseline_diagnostics")
    }

    upgrade(config, "20260718_0013")
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT template_version FROM baseline_diagnostics WHERE id = 'legacy-diagnostic'"
            )
        ) == "legacy_unversioned"
    engine.dispose()


def test_document_processing_metadata_migration_preserves_legacy_rows_and_downgrades(
    tmp_path,
):
    db_path = tmp_path / "document-processing-metadata.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "20260718_0013")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, display_name, status, created_at, normalized_email, role, token_version
                ) VALUES (
                    'document-user', 'document@example.com', 'Document', 'active',
                    '2026-07-18 08:00:00', 'document@example.com', 'learner', 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    id, owner_user_id, corpus_type, filename, object_key, mime_type,
                    parse_status, parse_error, sha256, source_url, trusted_level, created_at
                ) VALUES (
                    'legacy-document', 'document-user', 'user_uploaded', 'legacy.md',
                    'uploads/legacy.md', 'text/markdown', 'success', NULL, 'abc123', NULL, 1,
                    '2026-07-18 08:00:00'
                )
                """
            )
        )

    upgrade(config, "20260718_0014")
    metadata_columns = {
        column["name"] for column in inspect(engine).get_columns("documents")
    }
    assert {
        "size_bytes",
        "parse_error_code",
        "page_count",
        "block_count",
        "parser_version",
        "processing_started_at",
        "processing_completed_at",
    } <= metadata_columns
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT size_bytes, parse_error_code, page_count, block_count, parser_version,
                       processing_started_at, processing_completed_at
                FROM documents WHERE id = 'legacy-document'
                """
            )
        ).one()
    assert all(value is None for value in row)

    downgrade(config, "20260718_0013")
    assert "size_bytes" not in {
        column["name"] for column in inspect(engine).get_columns("documents")
    }
    upgrade(config, "20260718_0014")
    assert "size_bytes" in {
        column["name"] for column in inspect(engine).get_columns("documents")
    }
    engine.dispose()


def test_conversation_migration_downgrades_and_reapplies_cleanly(tmp_path):
    db_path = tmp_path / "conversation-roundtrip.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)

    upgrade(config, "head")
    engine = create_engine(database_url)
    assert "conversation_threads" in inspect(engine).get_table_names()

    downgrade(config, "20260718_0015")
    assert "conversation_threads" not in inspect(engine).get_table_names()
    assert "correlation_id" not in {
        item["name"] for item in inspect(engine).get_columns("agent_runs")
    }

    upgrade(config, "head")
    assert "conversation_threads" in inspect(engine).get_table_names()
    assert "correlation_id" in {
        item["name"] for item in inspect(engine).get_columns("agent_runs")
    }
    engine.dispose()


def test_postgresql_agent_run_backfill_interprets_legacy_created_at_as_utc():
    from importlib import import_module

    migration = import_module(
        "backend.alembic.versions.20260729_0016_conversation_threads_and_run_trace"
    )

    assert migration._started_at_backfill_sql("postgresql") == (
        "UPDATE agent_runs SET started_at = created_at AT TIME ZONE 'UTC' "
        "WHERE started_at IS NULL"
    )
