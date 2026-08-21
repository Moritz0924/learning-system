# DeepSeek and Zhipu Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use DeepSeek V4 Flash/Pro for tutor reasoning and Zhipu GLM-4.5V for document and MCP vision without sharing provider credentials.

**Architecture:** Extend the existing HTTP clients instead of adding provider SDKs. The tutor gateway selects Flash by default and Pro for explicit repair work, while the document parser and MCP tools use an independently configured vision client. Keep embeddings independently configured because DeepSeek does not provide the project's embedding endpoint.

**Tech Stack:** Python 3.10+, FastAPI, httpx, MCP FastMCP, pytest, Docker Compose.

## Global Constraints

- Preserve the existing `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` interface as compatibility fallbacks.
- Preserve existing tutor, streaming, grounding, document parsing, and `metadata.tool_request` behavior.
- Do not add a DeepSeek or Zhipu SDK; reuse `httpx`.
- Never put API keys in `.env.example`, tests, logs, or committed documentation.
- Remote API smoke tests require user-provided keys and are not run implicitly.

---

### Task 1: DeepSeek Flash/Pro reasoning gateway

**Files:**
- Modify: `backend/app/services/llm_gateway.py`
- Modify: `src/adaptive_tutor/phase2/engine.py`
- Test: `tests/test_stage3_gateway_tools.py`

**Interfaces:**
- Consumes: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `DEEPSEEK_FLASH_MODEL`, `DEEPSEEK_PRO_MODEL`, and `DEEPSEEK_REASONING_EFFORT`.
- Produces: `LLMGatewayClient.complete(..., model_tier: str | None = None) -> str` with Flash default and Pro selection for repair calls.

- [x] Add tests proving Flash is the default, Pro can be selected, and DeepSeek requests include enabled thinking plus reasoning effort.
- [x] Run the focused gateway tests and confirm they fail because model-tier routing is absent.
- [x] Add the minimum model selection and DeepSeek payload fields.
- [x] Route grounding repair calls to Pro while retaining the existing TypeError compatibility fallback.
- [x] Run gateway and structured-engine tests.

### Task 2: Independent Zhipu vision and MCP runtime

**Files:**
- Modify: `backend/app/services/vision_understanding.py`
- Modify: `backend/app/services/embeddings.py`
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/app/core/runtime_config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Test: `tests/test_document_parsing.py`
- Test: `tests/test_mcp_server_smoke.py`
- Test: `tests/test_e2e_runner_contracts.py`
- Test: `tests/test_p0_auth_and_runtime.py`
- Test: `tests/test_stage3_gateway_tools.py`

**Interfaces:**
- Consumes: `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL`, `VISION_ENABLED`, `MCP_HOST`, and `MCP_PORT`.
- Produces: Zhipu-compatible Base64 image requests through the existing `VisionClient`, plus the existing `ocr_image` and `parse_document` tools on an independently addressable MCP service.

- [x] Add tests proving vision does not inherit `LLM_*`, Zhipu requests use the independent endpoint/key/model, and malformed/fenced model text is handled safely.
- [x] Run focused vision tests and confirm they fail because independent configuration is absent.
- [x] Implement independent vision configuration and robust final-content JSON extraction.
- [x] Restrict embedding credential fallback to the exact same provider endpoint and require an independent key across providers.
- [x] Configure MCP host/port and add the Compose MCP service.
- [x] Run document, MCP, readiness, embedding, and Compose contract tests.

### Task 3: Operator documentation and verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the environment variables and services from Tasks 1-2.
- Produces: exact local/Compose startup instructions and a safe key-placement example.

- [x] Document root `.env` values, model routing, MCP endpoint, and the independent embedding requirement.
- [x] Run `docker compose config --quiet` without starting paid providers.
- [x] Run the focused regression suite with cleared proxy variables and a short Windows pytest temp path.
- [x] Run the full backend test suite and inspect the final Git diff.
