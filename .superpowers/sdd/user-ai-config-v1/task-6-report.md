# Task 6 report: MCP transports, discovery, and safe read-only execution

## Status

Implemented the backend-only Task 6 scope on `codex/user-ai-config-v1`.

Implementation commit: `b7a0531 feat: add safe user MCP runtime`

## Files

- `backend/app/application/mcp_service.py`
  - One bounded MCP application service using the installed MCP SDK.
  - Streamable HTTP and direct stdio transport construction, timeout/cleanup bounds, URL/DNS/redirect policy, trust fingerprint enforcement, just-in-time Secret injection, pagination, transactional catalog replacement, JSON Schema validation, exact read-only classification, typed approval requirement, and sanitized/truncated output.
- `backend/app/routers/config.py`
  - Added owned `POST /api/config/mcp-servers/{id}/test` and `POST /api/config/mcp-servers/{id}/discover` endpoints with sanitized outcomes.
- `backend/app/services/tutor_tools.py`
  - Added owned enabled MCP tools to the per-request registry with server-derived collision-safe names while retaining the official-search registration.
- `backend/app/application/engine.py`
  - Passed the Tutor request `user_id` and request Secret store into registry construction under the existing feature-flag gate.
- `tests/test_mcp_application_service.py`
  - Added fake-session transport, URL/DNS, secrets, cleanup, pagination, catalog, invocation, ownership, registry, API, rollback, and output-limit coverage.
- `pyproject.toml`
  - Promoted `jsonschema` from dev-only to a runtime dependency because the application service imports it.

## TDD evidence

All commands used the shared interpreter `E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe`, cleared `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`, and used short `E:\codex-pytest-task6-*` base temp paths.

### RED

- `python -m pytest tests/test_mcp_application_service.py::test_mcp_application_module_exists -q --basetemp E:\codex-pytest-task6-red-module`
  - Failed as expected: MCP application module was absent.
- `python -m pytest tests/test_mcp_application_service.py -q --basetemp E:\codex-pytest-task6-red-service`
  - Failed as expected: five service/registry behaviors were absent.
- `python -m pytest tests/test_mcp_application_service.py::test_http_url_policy_allows_expected_hosts_and_rejects_metadata_resolution -q --basetemp E:\codex-pytest-task6-red-metadata`
  - Failed as expected: resolved cloud metadata addresses were accepted.
- `python -m pytest tests/test_mcp_application_service.py::test_config_api_test_and_discover_use_real_service_with_fake_sessions -q --basetemp E:\codex-pytest-task6-red-routes-2`
  - Failed as expected: the MCP session-factory seam and routes were absent.
- `python -m pytest tests/test_mcp_application_service.py::test_safe_invocation_validates_schema_classifies_exact_true_and_sanitizes -q --basetemp E:\codex-pytest-task6-red-echo`
  - Failed as expected: an echoed injected Secret value survived result sanitization.
- `python -m pytest tests/test_mcp_application_service.py::test_invalid_discovery_rolls_back_the_entire_catalog_replacement -q --basetemp E:\codex-pytest-task6-red-rollback`
  - Failed as expected: an invalid later tool allowed an earlier partial catalog update to commit.
- `python -m pytest tests/test_mcp_application_service.py::test_discovery_enforces_the_transport_output_limit_before_persisting -q --basetemp E:\codex-pytest-task6-red-output`
  - Failed as expected: an oversized discovered catalog was persisted.
- `python -m pytest tests/test_mcp_application_service.py::test_stdio_requires_current_trust_injects_env_and_closes_on_timeout -q --basetemp E:\codex-pytest-task6-red-trust-status`
  - Failed as expected: trust rejection was safe but did not yet return/persist the sanitized failed outcome.

### GREEN

- `python -m pytest tests/test_mcp_application_service.py -q --basetemp E:\codex-pytest-task6-green-focused-5`
  - `9 passed`.
- Required compatibility command:
  - `python -m pytest tests/test_mcp_application_service.py tests/test_user_ai_config_api.py tests/tutor/test_agent_production_tools.py tests/phase2/test_agent_tool_loop.py tests/phase2/test_agent_tool_evidence.py tests/tutor/test_tutor_streaming_api.py tests/test_mcp_server_smoke.py tests/test_p0_auth_and_runtime.py tests/test_runtime_resolver.py tests/test_thread3_runtime_config.py tests/auth/test_auth_flow.py tests/auth/test_legacy_password_activation.py tests/auth/test_principal_boundary.py -q --basetemp E:\codex-pytest-task6-final`
  - Exit `0`; `109` tests collected and passed.
- `python -m compileall -q backend/app/application/mcp_service.py backend/app/application/engine.py backend/app/routers/config.py backend/app/services/tutor_tools.py`
  - Exit `0`.
- `python -m pip check`
  - `No broken requirements found.`
- `git diff --check`
  - Exit `0`; only Git line-ending notices were emitted.

## Self-review

- Each test/discovery/call creates and closes one session. No pooling or list-changed listener was added.
- HTTP uses `follow_redirects=False`, rejects userinfo/secret queries/link-local and known metadata addresses (including DNS results), and permits localhost/private LAN/public targets.
- stdio uses SDK direct command/args with no shell, verifies command/args/cwd fingerprint before opening, bounds startup/call/output, captures bounded stderr, and relies on the SDK context manager's terminate-on-close behavior.
- Secret values are resolved only when building a connection, cleared from mutable connection maps after close, redacted if echoed, and never included in outcomes.
- Discovery rollback was verified against partial replacement; unchanged tool names retain their enabled state.
- Only `annotations.readOnlyHint is True` is classified as read-only. Invalid schemas/arguments do not reach transport; non-read-only tools return `ToolApprovalRequired` without opening a session.
- Registry names include a server-identity digest and a tool digest; legacy registration and existing feature flags remain intact.
- No output-schema column was added because the current model does not store one.

## Concerns and explicit exclusions

- No real external MCP endpoint was contacted, as required; SDK transport construction is exercised through the application boundary with fake sessions.
- Redirects are rejected conservatively rather than followed, including otherwise safe redirects.
- Existing Starlette and naive-UTC deprecation warnings remain; no new warning class was introduced.
- No LangGraph interrupt/resume, approval decision endpoint, durable approval execution, or frontend work was added.

## Review fix round 1

Implementation commit: `352ab21 fix: harden user MCP runtime boundaries`

### Addressed findings

- Normalizes IPv4-mapped IPv6 before metadata/link-local classification, including mapped Alibaba metadata.
- Resolves HTTP targets once, pins the validated address for the actual connection, and preserves the original Host header and TLS SNI. Redirect following remains disabled.
- Passes `include_mcp=FEATURE_MCP_TOOL_ROUTER_V2` explicitly; all four Agent Loop/MCP flag combinations are covered, and MCP rows are not queried when the MCP flag is off.
- Enforces the byte limit in raw HTTP response streams and raw stdio lines before MCP JSON decoding, including initialize responses. The stdio path retains direct command/args execution and SDK Windows process-tree termination helpers.
- Resolves each Secret once per operation for both transport injection and echoed-value sanitization. MCP/httpx/httpcore transport logs and stdio stderr are suppressed so raw protocol messages cannot reflect Secret values.
- Adds owned `POST /api/config/mcp-servers/{id}/trust` with a strict empty request object. The server computes command/args/cwd fingerprints and persists `trusted_at`; client-provided fingerprints are rejected. Any command, args, cwd, or transport change invalidates trust.
- Uses the full SHA-256 digest of the combined server/tool identity in names bounded to 128 characters, and fails closed on any registry collision.
- Propagates MCP normalization truncation into the existing `ToolResult.truncated` audit/observation path.

### RED evidence

- `python -m pytest tests/test_mcp_application_service.py -q --basetemp E:\codex-pytest-task6-review-red`
  - `12 failed, 7 passed` before the review fixes: mapped metadata, address pinning/rebinding, raw initialize bounds, transport log secrecy, single Secret resolution, feature gates, full digest/collision handling, truncation propagation, and trust confirmation all failed for the expected missing behavior.
- `python -m pytest tests/test_mcp_application_service.py::test_owned_stdio_trust_confirmation_computes_fingerprint_server_side -q --basetemp E:\codex-pytest-task6-trust-cwd-red`
  - Failed because changing only stdio cwd left the old trust fingerprint and timestamp visible.

### GREEN evidence

- `python -m pytest tests/test_mcp_application_service.py -q --basetemp E:\codex-pytest-task6-review-focused-final`
  - `19 passed`.
- Required compatibility command with the same 13 files recorded above and `--basetemp E:\codex-pytest-task6-review-verify`
  - Exit `0`; `119` tests collected and passed.
- `python -m compileall -q backend/app/application/mcp_service.py backend/app/application/engine.py backend/app/routers/config.py backend/app/services/tutor_tools.py src/adaptive_tutor/tutor/tool_router.py`
  - Exit `0`.
- `python -m pip check`
  - `No broken requirements found.`
- `git diff --check`
  - Exit `0`; only Git line-ending notices were emitted.

### Review concerns and exclusions

- No external MCP service was contacted. Raw transport tests use a local loopback HTTP server and a local Python stdio child, while application behavior continues to use fake MCP sessions where appropriate.
- The bounded stdio client relies on MCP SDK private Windows process helpers because the public `stdio_client` decodes stdout before exposing it; this dependency should be rechecked when upgrading MCP SDK versions.
- Redirects remain conservatively rejected rather than followed.
- Task 7 approval execution, LangGraph resume, approval endpoints, and frontend work remain excluded.

## Review fix round 2

Implementation commit: `464e0f4 fix: reject compressed MCP responses`

### Finding and resolution

- The HTTP byte stream counted compressed wire bytes, while httpx decompressed afterward; a small gzip body could therefore materialize a much larger JSON response inside the MCP SDK.
- The pinned HTTP boundary now rejects every non-identity `Content-Encoding` immediately after headers and before reading or decoding the response body. The existing `mcp.output_too_large` sanitized outcome is preserved.

### RED evidence

- `python -m pytest tests/test_mcp_application_service.py::test_http_rejects_compressed_response_before_large_decode -q --basetemp E:\codex-pytest-task6-gzip-red-2`
  - Failed as expected: a gzip initialize response with fewer than 1,000 wire bytes but more than 200,000 decoded bytes completed successfully.

### GREEN evidence

- Targeted: `python -m pytest tests/test_mcp_application_service.py::test_http_rejects_compressed_response_before_large_decode -q --basetemp E:\codex-pytest-task6-gzip-green`
  - `1 passed`.
- Focused: `python -m pytest tests/test_mcp_application_service.py -q --basetemp E:\codex-pytest-task6-gzip-focused`
  - `20 passed`.
- Required compatibility command with the same 13 files recorded above and `--basetemp E:\codex-pytest-task6-gzip-regression`
  - Exit `0`; `120` tests collected and passed.
- `python -m compileall -q backend/app/application/mcp_service.py tests/test_mcp_application_service.py`, `python -m pip check`, and `git diff --check`
  - All exited `0`; dependency check reported `No broken requirements found.`

### Concern

- Compressed MCP HTTP responses are intentionally unsupported. Identity/unencoded responses retain the existing raw byte-stream bound.
