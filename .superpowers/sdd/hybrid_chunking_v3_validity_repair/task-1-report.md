# Task 1 Report: Validate V3 execution-snapshot policies

## Scope and implementation

- Added construction-time `ValueError` invariants to `SemanticChunkPolicy`, `SizeGuardPolicy`, and `HybridChunkPolicy` in `backend/app/domain/rag/chunking/v3/config.py`.
- The shared constructors are used both by direct callers and by `HybridChunkPolicy.from_mapping()` during `ChunkingExecutionSnapshot.from_payload()` restoration.
- Tests in `tests/rag/test_chunking_execution_snapshot.py` cover invalid direct policy construction, valid one-zero similarity-weight ablations, invalid V3 payload restoration, and the existing service-level `HybridChunkingSnapshotIncompatible` conversion.

## TDD evidence

### RED

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_chunking_execution_snapshot.py --basetemp $taskTemp
```

Key output: `FFFFFFFFFFFFFFF......... [100%]`; all 15 new direct-construction cases failed with `Failed: DID NOT RAISE ValueError`, proving the missing policy-validation behavior before the production change.

### GREEN

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_chunking_execution_snapshot.py --basetemp $taskTemp
```

Key output: `........................ [100%]` (24 passed). The environment emitted only the pre-existing FastAPI/httpx deprecation warning.

## Focused regression verification

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-v3-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_hybrid_chunking_contracts_v3.py tests/rag/test_semantic_chunking_v3.py tests/rag/test_size_guard_v3.py --basetemp $taskTemp
```

Key output: `........................ [100%]` (24 passed), with only the same pre-existing FastAPI/httpx deprecation warning.

`git diff --check` completed with exit code 0 (no whitespace errors).

## Files

- `backend/app/domain/rag/chunking/v3/config.py`
- `tests/rag/test_chunking_execution_snapshot.py`
- `.superpowers/sdd/hybrid_chunking_v3_validity_repair/task-1-report.md`

## Self-review and concerns

- Controls require exact positive integer values; semantic weights remain independently bounded in `[0, 1]` and intentionally do not need to sum to 1.
- A zero value remains valid for either individual similarity weight when the other is positive, preserving ablation policies.
- The payload path propagates `ValueError` to the already-existing service boundary, which converts it to `HybridChunkingSnapshotIncompatible`.
- No evaluation files, dependencies, public APIs, V2 payload semantics, fingerprints, or feature-flag defaults were modified.
- Commit: one focused `fix(chunking): validate v3 snapshot policies` commit; the final SHA is reported after the report's final amendment.
