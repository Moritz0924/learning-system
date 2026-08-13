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

## Fix round 1: review follow-up

### Scope and implementation

- Removed `from_mapping()`'s pre-validation `int()` conversion for `semantic_batch_size`, allowing the existing exact-positive-integer invariant to reject floats, booleans, strings, and infinity as `ValueError`.
- Made `mad_multiplier` require a positive finite numeric value, rejecting both `nan` and infinity.
- Kept the local-plus-adjacent invariant and changed the B ablation runner's unused semantic policy to its legal equivalent (`local_window_weight=1.0`, all other B semantics remain bypassed), preserving Structure + Size behavior.

### RED

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-red-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_chunking_execution_snapshot.py tests/evaluation/test_chunking_v3_dataset.py -k 'mapping_rejects_non_integer_batch_size or maps_non_finite_or_wrong_type_policy_to_incompatible or policies_reject_invalid_direct_construction or variant_b_runs_structure' --basetemp $taskTemp
```

Key output: 8 failures. `nan` and infinity MAD direct-construction cases did not raise; `1.5`, `True`, and `"8"` batch sizes were silently accepted; infinity leaked `OverflowError`; and B failed by constructing all-zero similarity weights.

### GREEN

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-green-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_chunking_execution_snapshot.py tests/evaluation/test_chunking_v3_dataset.py -k 'mapping_rejects_non_integer_batch_size or maps_non_finite_or_wrong_type_policy_to_incompatible or policies_reject_invalid_direct_construction or variant_b_runs_structure' --basetemp $taskTemp
```

Key output: `............................ [100%]` (28 passed). Only the pre-existing FastAPI/httpx deprecation warning appeared.

### Final focused verification

Command:

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY=''; $taskTemp = "E:/codex-pytest-policy-final-round1-$PID"; & 'E:/AI-chat/learning-system/learning-system/.venv/Scripts/python.exe' -m pytest -q tests/rag/test_chunking_execution_snapshot.py tests/rag/test_hybrid_chunking_contracts_v3.py tests/rag/test_semantic_chunking_v3.py tests/rag/test_size_guard_v3.py tests/evaluation/test_chunking_v3_dataset.py::test_variant_b_runs_structure_and_size_without_constructing_an_invalid_semantic_policy --basetemp $taskTemp
```

Key output: `............................................................. [100%]` (61 passed). `git diff --check` completed with exit code 0.

### Files and self-review

- Modified: `backend/app/domain/rag/chunking/v3/config.py`, `evals/chunking_v3_runner.py`, `tests/rag/test_chunking_execution_snapshot.py`, and `tests/evaluation/test_chunking_v3_dataset.py`.
- The service's existing `ValueError` to `HybridChunkingSnapshotIncompatible` conversion now covers all newly rejected payload values; no `OverflowError` can escape this batch-size path.
- B still bypasses `semantic.split_with_trace()` and only applies structure plus size guard; its legal semantic policy is not behaviorally consulted.
- No dataset asset or evaluation result report was changed.
