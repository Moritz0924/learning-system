# Task 5 report: final integration and review fix wave

## Delivered scope

- Made the checkpoint backend default to the in-memory saver for test environments and for non-production SQLite application runtimes, including the repository's default SQLite startup when `DATABASE_URL`, `APP_ENV`, and `TUTOR_CHECKPOINT_BACKEND` are all absent. Production and explicit PostgreSQL selection still use the PostgreSQL saver and retain the PostgreSQL URL validation boundary.
- Moved synchronous `POST /api/tutor/chat` onto the same owner-scoped one-active-run lifecycle used by streaming chat. The managed row is committed before context/provider work, conflicts return HTTP 409, cancellation is checked before application commit, success is terminalized only after history checkpoint finalization, and failures remain durable.
- Preserved the synchronous audit contract while eliminating the duplicate legacy audit row: the managed row receives the canonical request audit, post-write memory receipt statuses, node trace, public output snapshot, and tool-call linkage.
- Added a durable request-thread resolution boundary for every non-chat application trigger. Assessment creation/phase creation, assessment submission, task completion, and manual replan now resolve their public or synthetic alias to a persisted user+goal-owned `ConversationThread` before LangGraph invocation. The request, checkpoint namespace, audit input, and `AgentRun.thread_id` all use the same canonical server ID. The public replan endpoint now honors its existing `thread_id` field.
- Added a defensive non-chat engine ownership check so a future caller cannot send a raw alias into LangGraph without first resolving it. Canonical conversation rows are committed before external checkpoint writes, so archive/reconciliation cleanup remains possible even when later business work fails.
- Updated the branch-local Alembic-head assertion from deliberate head `20260718_0015` to current head `20260729_0016`, and updated managed-run/failure-injection doubles to the current keyword arguments.
- Did not change retrieval, answer/citation shape, assessment algorithms, document code, MCP routing, prompt candidates, or remote evaluation behavior. No paid or remote provider was invoked.

## TDD evidence

All pytest commands cleared uppercase and lowercase HTTP/HTTPS/ALL/NO proxy variables and used a short external `C:\p5-*` base temporary directory.

Initial RED covered runtime selection, chat concurrency, checkpoint identity, and stale tests:

```powershell
python -m pytest -q \
  tests/tutor/test_runtime_checkpoints.py::test_sqlite_startup_selects_memory_without_hidden_test_environment \
  tests/tutor/test_tutor_streaming_api.py::test_sync_chat_conflicts_with_active_stream_and_uses_managed_terminal_trace \
  tests/assessment/test_assessment_request_validation.py::test_create_assessment_audits_the_requested_thread_id \
  tests/memory/test_memory_migration.py::test_conversation_runtime_migration_is_the_only_head \
  tests/tutor/test_conversation_persistence.py::test_sync_tutor_reuses_legacy_alias_safely_across_two_goals \
  --basetemp=C:\p5-red1 -o addopts=''
```

Result before implementation: `4 failed, 1 passed`. The expected failures showed SQLite being parsed as PostgreSQL, synchronous chat returning 200 while a stream run was active, audit/checkpoint thread divergence, and the old unmanaged mock signature. The new 0016 head assertion already passed.

Non-chat RED was run after temporarily removing the exploratory implementation and before restoring the production changes:

```powershell
python -m pytest -q \
  tests/assessment/test_assessment_request_validation.py::test_create_assessment_audits_the_requested_thread_id \
  tests/test_learning_evidence_replan_application.py::test_task_start_and_complete_records_sessions_events_and_refreshes_state \
  tests/test_learning_evidence_replan_application.py::test_assessment_cannot_be_submitted_twice \
  tests/test_learning_evidence_replan_application.py::test_replan_preview_then_apply_creates_new_plan_tasks_and_audit_event \
  --basetemp=C:\p5-red2 -o addopts=''
```

Result before implementation: `4 failed`. Every run used a raw alias in its input/checkpoint while the audit row referenced a different generated conversation ID; manual replan also ignored the public `thread_id` and used `manual-replan`.

Managed tool-call RED:

```powershell
python -m pytest -q \
  tests/tutor/test_tutor_streaming_api.py::test_sync_chat_conflicts_with_active_stream_and_uses_managed_terminal_trace \
  --basetemp=C:\p5-red3 -o addopts=''
```

Result before audit linkage: `1 failed`; the real retrieval tool call had `agent_run_id=None` instead of the synchronous managed run ID. Existing memory audit regressions later also demonstrated missing post-write receipt fields before enrichment was added.

Focused GREEN results:

- Runtime/concurrency/canonical assessment/stale regressions: `5 passed`.
- Non-chat canonical checkpoint paths: `4 passed`.
- Managed tool-call, memory audit, transaction, and failure behavior: `4 passed`.
- Final no-environment default-SQLite startup plus production PostgreSQL policy: `2 passed`.

## Final verification

Affected backend, migrations, stream/concurrency, memory, Phase 2, and Stage 3 API sweep:

```powershell
python -m pytest -q \
  tests/tutor/test_runtime_checkpoints.py \
  tests/tutor/test_tutor_streaming_api.py \
  tests/tutor/test_conversation_persistence.py \
  tests/assessment/test_assessment_request_validation.py \
  tests/test_learning_evidence_replan_application.py \
  tests/test_alembic_migration.py \
  tests/memory/test_memory_migration.py \
  tests/memory/test_memory_context_transactions.py \
  tests/phase2/test_phase2_engine.py \
  tests/phase2/test_phase2_contracts.py \
  tests/test_stage3_api_workflow.py \
  --basetemp=C:\p5-reg2 -o addopts=''
```

Result: `107 passed, 762 warnings`.

Complete backend suite, with the repository venv prepended to `PATH` so the worktree-local test-script contract uses the installed environment:

```powershell
python -m pytest -q tests --basetemp=C:\p5-all-final -o addopts=''
```

Result: `682 passed, 1954 warnings in 128.70s`.

Independent short-path document verification:

```powershell
python -m pytest -q \
  tests/documents \
  tests/test_document_ingestion_worker.py \
  tests/test_document_parsing.py \
  tests/test_frontend_document_upload_contracts.py \
  --basetemp=C:\p5-doc-final -o addopts=''
```

Result: `85 passed, 389 warnings`. The 16 document failures previously seen with a worktree-local pytest base are therefore Windows path-length environmental failures; no document implementation or test was changed to mask them.

Frontend verification:

- SSE decoder/race unit tests: `5 passed`.
- UI route verification: passed.
- ESLint: exit 0.
- Next.js 16.2.12 production build: exit 0; TypeScript and all 12 static routes completed.
- Real local Chromium `memory-workflow.spec.ts`: `5 passed`. The E2E runner supplied SQLite but no hidden `APP_ENV` or `TUTOR_CHECKPOINT_BACKEND`, directly exercising automatic in-memory checkpoint selection.

Additional gates:

- `python -m compileall -q backend/app src/adaptive_tutor`: exit 0.
- `git diff --check`: exit 0; only configured LF-to-CRLF notices were emitted.

## Residual environment notes

- This nested worktree has no local `.venv`, so `scripts/test.ps1` falls back to system `python` unless the repository venv is on `PATH`. The contract test passes when run under the intended repository environment; no product or script change was made for this workspace-only layout.
- The known 16 document failures occur only with a long worktree-local temporary path. Both the 85-test document subset and the complete 682-test backend suite pass with a short external base. No document-code blocker remains.
- No live PostgreSQL service was available. Production saver selection/URL normalization and migration contracts are covered, but live PostgreSQL saver connectivity remains the same external integration limitation documented in Task 3.
