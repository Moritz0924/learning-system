# Task 1 report: contract and state boundary

## Delivered scope

- Extracted the tutor workflow services into focused modules while retaining every existing import from `adaptive_tutor.tutor.services` through a compatibility facade.
- Added strict, runtime-checkable domain dependency protocols and connected the Phase 2 dependency boundary to the new LLM, RAG, state, and memory-gate contracts.
- Added `LegacyTutorStateAdapter` as the ingress/egress bridge between the LangGraph legacy mapping and the canonical `TutorWorkflowState`. It owns UUID4 run correlation IDs and projects compatibility aliases only at the boundary.
- Replaced process-randomized retrieval hashing with a normalized, key-order-independent SHA-256 request hash and added the run ID and request hash to agent-run audit payloads.

## Changed files

- `src/adaptive_tutor/tutor/contracts.py` — structural Protocols for runtime dependencies.
- `src/adaptive_tutor/tutor/identifiers.py` — UUID4 generation and normalized request SHA-256.
- `src/adaptive_tutor/tutor/state.py` — legacy ingress/egress adapter.
- `src/adaptive_tutor/tutor/workflow_services.py` — grounding and routing services.
- `src/adaptive_tutor/tutor/context_services.py` — context loading, retrieval, and teaching services.
- `src/adaptive_tutor/tutor/learning_services.py` — assessment, observer, planning, memory, and action-building services.
- `src/adaptive_tutor/tutor/services.py` — compatibility re-exports.
- `src/adaptive_tutor/tutor/__init__.py` — adapter export.
- `src/adaptive_tutor/phase2/ports.py` and `src/adaptive_tutor/phase2/engine.py` — new contracts and state/hash integration.
- `tests/tutor/test_tutor_domain_contracts.py` and `tests/phase2/test_phase2_engine.py` — focused contract, adapter, UUID, normalized-hash, cross-process, and engine audit coverage.

## Test evidence

Red phase:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'; & 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .pytest-tmp-task1 tests\tutor\test_tutor_domain_contracts.py
```

Initially failed during collection because the new `adaptive_tutor.tutor.contracts` module did not exist. The engine regression test then failed with the expected missing `audit_payload["run_id"]` key.

Green / focused verification:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'; & 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .pytest-tmp-task1 tests\tutor\test_tutor_domain_contracts.py tests\phase2\test_phase2_engine.py tests\phase2\test_phase2_contracts.py tests\test_stage3_refactor_boundaries.py tests\memory\test_memory_context_tutor_integration.py
```

Result: `35 passed, 1 warning in 0.46s`. The warning is the existing FastAPI/Starlette `TestClient` deprecation warning.

`git diff --check` also completed without whitespace errors.

## Concerns

- A broad `pytest tests` run was started after focused verification but showed unrelated failures around 11% progress before the parent requested that this Task 1 agent return after focused coverage. It was not used as a Task 1 gate, and no unrelated failures were changed.
- `.pytest-tmp-baseline/` was already untracked before Task 1 work and was deliberately left untouched. `.pytest-tmp-task1/` is local test output and is not staged.

## Review fix round 1

- Removed the duplicated learning aliases from `TutorState` and prevented the legacy adapter from retaining `active_plan`, `current_task`, `mastery_snapshot`, or `recent_learning_events` after egress. The adapter still accepts those values only as legacy ingress input.
- Updated context loading and learning nodes to derive plan, task, and mastery exclusively from `TutorWorkflowState.learning`; a regression test injects conflicting legacy aliases and verifies assessment, grading, and planning still use the canonical values.
- Tightened the Protocol annotations to the concrete Phase 2 values (`TutorContext`, `RetrievedChunk`, assessment result/draft, and mastery update) and added type-hint assertions for those call signatures.
- Normalized mapping keys with the same NFC/newline rules as string values before canonical JSON serialization.

Review red/green command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'; & 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .pytest-tmp-task1 tests\tutor\test_tutor_domain_contracts.py
```

The new tests first failed for the retained aliases, generic Protocol annotations, and unnormalized mapping keys. Final focused verification was:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'; & 'E:\AI-chat\learning-system\learning-system\.venv\Scripts\python.exe' -m pytest --basetemp .pytest-tmp-task1 tests\tutor\test_tutor_domain_contracts.py tests\phase2\test_phase2_engine.py tests\phase2\test_phase2_contracts.py tests\test_stage3_refactor_boundaries.py tests\memory\test_memory_context_tutor_integration.py
```

Result: `38 passed, 1 warning in 0.46s` (the existing FastAPI/Starlette `TestClient` deprecation warning).
