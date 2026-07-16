import json

from alembic.command import upgrade
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
    assert "tool_calls" in inspector.get_table_names()
    assert "outbox_events" in inspector.get_table_names()
    assert "learning_sessions" in inspector.get_table_names()
    assert "learning_events" in inspector.get_table_names()
    chunk_columns = {column["name"] for column in inspector.get_columns("document_chunks")}
    assert "embedding_vector" in chunk_columns
    document_columns = {column["name"] for column in inspector.get_columns("documents")}
    assert "parse_error" in document_columns
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
