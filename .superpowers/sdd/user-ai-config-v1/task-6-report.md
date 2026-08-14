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
