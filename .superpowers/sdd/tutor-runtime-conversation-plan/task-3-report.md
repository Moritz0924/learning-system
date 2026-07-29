# Task 3 report: persistent runtime and history

## Delivered scope

- Added a production PostgreSQL LangGraph checkpoint runtime and a test-only in-memory runtime behind one lifecycle protocol.
- Compiled every tutor graph with the selected saver and invoked/updated it with `configurable.thread_id`.
- Added FastAPI startup/shutdown ownership for saver initialization and cleanup. PostgreSQL `setup()` is idempotent and owns its internal schema migrations; Alembic does not define saver tables.
- Restored only an exact thread/user/goal-matching typed conversation boundary from checkpoints. Request objects, prepared context, RAG documents, tutor context, and long-term memories use untracked LangGraph channels and are not checkpointed.
- Passed restored short-term conversation context separately from validated long-term memories to the existing LLM gateway boundary.
- Added deterministic offline compaction after the configured completed-turn threshold or estimated-token threshold. Defaults are 12 turns and 16,000 estimated tokens; stored messages and summaries are sanitized and bounded.
- Deferred completed-turn checkpoint finalization until after workflow actions and the application database transaction commit, preventing failed requests from restoring undelivered answers.
- Bridged application thread archival to LangGraph `delete_thread`, so archived conversations do not retain checkpoint history.
- Preserved the synchronous chat result, citations, retrieval, assessment, memory, MCP, and evaluation contracts. No SSE, cancellation endpoint/UI, frontend, prompt-candidate, or remote-evaluation changes were made.

## Changed files

- `backend/app/infrastructure/checkpoints.py` - settings, PostgreSQL/in-memory runtime adapters, explicit safe serializer allowlist, idempotent setup, close, deletion, and process lifecycle.
- `src/adaptive_tutor/tutor/history.py` - ownership-safe restoration, LLM conversation context projection, token estimation, and deterministic bounded compaction.
- `src/adaptive_tutor/tutor/models.py` - typed completed conversation turns in the existing authoritative workflow state.
- `src/adaptive_tutor/phase2/schemas.py` - untracked ephemeral graph channels so only the workflow state is checkpointed.
- `src/adaptive_tutor/phase2/engine.py` - configurable saver compilation, `thread_id` invocation, safe restore, and final history checkpoint update.
- `src/adaptive_tutor/tutor/context_services.py` - separate short-term conversation context delivery at the LLM boundary.
- `backend/app/application/engine.py` - selected runtime injection into production engine construction.
- `backend/app/application/conversation_service.py` - archive-to-checkpoint deletion bridge without changing existing persistence APIs.
- `backend/app/main.py` - saver startup/shutdown lifespan.
- `backend/app/core/runtime_config.py`, `.env.example`, and `docker-compose.yml` - production backend and history threshold configuration.
- `pyproject.toml` - PostgreSQL saver dependency.
- `tests/tutor/test_runtime_checkpoints.py` - focused Task 3 behavior tests.
- `tests/test_alembic_migration.py` - explicit assertion that Alembic does not create LangGraph saver tables.
- `tests/conftest.py` - explicit test runtime environment, selecting the test-only in-memory saver.

## TDD evidence and verification

The first focused RED run failed during collection because `backend.app.infrastructure.checkpoints` did not exist. Later RED cycles demonstrated restart truncation at a configured 14-turn boundary, an unregistered-workflow-model serializer warning, premature completed-turn finalization, failure to compact restored history under lowered thresholds, and loss of newest turns after repeated long compactions. Each defect was reproduced before its focused fix. A forced application-commit failure additionally verifies that an undelivered answer never becomes completed checkpoint history.

All test commands removed uppercase and lowercase HTTP/HTTPS/ALL/NO proxy variables and used explicit `--basetemp` paths. No remote LLM, embedding, judge, or other paid provider was invoked.

Focused Task 3 plus Task 1-2 persistence/migration verification:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest -q tests/tutor/test_runtime_checkpoints.py tests/tutor/test_conversation_persistence.py tests/tutor/test_tutor_domain_contracts.py tests/test_alembic_migration.py tests/memory/test_memory_context_transactions.py --basetemp=.tmp/pytest-task3-review-final -o addopts=''
```

Result: `58 passed, 154 warnings in 12.55s`.

Final engine/state compatibility verification:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest -q tests/phase2/test_phase2_engine.py tests/phase2/test_phase2_contracts.py tests/memory/test_memory_context_tutor_integration.py --basetemp=.tmp/pytest-task3-engine-final -o addopts=''
```

Result: `20 passed, 1 warning in 0.14s`.

`python -m compileall -q backend/app src/adaptive_tutor` and `git diff --check` also completed successfully. Warnings are existing Starlette and naive-UTC deprecations.

An independent read-only review found three Important issues in its first pass (commit/checkpoint ordering, policy-change restoration, and rolling-summary freshness). After the focused fixes and regressions, re-review reported no Critical or Important findings and marked Task 3 ready.

## Dependency and runtime behavior

- Added `langgraph-checkpoint-postgres>=3.1,<4`; the verified environment resolved `langgraph-checkpoint-postgres 3.1.0`, `langgraph-checkpoint 4.1.1`, and `psycopg-pool 3.3.1` alongside the existing `langgraph 1.2.9` and `psycopg 3.3.4`.
- Production defaults to `TUTOR_CHECKPOINT_BACKEND=postgres`. `TUTOR_CHECKPOINT_DATABASE_URL` may override the application database; when blank, `DATABASE_URL` is used and the SQLAlchemy `postgresql+psycopg://` prefix is normalized for psycopg.
- The in-memory backend is rejected outside `APP_ENV=test` or `testing`.
- PostgreSQL opens an autocommit psycopg connection with `prepare_threshold=0` and `dict_row`, constructs the saver with an explicit workflow-model deserialization allowlist, calls `setup()` once, and closes the connection on shutdown or failed setup.
- Saver tables remain library-owned. The application migration at Alembic head is verified not to contain `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, or `checkpoint_writes`.

## Concerns

- No live PostgreSQL service was available in this worktree, so PostgreSQL connectivity and actual saver DDL execution were not exercised end to end. The adapter follows the installed 3.1.0 API and the official required connection options; live PostgreSQL verification remains an integration item for Task 5.
- A broad full-Python run was intentionally stopped at the parent coordinator's request to prioritize scoped Task 3 delivery. The focused and affected suites above are green; unrelated document-upload tests require a shorter Windows basetemp than this nested worktree path.
