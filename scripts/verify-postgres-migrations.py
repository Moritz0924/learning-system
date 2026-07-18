from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text


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
}

REQUIRED_INDEXES = {
    "ix_document_chunks_embedding_vector",
    "uq_learning_sessions_active_user_task",
    "uq_learning_plans_active_user_goal",
    "ix_outbox_events_dispatch_due",
    "ix_auth_sessions_user_status",
    "ix_refresh_tokens_session_expires",
    "uq_baseline_diagnostics_user_request_id",
}


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            extension_installed = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
            if not extension_installed:
                raise AssertionError("the vector extension is not installed")

            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if len(revisions) != 1:
                raise AssertionError(f"expected one applied Alembic head, found {revisions!r}")

            existing_tables = set(inspect(connection).get_table_names())
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
                for column in inspect(connection).get_columns("baseline_diagnostics")
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
                column["name"]: column
                for column in inspect(connection).get_columns("documents")
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
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
