# Task 2 report: conversation and run persistence

## Delivered scope

- Added application-owned `conversation_threads` persistence with server-generated UUID thread IDs, active/archive lifecycle state, ownership-safe user/goal/thread lookups, and goal ownership enforced by a composite foreign key.
- Added ownership-safe repository and application services for thread creation, lookup, listing, archival, managed run start/complete/fail, cancellation request checks, and terminal cancellation persistence.
- Enforced one active run per thread in both repository logic and a PostgreSQL/SQLite partial unique index covering `running` and `cancellation_requested` states.
- Extended `agent_runs` with goal ownership, UUID correlation ID, canonical request hash, node trace, start/completion timestamps, and cancellation timestamps.
- Kept synchronous `/api/tutor/chat` compatible by adopting a previously unseen caller thread ID into an owned legacy thread after validating the user/goal scope. The lifecycle write is committed before provider execution so the existing no-open-read-transaction behavior remains intact.
- Did not add checkpoint tables or integration, SSE, history compression, frontend changes, or changes to retrieval, answer/citation, assessment, MCP, evaluation, document parsing, or prompt behavior.

## Changed files

- `backend/alembic/versions/20260729_0016_conversation_threads_and_run_trace.py` - application-table migration and downgrade.
- `backend/app/models.py` - `ConversationThread` model and `AgentRun` trace/cancellation extensions.
- `backend/app/domain/conversation.py` - immutable records, ownership-safe protocols, and domain errors.
- `backend/app/infrastructure/persistence/repositories/conversation_repository.py` - SQLAlchemy thread/run persistence and concurrency handling.
- `backend/app/application/conversation_service.py` - application-owned lifecycle API.
- `backend/app/application/tutor_service.py` and `backend/app/routers/tutor.py` - synchronous legacy thread adoption and safe not-found handling.
- `backend/app/infrastructure/persistence/repositories/audit_repository.py` and `src/adaptive_tutor/phase2/engine.py` - run correlation, request hash, node trace, and terminal timestamp persistence.
- `tests/tutor/test_conversation_persistence.py` - lifecycle, isolation, concurrent conflict, cancellation, and trace tests.
- `tests/test_alembic_migration.py` - schema constraints plus downgrade/reapply coverage.
- `tests/memory/test_memory_context_transactions.py` and `tests/assessment/test_assessment_request_validation.py` - synchronous transaction and audit compatibility assertions.

## TDD evidence

Initial RED runs failed because the conversation application/repository modules and migration table did not exist. Subsequent RED cycles caught the missing goal-scoped run cancellation/application lifecycle methods, missing goal-scoped thread read/archive APIs, nullable migrated `started_at`, and the missing composite agent-run goal ownership constraint. The broad compatibility check also caught an open transaction introduced by legacy thread adoption; committing the lifecycle record before engine execution restored the existing provider boundary.

Focused GREEN verification used cleared `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY`, `NO_PROXY=*`, and workspace-local `--basetemp` directories:

```powershell
& 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .t2-final tests\tutor\test_conversation_persistence.py tests\test_alembic_migration.py tests\tutor\test_tutor_domain_contracts.py tests\phase2\test_phase2_engine.py tests\phase2\test_phase2_contracts.py tests\memory\test_memory_context_transactions.py tests\memory\test_memory_chat_write_api.py tests\memory\test_memory_context_tutor_integration.py tests\assessment\test_assessment_request_validation.py
```

Result: `68 passed, 301 warnings in 15.09s`. The warnings are existing Starlette and naive-UTC deprecations.

Migration downgrade/reapply verification:

```powershell
& 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .t2-roundtrip tests\test_alembic_migration.py::test_conversation_migration_downgrades_and_reapplies_cleanly
```

Result: `1 passed, 2 warnings in 0.96s`.

`python -m compileall -q backend/app src/adaptive_tutor` and `git diff --check` also completed successfully.

## Migration design

- Alembic owns only the application `conversation_threads` table and the application `agent_runs` columns/indexes. No LangGraph/checkpointer table is created or modified.
- `conversation_threads` uses a composite `(user_id, goal_id)` foreign key to `learning_goals`, a named status check, an ownership/status listing index, and a unique `(user_id, goal_id, id)` key.
- Existing agent runs remain migratable: new ownership/correlation fields are nullable for legacy rows, `node_trace` is backfilled to an empty JSON list, and `started_at` is backfilled from `created_at` before being made non-null.
- New managed runs require goal/correlation/hash values at the domain boundary. Agent-run goal ownership is enforced with a composite `(user_id, goal_id)` foreign key.
- The partial unique index `uq_agent_runs_active_thread` is portable across PostgreSQL production and SQLite tests and prevents concurrent `running` or `cancellation_requested` rows for one thread.
- Downgrade removes the new indexes, constraints, columns, and conversation table; a downgrade/reapply test verifies the round trip.

## Concerns

- Legacy synchronous chat must continue accepting caller-provided thread IDs until the Task 4 frontend/API transition. Those IDs are adopted only after strict user/goal validation; new application-created threads always use server-generated UUIDs.
- A broader non-gating regression attempt reached `107 passed` with five unrelated document-upload failures caused by Windows path length under this long worktree. The scoped Task 2/compatibility suite is green; no document code was changed.
- SQLite validates the portable constraints and persistence behavior, but production concurrency semantics still depend on the PostgreSQL partial unique index included in this migration.

## Review fix round 1

- Made cancellation terminal: both `complete` and `fail` now convert `cancellation_requested` to `cancelled` without accepting late output, trace, or errors, and leave an already-cancelled run unchanged.
- Replaced the agent-run user/goal foreign key with a composite `(user_id, goal_id, thread_id)` foreign key to `conversation_threads`, so the database rejects cross-user and cross-goal thread mismatches on SQLite and PostgreSQL.
- Added nullable `conversation_threads.legacy_key` with uniqueness scoped to `(user_id, goal_id)`. Legacy synchronous caller IDs now resolve to stable server-generated UUID threads independently for each goal; the fixed frontend ID can therefore be reused while the persisted runtime always uses the owned server ID.
- Updated the audit sink to resolve caller aliases to an owned conversation before inserting an agent run. Existing audit payloads retain the requested alias when they originate outside the synchronous chat adapter, while `AgentRun.thread_id` always references the owned conversation row.
- Added matching `SELECT ... FOR UPDATE` locking to managed run start and thread archive before either checks active/archive state. On PostgreSQL the two operations serialize on the same conversation row; either the run commits first and blocks archive, or archive commits first and blocks start.
- Made the PostgreSQL migration backfill explicit: legacy naive `agent_runs.created_at` values use `created_at AT TIME ZONE 'UTC'` when populating timezone-aware `started_at`; SQLite retains the direct assignment used by tests.

Review RED evidence covered late completion after cancellation, late failure after cancellation, database ownership mismatches, a fixed legacy alias reused across two goals, missing migration constraints, and missing PostgreSQL UTC conversion. Focused GREEN verification was:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
& 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .t2-r1-verify tests\tutor\test_conversation_persistence.py tests\test_alembic_migration.py tests\tutor\test_tutor_domain_contracts.py tests\phase2\test_phase2_engine.py tests\phase2\test_phase2_contracts.py tests\memory\test_memory_context_transactions.py tests\memory\test_memory_chat_write_api.py tests\memory\test_memory_context_tutor_integration.py tests\assessment\test_assessment_request_validation.py
```

Result: `76 passed, 334 warnings in 17.80s`. Warnings remain the existing Starlette and naive-UTC deprecations. `python -m compileall -q backend/app backend/alembic/versions/20260729_0016_conversation_threads_and_run_trace.py` and `git diff --check` also passed.
