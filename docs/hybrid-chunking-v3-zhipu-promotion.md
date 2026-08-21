# Hybrid Chunking V3 — Zhipu Embedding-3 promotion record

## Decision

`FEATURE_HYBRID_CHUNKING_V3` is enabled by default. New jobs may explicitly
set it to `false` to retain the frozen V2 execution snapshot.

## Reproducible remote evidence

- Dataset: `chunking-v3-ablation-v2` (`af757d45b15a36adccdba8d8a7f4daed3fbcdd29f0c6229bdc528d9abd91ed9e`)
- Provider identity: `openai-compatible:0413b53d28826c51b400bc9ebc578639bf6a4ff94d3e43fbcbc468bd51945602`
- Model and dimensions: `embedding-3` / `2048`
- Development isolation: `evals/results/chunking-v3-isolation-development.json`
- Frozen Test production-orchestrator result: `evals/results/chunking-v3-production-test.json`

The provider-backed development run selected B by Evidence nDCG@5.  The frozen
Test run used the production `RetrievalOrchestrator` in an isolated pgvector
database and retained the same provider, model, dimensions, dataset hash, and
gold corpus identity.

| Test metric @5 | A (V2) | B (V3) | Gate |
|---|---:|---:|---|
| Evidence Recall | 0.8750 | 0.9000 | B is not worse than A by 0.5 pp |
| MRR | 0.7500 | 0.8250 | no regression |
| Evidence nDCG | 0.7771 | 0.8899 | no regression and improved |
| Paired bootstrap nDCG delta CI | — | [0.0508, 0.1856] | no material unstable regression |

The per-document provider-backed ingestion check used the same isolated Test
corpus. A P95 was 0.464 s and B P95 was 0.335 s (0.72x A). B submitted 12
logical embedding inputs in 10 provider requests, with no scalar embedding
requests and a maximum batch size of 2. This satisfies the batching and P95
ingestion gate.

The existing V3 structure, table validation, deduplication, and final size
guard tests cover the hard structural invariant, malformed/duplicate table
rejection, and final overlong-chunk prevention. They are run again with the
default enabled as part of this release verification.

## Rollback

Set `FEATURE_HYBRID_CHUNKING_V3=false` for future jobs. Existing queued work
continues with its persisted execution snapshot and is not reinterpreted.
