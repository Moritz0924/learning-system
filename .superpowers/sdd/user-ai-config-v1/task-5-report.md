# Task 5 report: runtime resolution, Skills, and embedding reindex

## Status

Implemented the complete backend-only Task 5 scope on `codex/user-ai-config-v1`. No MCP discovery/execution/approval or frontend work was added.

## Commits

- `8007197` — `feat(config): add user runtime and skills`
- `fb92759` — `fix(config): enforce bound runtime isolation`

## Implementation

- Added `RuntimeResolver` as the single user/environment runtime selection boundary for chat, reasoning, vision, and embedding.
  - No binding preserves the existing environment constructors.
  - A binding/profile must be owned, enabled, capability-compatible, and have an accessible credential.
  - Bound LLM and embedding calls are strict and raise stable errors instead of using offline/degraded fallback.
  - Explicit profile construction supplies base URL, API key, model, and embedding dimensions.
  - Explicit non-DeepSeek LLM/vision profiles do not receive DeepSeek routing parameters.
  - Vision and embedding credentials remain separate; embedding profiles never borrow LLM credentials.
- Added `POST /api/config/models/{id}/test`.
  - Chat/reasoning uses a minimal strict completion.
  - Vision uses a valid 1x1 PNG request.
  - Embedding requires exactly 1536 values.
  - Only `last_test_status` and `last_tested_at` are persisted; responses contain stable sanitized status/code values.
- Added tutor `skill_ids` support for sync and streaming paths.
  - Omitted IDs select enabled `default_enabled` Skills.
  - Explicit IDs preserve request order and enforce ownership/enabled state.
  - User instructions are placed in a delimited extension after the immutable safety prompt.
  - Skill combinations that exceed the dedicated prompt budget are rejected rather than partially truncated.
  - Skill text is compared with the user's configured stored secret values and rejected on an exact secret match.
  - Skill model overrides must reference one owned enabled chat/reasoning profile; conflicting overrides are rejected.
- Added embedding reindex outbox/worker flow.
  - Binding changes and bound embedding identity changes enqueue one event per owned successful document with an active index.
  - Dedupe is per binding/configuration change while allowing a later change back to the same profile.
  - Events carry user, document, profile ID, and immutable embedding configuration fingerprint.
  - The worker reuses active chunks and the active chunker version through `DocumentIndexService`.
  - Final activation locks a stable user configuration row, rechecks binding plus fingerprint, and is committed atomically with event success.
  - Stale events never activate; failed builds keep the old active index and store only a stable event error code.
- Preserved legacy request metadata (including `metadata.tool_request`), streaming/durable run behavior, transactions, and environment-only operation.

## Files

Production:

- `backend/app/application/config_service.py`
- `backend/app/application/embedding_reindex_service.py`
- `backend/app/application/engine.py`
- `backend/app/application/tutor_service.py`
- `backend/app/application/tutor_stream_service.py`
- `backend/app/api/schemas/tutor.py`
- `backend/app/routers/config.py`
- `backend/app/routers/tutor.py`
- `backend/app/services/llm_gateway.py`
- `backend/app/services/vision_understanding.py`
- `backend/app/worker.py`
- `src/adaptive_tutor/phase2/schemas.py`

Tests:

- `tests/test_runtime_resolver.py`
- `tests/tutor/test_user_skills.py`
- `tests/rag/test_embedding_reindex.py`
- `tests/test_user_ai_config_api.py`

## TDD evidence

### RED

1. Initial focused specification:

   `python -m pytest tests/test_runtime_resolver.py tests/tutor/test_user_skills.py tests/rag/test_embedding_reindex.py tests/test_user_ai_config_api.py ... -q`

   Result: 13 expected failures for missing resolver/services/routes and rejected `skill_ids`.

2. Worker dispatch specification:

   `python -m pytest tests/rag/test_embedding_reindex.py -k 'claim_is_single_dispatch or worker_embedding_task' ... -q`

   Result: 2 expected failures for absent outbox claim and Celery handler.

3. Binding-to-outbox integration:

   `python -m pytest tests/test_user_ai_config_api.py -k 'embedding_binding_change_enqueues' ... -q`

   Result: expected `0 != 1` event failure before route wiring.

4. Review hardening:

   `python -m pytest ... -k 'bound_embedding_provider_failure or explicit_non_deepseek_vision or embedding_binding_change_enqueues' ...`

   Result: 3 expected failures for strict embedding errors, explicit vision routing, and bound profile identity updates.

5. Secret/budget guard:

   `python -m pytest tests/tutor/test_user_skills.py -k stored_secrets ...`

   Result: expected failure before SecretStore-aware Skill selection existed.

### GREEN

Focused Task 5 suite:

`python -m pytest tests/test_runtime_resolver.py tests/tutor/test_user_skills.py tests/rag/test_embedding_reindex.py tests/test_user_ai_config_api.py --basetemp E:\codex-pytest-task5-focused-final --disable-warnings`

Result before final review additions: `27 passed`; final additions were included in the full matrix below.

Full required compatibility matrix plus vision compatibility:

`python -m pytest tests/phase2/test_agent_tool_loop.py tests/tutor/test_agent_controller.py tests/tutor/test_tutor_streaming_api.py tests/tutor/test_conversation_persistence.py tests/rag/test_document_index_versions.py tests/rag/test_embedding_reindex.py tests/test_document_ingestion_worker.py tests/test_document_parsing.py tests/test_stage3_gateway_tools.py tests/auth tests/test_p0_auth_and_runtime.py tests/test_thread3_runtime_config.py tests/test_runtime_resolver.py tests/tutor/test_user_skills.py tests/test_user_ai_config_api.py --basetemp E:\codex-pytest-task5-final --disable-warnings`

Result: `229 passed, 836 warnings in 86.17s`. Warnings are existing Starlette and naive-UTC deprecations; there were no failures.

`git diff --check` was clean apart from Git's existing LF-to-CRLF notices.

## Independent review and self-review

- Independent review identified a blocking configured-embedding degradation path. Fixed by wrapping bound embedding calls with stable `runtime.provider_call_failed` behavior while leaving environment-only retrieval degradation unchanged.
- Review also identified mutable-profile and activation races. Fixed with immutable embedding fingerprints, reindex-on-bound-identity-update, final fingerprint recheck, and user-row locking shared with embedding binding mutations.
- Explicit non-DeepSeek vision profiles now omit thinking fields while the legacy environment GLM behavior remains compatible.
- Stored configured secret values are never returned and cannot be copied verbatim from a Skill into the provider prompt.
- Provider response bodies are not persisted or returned by model tests or reindex events.
- Agent-loop, streaming, persistence, index/worker, gateway, auth, and runtime compatibility suites passed.

## Concerns

- The pre-existing user-config API permits arbitrary model base URLs. The new test/runtime calls therefore inherit the product's endpoint trust-policy question (for example private-network endpoints versus SSRF protection). This task did not invent a private-address block because local/self-hosted providers may be an intended use case; an administrator-controlled endpoint allow policy should be decided explicitly.
- The 836 warnings are pre-existing deprecations and were not expanded into this bounded change.
