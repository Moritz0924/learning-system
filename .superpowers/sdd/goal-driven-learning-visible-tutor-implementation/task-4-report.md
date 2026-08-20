# Task 4 Report: 前端可见导师、动态路线与资料

## Scope delivered

- Tutor UI now shows the learner turn immediately, public prepare/retrieve/write phases through `aria-live`, multi-delta text with a cursor, server-final answer replacement only on `run.completed`, sanitized failure codes, retry, cancel, approval resume, and `/ai-config` recovery.
- Tutor retries reuse the captured question, selected skill IDs, and explicit memory declaration (including its UUID). Real-goal restoration and initialization clear demo chat; demo mode remains explicitly labeled.
- Onboarding now uses only `POST /api/onboarding/dynamic-drafts` followed by `POST /api/onboarding/initialize-from-draft`. Goal/preferences and answers survive safe failures, unchanged retries reuse their request UUIDs, and changing input/answers starts a new idempotency request.
- Logged-in roadmap UI is derived only from `state.roadmap.stages`; the fixed `pathNodes` data was removed. `roadmap: null` links to `/diagnosis` with the localized reassessment action.
- Learning-source search now uses `POST /api/tools/search-learning-sources`; `source_level=web` is labeled as external/unverified, unavailable search remains safe/localized, and upload remains available.
- All new UI copy is present in `zh-CN` and `en-US`. User input, server roadmap copy, diagnostic copy, and source text are rendered unchanged.
- Playwright dynamic onboarding, tutor streams, roadmap, search, and memory-dependent frontend flows use browser routes/deterministic fixtures. No production offline fallback or backend test backdoor was added.

## TDD RED evidence

1. `npm run test:unit`
   - Exit 1 before production changes.
   - Expected failures: `reduceTutorRunView`/`startTutorRunView` exports did not exist; `tutor.phase.preparing` and the other new bilingual keys resolved to their raw key names.
2. `npm run test:e2e -- diagnosis-workflow.spec.ts --grep "creates locale-aware"`
   - Exit 1 before production changes.
   - Expected failure: `data-testid="diagnosis-form-ready"` did not exist because the UI still depended on the static template flow.
3. `pytest tests/test_frontend_learning_provider_contracts.py tests/test_frontend_diagnosis_contracts.py tests/test_e2e_runner_contracts.py -q`
   - After the product contract changed, the focused contract suite reported three expected stale-test failures for the removed static diagnostic template, legacy `/api/onboarding/initialize`, and official-only source endpoint. Those frontend contract assertions were migrated to the Task 4 APIs.

## GREEN verification evidence

- `npm run test:unit` — exit 0, 20/20 tests passed.
- `npm run lint` — exit 0.
- `npm run build` — exit 0; TypeScript passed and 13 application routes were generated.
- `npm run test:ui-routes` — exit 0, `UI route verification passed.`
- `npm run test:e2e` — exit 0, 26/26 Playwright tests passed in 44.3s.
- `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_frontend_learning_provider_contracts.py tests\\test_frontend_diagnosis_contracts.py tests\\test_e2e_runner_contracts.py -q --basetemp .tmp\\pytest-task4-contracts-final` — exit 0, 28/28 passed.
- `git diff --check` — exit 0; only Git's existing LF-to-CRLF working-copy warnings were printed.

## Self-review

- Confirmed no frontend references remain to `pathNodes`, `/api/onboarding/diagnostic-template`, `/api/onboarding/initialize`, or `/api/tools/search-official-learning-sources`.
- Confirmed raw tutor/model/source error messages are not rendered; only localized safe text and sanitized `tutor.*`, `runtime.*`, or `mcp.*` codes are exposed.
- Confirmed external result and citation links use a new tab with `noopener noreferrer`.
- Confirmed no backend, migration, dependency, or production-runtime fallback change is included.
- The only verification warning is the pre-existing Starlette `httpx` deprecation warning in the Python contract run.

## Review round 1: conversation-view isolation

### TDD RED evidence

- Added an E2E regression that creates a second Tutor thread, returns to the original thread, produces a visible learner question, partial answer, failed public run state, and retry action, then deletes that active thread and selects the remaining thread.
- The first focused invocation exposed an ambiguous unscoped retry locator because the existing page intentionally renders retry in two panels. The assertion was scoped to `tutor-failure` before recording the product RED.
- `npm run test:e2e -- --grep "deleting the active conversation"` — exit 1 before the production change. The replacement thread was selected, but `getByTestId('tutor-user-turn')` remained at count 1 instead of 0 for the full 15-second assertion window.

### GREEN implementation and verification evidence

- Added one `resetTutorConversationView()` callback shared by create, select, and active-delete/replacement branches. It clears chat text, public phase/error/question state, tool approvals, active/completed run bookkeeping, and `lastTutorAttemptRef` before the new thread becomes active.
- Focused Playwright: `npm run test:e2e -- --grep "deleting the active conversation"` — exit 0, 1/1 passed.
- Frontend unit: `npm run test:unit` — exit 0, 20/20 passed.
- Frontend lint: `npm run lint` — exit 0.
- Frontend production build: `npm run build` — exit 0; TypeScript passed and 13 routes were generated.
- Route verification: `npm run test:ui-routes` — exit 0, `UI route verification passed.`
- Frontend contract suite: `..\\..\\.venv\\Scripts\\python.exe -m pytest tests\\test_frontend_learning_provider_contracts.py tests\\test_frontend_diagnosis_contracts.py tests\\test_e2e_runner_contracts.py -q --basetemp .tmp\\pytest-task4-review1` — exit 0, 28/28 passed with the pre-existing Starlette `httpx` deprecation warning.
- Full Playwright: `npm run test:e2e` — exit 0, 27/27 passed in 43.6s.
