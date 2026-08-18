# Hybrid Chunking V3

Hybrid Chunking V3 is an additive, opt-in ingestion path. V2 remains the default:

```env
FEATURE_HYBRID_CHUNKING_V3=false
```

No database migration is required. Existing V2 parser output, chunk metadata, index identity, citations, and APIs remain compatibility paths. PDF/PPTX use Legacy V2 unless a task carries a frozen `structured_v3` execution snapshot.

## Runtime pipeline

```text
Document -> parser profile -> structural regions -> semantic boundaries
         -> final renderer -> token-safe size guard -> invariant check
         -> embedding -> explicit v2/v3 versioned index
```

The ordering is `Structure > Semantic > Target Size`, with `max_tokens` as the final hard constraint. Heading, code, and table transitions are hard boundaries; page and slide transitions are soft. Semantic embeddings consume raw semantic-unit text, while the retrieval embedding consumes final rendered heading context plus body.

## Profiles and snapshots

`LEGACY_V2` selects `document-parser-v3`; binary `STRUCTURED_V3` parsing selects `document-parser-v4.1`, while native Markdown/TXT uses `text-parser-v1`. The feature flag resolves only the default for a new job. `DocumentChunkingService` serializes strategy, parser profile, policy, policy version, fingerprint, and tokenizer into the outbox payload. Retries reconstruct that exact policy and reject a mismatched fingerprint; they do not re-read changed environment values and do not silently fall back to V2.

The V3 policy fingerprint covers semantic window/weights, MAD thresholding, size limits, tokenizer identity, heading rendering, table/code policy, batch size, and semantic-unit limits. V3 index builds use an explicit `chunk_schema_version=v3`, so V2 and V3 builds can coexist.

## Parser behavior

Structured PDF parsing is fail-soft. Table detection attempts PyMuPDF lines, PyMuPDF text, then a deterministic spatial row-banding heuristic. Every candidate is validated for row/column count, cell density, valid bbox, and non-empty text; invalid candidates are rejected. Confirmed table bbox overlap removes duplicate ordinary text.

Structured PPTX parsing emits title, body, paragraph, list, table, and image-description blocks. Reading order is deterministic row-banding, not a raw `(top, left)` sort, so two-column slides do not interleave incorrectly.

## Variants and evaluation

| Variant | Components |
| --- | --- |
| A | Legacy V2 |
| P | Structured parser + deterministic V2-style length chunks; diagnostic control only |
| B | Structure + size |
| C | Structure + local window + adaptive threshold + size |
| D | Structure + local window + adjacent relation + frozen Dev-calibrated threshold + size |
| E | Structure + local window + adjacent relation + adaptive threshold + size |

A–E are engineering candidates. P is attribution-only and must not be ranked as a V3 promotion candidate. Gold is represented by canonical `EvidenceAnchor` records. Retrieved chunks are mapped to anchors via canonical source spans/text; V3 `source_unit_ids` remain debug metadata only.

The promotion-candidate `chunking-v3-ablation-v2` fixture contract is 30 documents: 20 development and 10 test, with 80/40 queries and all queries/evidence for a document staying in the same split. The source distribution is 10 Markdown, 10 PDF, 5 PPTX, and 5 text fixtures. It covers Chinese, English, mixed-language content, cross-page/slide evidence, code, tables, long CJK text, oversized structural units, repeated evidence, and calibrated lexical template-leakage checks. Historical `chunking-v3-v1` is synthetic smoke-only and cannot be used for promotion. D calibration evaluates candidate thresholds only on Dev, writes the selected threshold plus dataset hash and calibration run ID, and Test can only read that artifact.

Isolation uses one independently identified index per variant, one embedding identity, vector-only retrieval, `top_n=20`, offline cutoffs `@1/@3/@5/@10`, and token budgets `512/1024/2048`. Paired bootstrap uses 1000 resamples and a fixed seed. Production-like Phase 2 may enable query rewrite, keyword retrieval, metadata fusion, RRF, reranking, and context selection only after Phase 1 selects a candidate.

Offline/deterministic runs are algorithm checks and are explicitly not Promotion Evidence. A real promotion run requires a provider-backed embedding identity, isolated evaluation database, reproducibility manifest, and explicit `--allow-remote`.

## Verification

```powershell
$env:HTTP_PROXY=$null
$env:HTTPS_PROXY=$null
$env:ALL_PROXY=$null
$env:NO_PROXY=$null

.\.venv\Scripts\python.exe -m pytest tests/rag tests/documents --basetemp .pytest-tmp-hybrid-v3
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-hybrid-v3-full
.\.venv\Scripts\python.exe -m compileall backend evals
.\.venv\Scripts\python.exe scripts/verify-chunking-v3-dataset.py
.\.venv\Scripts\python.exe scripts/run-chunking-v3-ablation.py --offline --phase isolation --dataset chunking-v3-ablation-v2 --variants A P B C D E
git diff --check
```

The production command must not be run with a deterministic adapter:

```powershell
.\.venv\Scripts\python.exe scripts/run-chunking-v3-ablation.py `
  --phase isolation --allow-remote --dataset chunking-v3-ablation-v2 --variants A P B C D E
```

The provider-backed Phase 1 command creates completed, non-active candidate index versions and reads them through the eval-only explicit-index retriever. It requires an isolated PostgreSQL/pgvector `EVALUATION_DATABASE_URL`, a remote embedding provider, and `--allow-remote`; do not run it without authorization for provider usage and the evaluation database. Phase 2 additionally requires the compatible formal Dev output used to select Best:

```powershell
.\.venv\Scripts\python.exe scripts/run-chunking-v3-ablation.py `
  --phase production --allow-remote --dataset chunking-v3-ablation-v2 `
  --candidate <B|C|D|E> --dev-result evals/results/chunking-v3-ablation-v2-dev.json
```

Phase 2 activates A and Best sequentially only in that isolated database, reuses the production `RetrievalOrchestrator`, records its source traces, and restores the prior active state in `finally`.

## Promotion and rollback

Promotion requires all V2 tests to pass; hard structure preservation, deterministic output, malformed/duplicate table rate, and final oversize violations to be zero; Test Recall@5 no worse than A by 0.5 percentage points; MRR and nDCG@5 no worse than A; at least one approved quality improvement; bootstrap intervals without unstable major regression; P95 ingestion no more than 2x A; and batched semantic embedding with no per-unit HTTP calls. Otherwise V2 remains production default and V3 stays experimental.

Rollback is the single environment change `FEATURE_HYBRID_CHUNKING_V3=false` for new jobs. Existing snapshots continue their recorded strategy; an operational retry should be allowed to finish or be explicitly re-enqueued with a new snapshot. V2 and V3 index versions remain isolated, so rollback does not require destructive index replacement.
