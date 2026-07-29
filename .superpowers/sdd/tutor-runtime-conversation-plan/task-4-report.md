# Task 4 report: SSE, cancellation, and frontend integration

## Delivered scope

- Added authenticated `POST /api/tutor/chat/stream` with the public SSE event allowlist: `run.started`, `node.started`, `retrieval.completed`, `teacher.delta`, `node.completed`, `run.completed`, `run.failed`, and `run.cancelled`.
- Added a managed streaming-run lifecycle that commits the run before work starts, checks durable or disconnect cancellation immediately before the existing application commit/checkpoint-finalization boundary, and persists success, failure, or cancellation without inserting a duplicate legacy audit run.
- Added owner-only `POST /api/tutor/runs/{run_id}/cancel`. Cross-user run IDs return 404 and cancellation is committed durably.
- Added owner/goal-scoped conversation create, list, and archive APIs and connected archive to the Task 3 checkpoint cleanup behavior.
- Kept synchronous `POST /api/tutor/chat` compatible. Its engine call and legacy conversation adoption behavior are unchanged.
- Added a fragmented UTF-8 SSE decoder and routed streaming requests through the same bearer-token, cookie, and one-time 401 refresh path as existing JSON API calls.
- Replaced every fixed frontend `frontend-thread` usage with an active server-generated conversation. Added lightweight select, new, delete, and cancel controls without redesigning the tutor page.
- Updated the existing memory/auth Playwright workflow to exercise SSE, refresh retry, server-generated thread IDs, and session create/delete behavior.
- Did not change retrieval, answer/citation, assessment, MCP, evaluation, document parsing, prompt selection, or Task 3 checkpoint algorithms.

## Event data boundary

- Run events expose only the durable run ID and owned thread ID.
- Retrieval events expose only status and citation count.
- Teacher events expose only assistant answer deltas.
- Completion exposes the public answer, public citation fields, and an allowlisted runtime metadata projection.
- Failure exposes a stable public code and generic message; persisted errors contain only the exception class name.
- No event serializer accepts prompt fields, long-term memory objects, API keys, tracebacks, ORM instances, workflow state, or arbitrary internal dictionaries.

## Changed files

- `backend/app/api/schemas/tutor.py` - conversation and cancellation API schemas.
- `backend/app/domain/conversation.py` - owner-only cancellation repository protocol.
- `backend/app/infrastructure/persistence/repositories/conversation_repository.py` - ownership-scoped durable cancellation lookup/update.
- `backend/app/application/conversation_service.py` - owner-only cancellation application method.
- `backend/app/application/engine.py` - opt-in managed-stream audit suppression and pre-commit lifecycle hook; synchronous defaults unchanged.
- `backend/app/application/tutor_stream_service.py` - managed stream setup, cancellation gate, terminal persistence, and public result projection.
- `backend/app/routers/tutor.py` - conversation, stream, cancellation routes, disconnect monitor, and SSE framing.
- `frontend/lib/api.ts` - reusable authenticated response path plus stream/delete requests.
- `frontend/lib/tutor-stream.mjs` and `frontend/lib/tutor-stream.d.mts` - allowlisted SSE decoder and TypeScript contract.
- `frontend/lib/learning-data.ts` - conversation type.
- `frontend/components/learning-provider.tsx` - server-managed sessions, streamed chat state, refresh-preserving request, and explicit cancellation.
- `frontend/components/learning-pages.tsx` - light session and cancellation controls.
- `frontend/tests/tutor-stream.test.mjs` - real fragmented stream and event allowlist tests.
- `tests/tutor/test_tutor_streaming_api.py` - API ownership, sequence, sanitization, terminal failure/cancellation, and disconnect durability tests.
- `frontend/e2e/memory-workflow.spec.ts` - streamed memory/auth/session workflow coverage.

## TDD and verification results

- Initial backend RED: `3 failed` because conversation, stream, and cancellation routes did not exist.
- Initial frontend RED: Node failed with `ERR_MODULE_NOT_FOUND` for the wished-for SSE decoder.
- Focused backend stream/cancellation: `6 passed, 25 warnings`.
- Task 1-4 affected Python regression: `85 passed, 230 warnings`.
- Final Task 3 checkpoint plus Task 4 stream check: `25 passed, 39 warnings`.
- Frontend SSE decoder: `2 passed`.
- Frontend ESLint: exit 0.
- Next.js production build: exit 0; TypeScript and all 12 static routes completed.
- Local Playwright memory/auth/session workflow: `4 passed` on Chromium.
- `git diff --check` and Python compile checks completed without errors. Line-ending notices are Git configuration warnings, not whitespace errors.

All Python test commands cleared uppercase HTTP/HTTPS/ALL/NO proxy variables, used `-o addopts=''`, and used workspace-local `--basetemp` paths. Tests selected `APP_ENV=test`, deterministic embeddings, and the test-only in-memory checkpoint runtime. No paid or remote LLM, embedding, or judge provider was invoked.

## Known live-E2E limitations

- The live browser run used SQLite plus the test-only in-memory checkpointer. PostgreSQL concurrency, saver connectivity, and production disconnect behavior still require Task 5/live infrastructure verification.
- The current LLM gateway is synchronous, so the adapter emits the final teacher answer as one `teacher.delta`; the frontend decoder supports arbitrarily fragmented and repeated delta events.
- The browser workflow covered authenticated streaming, refresh retry, server-generated sessions, and session create/delete. A hard socket disconnect is not reliably reproducible against the fast deterministic provider; focused service/API tests verify that the disconnect signal becomes a durable `cancelled` run and that explicit cancellation is owner-safe.

## Review fix round 1

- Moved managed success terminalization after checkpoint/history finalization. The existing pre-commit hook is now cancellation-only; if finalization raises, the managed row remains active long enough for the outer failure path to persist `failed`, so durable state and `run.failed` agree. Synchronous chat defaults and Task 3 commit-before-history behavior remain unchanged.
- Locked conversation new/select/delete controls on `busy.chat` from submission start, before `run.started` can arrive. The action callbacks enforce the same guard.
- Added per-request stream identity containing request ID, initiating thread, run ID, and abort controller. Event callbacks mutate UI only while both request and initiating thread are current; stale finalizers cannot clear a newer run.
- Changed explicit cancellation to capture the initiating request/controller before awaiting the cancellation API. A delayed cancel for run A aborts only A even if run B has become current.
- Normalized CRLF after decoded bytes are appended to the shared buffer, so a `\r`/`\n` pair split across chunks still becomes a frame delimiter.

Review RED evidence reproduced all four findings: checkpoint finalization emitted `run.failed` while the database remained `success`; the session selector stayed enabled while a stream request was held before its first event; request/cancel identity exports were absent; and three valid CRLF frames merged into malformed JSON when delimiters crossed chunks.

Review GREEN verification used cleared proxy variables and local test state:

- Backend streaming/checkpoint/conversation/memory regression: `70 passed, 229 warnings`.
- Focused terminal ordering plus cancellation preservation: `3 passed, 10 warnings`.
- Frontend SSE/request-race tests: `5 passed`.
- ESLint and Next.js production build: exit 0.
- Local Chromium memory/auth/session workflow, including the pre-event control lock: `5 passed`.
- `git diff --check`: exit 0 with only configured LF-to-CRLF notices.

No remote or paid provider was invoked. PostgreSQL/live hard-disconnect limitations remain as documented above.
