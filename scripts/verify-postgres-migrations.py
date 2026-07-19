from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


REQUIRED_TABLES = {
    "users",
    "learning_goals",
    "baseline_diagnostics",
    "learning_plans",
    "plan_tasks",
    "mastery_records",
    "learning_state_snapshots",
    "documents",
    "document_chunks",
    "outbox_events",
    "auth_sessions",
    "refresh_tokens",
    "memories",
}

REQUIRED_INDEXES = {
    "ix_document_chunks_embedding_vector",
    "uq_learning_sessions_active_user_task",
    "uq_learning_plans_active_user_goal",
    "ix_outbox_events_dispatch_due",
    "ix_auth_sessions_user_status",
    "ix_refresh_tokens_session_expires",
    "uq_baseline_diagnostics_user_request_id",
    "ix_memories_user_scope_type",
    "ix_memories_user_enabled_expiry",
}

REQUIRED_MEMORY_COLUMNS = {
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


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            verification_transaction = connection.begin()
            try:
                extension_installed = connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                )
                if not extension_installed:
                    raise AssertionError("the vector extension is not installed")

                revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
                if len(revisions) != 1:
                    raise AssertionError(f"expected one applied Alembic head, found {revisions!r}")

                schema = inspect(connection)
                existing_tables = set(schema.get_table_names())
                missing_tables = REQUIRED_TABLES - existing_tables
                if missing_tables:
                    raise AssertionError(f"missing required tables: {sorted(missing_tables)!r}")

                existing_indexes = set(
                    connection.execute(
                        text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
                    ).scalars()
                )
                missing_indexes = REQUIRED_INDEXES - existing_indexes
                if missing_indexes:
                    raise AssertionError(f"missing required indexes: {sorted(missing_indexes)!r}")

                diagnostic_columns = {
                    column["name"]: column
                    for column in schema.get_columns("baseline_diagnostics")
                }
                for required_column in (
                    "request_id",
                    "template_version",
                    "template_hash",
                    "score_breakdown",
                ):
                    if required_column not in diagnostic_columns:
                        raise AssertionError(
                            f"missing baseline diagnostic column: {required_column}"
                        )
                if diagnostic_columns["template_version"]["nullable"]:
                    raise AssertionError("baseline diagnostic template_version must be non-null")
                if diagnostic_columns["score_breakdown"]["nullable"]:
                    raise AssertionError("baseline diagnostic score_breakdown must be non-null")

                document_columns = {
                    column["name"]: column for column in schema.get_columns("documents")
                }
                for required_column in (
                    "size_bytes",
                    "parse_error_code",
                    "page_count",
                    "block_count",
                    "parser_version",
                    "processing_started_at",
                    "processing_completed_at",
                ):
                    if required_column not in document_columns:
                        raise AssertionError(
                            f"missing document processing column: {required_column}"
                        )
                    if not document_columns[required_column]["nullable"]:
                        raise AssertionError(
                            f"document processing column must be nullable: {required_column}"
                        )

                memory_columns = {
                    column["name"]: column for column in schema.get_columns("memories")
                }
                missing_memory_columns = set(REQUIRED_MEMORY_COLUMNS) - set(memory_columns)
                if missing_memory_columns:
                    raise AssertionError(
                        f"missing memory columns: {sorted(missing_memory_columns)!r}"
                    )
                observed_memory_nullability = {
                    name: memory_columns[name]["nullable"]
                    for name in REQUIRED_MEMORY_COLUMNS
                }
                if observed_memory_nullability != REQUIRED_MEMORY_COLUMNS:
                    raise AssertionError(
                        "unexpected memory column nullability: "
                        f"{observed_memory_nullability!r}"
                    )

                memory_unique_constraints = {
                    constraint["name"] for constraint in schema.get_unique_constraints("memories")
                }
                if "uq_memories_user_idempotency" not in memory_unique_constraints:
                    raise AssertionError("missing uq_memories_user_idempotency")
                goal_unique_constraints = {
                    constraint["name"]
                    for constraint in schema.get_unique_constraints("learning_goals")
                }
                if "uq_learning_goals_user_id_id" not in goal_unique_constraints:
                    raise AssertionError("missing uq_learning_goals_user_id_id")

                memory_foreign_keys = schema.get_foreign_keys("memories")
                memory_goal_foreign_key = next(
                    (
                        foreign_key
                        for foreign_key in memory_foreign_keys
                        if foreign_key["name"] == "fk_memories_user_goal"
                    ),
                    None,
                )
                if memory_goal_foreign_key is None:
                    raise AssertionError("missing fk_memories_user_goal")
                if (
                    memory_goal_foreign_key["constrained_columns"] != ["user_id", "goal_id"]
                    or memory_goal_foreign_key["referred_table"] != "learning_goals"
                    or memory_goal_foreign_key["referred_columns"] != ["user_id", "id"]
                ):
                    raise AssertionError(
                        "fk_memories_user_goal must constrain memories[user_id, goal_id] "
                        "to learning_goals[user_id, id]"
                    )

                memory_check_constraints = {
                    constraint["name"] for constraint in schema.get_check_constraints("memories")
                }
                missing_memory_checks = {
                    "ck_memories_importance_range",
                    "ck_memories_confidence_range",
                } - memory_check_constraints
                if missing_memory_checks:
                    raise AssertionError(
                        f"missing memory check constraints: {sorted(missing_memory_checks)!r}"
                    )

                suffix = uuid4().hex
                created_at = datetime.now(timezone.utc)
                user_a_id = f"memory-verification-a-{suffix}"
                user_b_id = f"memory-verification-b-{suffix}"
                goal_b_id = f"memory-verification-goal-{suffix}"
                for user_id in (user_a_id, user_b_id):
                    email = f"{user_id}@example.invalid"
                    connection.execute(
                        text(
                            """
                            INSERT INTO users (
                                id, email, display_name, status, created_at, normalized_email,
                                role, token_version
                            ) VALUES (
                                :id, :email, 'Memory verification user', 'active', :created_at,
                                :normalized_email, 'learner', 1
                            )
                            """
                        ),
                        {
                            "id": user_id,
                            "email": email,
                            "created_at": created_at,
                            "normalized_email": email,
                        },
                    )
                connection.execute(
                    text(
                        """
                        INSERT INTO learning_goals (
                            id, user_id, title, domain, target_outcome, deadline,
                            weekly_hours_target, status, learning_preferences, created_at
                        ) VALUES (
                            :id, :user_id, 'Memory verification goal', 'ai_app_dev',
                            'Verify ownership isolation', NULL, 1, 'active',
                            CAST('{}' AS JSON), :created_at
                        )
                        """
                    ),
                    {"id": goal_b_id, "user_id": user_b_id, "created_at": created_at},
                )

                try:
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                """
                                INSERT INTO memories (
                                    id, user_id, goal_id, memory_type, schema_version, content_json,
                                    content_hash, source_kind, source_ref_id, source_metadata,
                                    importance, confidence, is_enabled, expires_at, disabled_at,
                                    disabled_reason, idempotency_key, created_at, updated_at
                                ) VALUES (
                                    :id, :user_id, :goal_id, 'learning_preference', 'memory-v1',
                                    CAST('{}' AS JSON), :content_hash, 'explicit_user', :source_ref_id,
                                    CAST('{}' AS JSON), 0.5, 0.5, FALSE, :expires_at, :disabled_at,
                                    :disabled_reason,
                                    :idempotency_key, :created_at, :updated_at
                                )
                                """
                            ),
                            {
                                "id": f"memory-verification-memory-{suffix}",
                                "user_id": user_a_id,
                                "goal_id": goal_b_id,
                                "content_hash": suffix,
                                "source_ref_id": f"memory-verification-source-{suffix}",
                                "expires_at": created_at,
                                "disabled_at": created_at,
                                "disabled_reason": "verification",
                                "idempotency_key": f"memory-verification-{suffix}",
                                "created_at": created_at,
                                "updated_at": created_at,
                            },
                        )
                except IntegrityError as error:
                    diagnostic = getattr(error.orig, "diag", None)
                    if getattr(diagnostic, "constraint_name", None) != "fk_memories_user_goal":
                        raise
                else:
                    raise AssertionError(
                        "cross-user memory goal insert unexpectedly succeeded"
                    )
            finally:
                verification_transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
