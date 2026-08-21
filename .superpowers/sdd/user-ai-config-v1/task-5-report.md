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
  - Skill combinations share the existing 8,192-character tutor request context budget with the user message and are rejected rather than partially truncated.
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

## Review fix round 1

### Implemented fixes

- Threaded the `SecretStore` dependency through both document upload endpoints, inline processing, outbox event processing, and the Celery worker. Owner-scoped vision and embedding bindings now resolve through `RuntimeResolver`; existing environment constructors remain the no-binding path.
- Required nonblank profile name/model fields and revalidated every persisted runtime profile. Explicit LLM and embedding clients no longer inherit environment models, and an explicit DeepSeek profile pins both flash/pro selection to its configured model.
- Added `StrictVisionClient`, mapping configured vision soft failures to sanitized `runtime.provider_call_failed`, and mapped `EvaluationProviderError` to the same stable SSE code without provider-body reflection.
- Replaced one-way embedding reindex dispatch with `queued` lease state, per-claim tokens, `available_at` expiry/reclaim, stale-release rejection, and recovery of legacy `dispatched` rows.
- Added shared provider URL building/canonicalization. Endpoint paths are appended before queries; canonical query semantics are shared by event fingerprints and index/provider identity. Query values, including terminal slashes, are preserved.
- Rejected signature, AWS/GCP presigned, and Azure SAS query credential names with generic non-reflective errors while preserving loopback/private endpoint support.
- Accounted Skill extensions and the user message against one 8,192-character request-context budget with an exact combined-boundary test.

### RED evidence

Initial review-finding specification:

`python -m pytest tests/test_provider_url_contract.py tests/test_runtime_resolver.py tests/test_user_ai_config_api.py tests/test_document_ingestion_worker.py tests/rag/test_embedding_reindex.py tests/tutor/test_tutor_streaming_api.py -q`

Result: `12 failed, 75 deselected, 29 warnings in 4.54s`. The failures covered missing query-safe URL composition/identity, persisted blank-profile validation, explicit DeepSeek model pinning, strict vision failure, blank API fields, signed URL rejection, document runtime/SecretStore threading, reindex lease reclaim, and stable SSE provider errors.

Additional combined Skill-budget specification:

`python -m pytest tests/tutor/test_user_skills.py::test_skill_instructions_share_the_existing_request_context_budget -q`

Result: `1 failed` with `TypeError: resolve_skill_selection() got an unexpected keyword argument 'context_chars_used'` before shared-budget support.

Expanded presigned/SAS name specification:

`python -m pytest tests/test_user_ai_config_api.py::test_model_profiles_reject_blank_identity_and_signed_secret_query_names -q`

Result: `1 failed`; `AWSAccessKeyId` was accepted with `201` before expanding the credential-name policy.

Terminal-query-value preservation specification:

`python -m pytest tests/test_provider_url_contract.py::test_llm_and_embedding_clients_use_shared_query_safe_builder -q`

Result: `1 failed`; explicit client construction stripped the terminal slash from a query value before the fix.

Independent-review race/re-enable/compound-query specification:

`python -m pytest -o addopts='' tests/test_document_ingestion_worker.py::test_document_ingestion_rechecks_embedding_identity_before_activation tests/test_user_ai_config_api.py::test_embedding_binding_change_enqueues_one_reindex_event_per_active_owned_document tests/test_user_ai_config_api.py::test_model_profiles_reject_blank_identity_and_signed_secret_query_names -q`

Result: `3 failed`. The pre-fix code activated after an in-flight embedding identity change, omitted a reindex event on bound profile re-enable, and accepted compound names such as `access_token`.

Cross-session identity-map specification:

`python -m pytest -o addopts='' tests/test_document_ingestion_worker.py::test_embedding_identity_recheck_refreshes_cross_session_profile_changes -q`

Result: `1 failed`; the final guard reused a strongly referenced stale ORM profile before `populate_existing` was added.

Pre-resolution identity-window specification:

`python -m pytest -o addopts='' tests/test_document_ingestion_worker.py::test_document_ingestion_rechecks_embedding_identity_before_activation -q`

Result: `1 failed`; snapshotting only after client resolution could miss a configuration mutation during resolution. The final implementation snapshots before resolution, validates immediately after it, and validates again from fresh locked state before activation.

### GREEN evidence

Affected fix-round suite:

`python -m pytest -o addopts='' tests/test_provider_url_contract.py tests/test_runtime_resolver.py tests/test_user_ai_config_api.py tests/test_document_ingestion_worker.py tests/rag/test_embedding_reindex.py tests/tutor/test_tutor_streaming_api.py tests/tutor/test_user_skills.py -q --disable-warnings --basetemp E:\codex-pytest-<pid>`

Final result: `94 passed, 468 warnings in 48.24s`.

Required compatibility matrix:

`python -m pytest tests/phase2/test_agent_tool_loop.py tests/tutor/test_agent_controller.py tests/tutor/test_tutor_streaming_api.py tests/tutor/test_conversation_persistence.py tests/rag/test_document_index_versions.py tests/rag/test_embedding_reindex.py tests/test_document_ingestion_worker.py tests/test_document_parsing.py tests/test_stage3_gateway_tools.py tests/auth tests/test_p0_auth_and_runtime.py tests/test_thread3_runtime_config.py tests/test_runtime_resolver.py tests/tutor/test_user_skills.py tests/test_user_ai_config_api.py tests/test_provider_url_contract.py -q --disable-warnings --basetemp E:\codex-pytest-<pid>`

Result: `242 passed, 882 warnings in 94.35s` before the final focused cross-session regression was added; the final 94-test affected suite above includes and passes that regression on the completed code.

Static verification:

- `python -m py_compile` passed for every changed production Python module.
- `git diff --check` reported no whitespace errors; only the repository's existing LF-to-CRLF notices were emitted.
- Final independent static re-review: `APPROVED`, with no remaining Critical or Important findings. Private/loopback endpoint access remains intentionally supported for Local-first providers.
- Final document ingestion/worker regression suite on the completed code: `47 passed, 301 warnings in 22.79s`.
