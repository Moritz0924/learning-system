# Hybrid Chunking V3 repair and production freeze

## Production freeze

- `PRODUCTION_ALGORITHM_FREEZE_SHA`: `6831ec999567817c6574d9f23786b3c3b964c383`
- Freeze scope: `backend/app/domain/rag/chunking/**` algorithm behavior and structured production parser behavior.
- This SHA re-freezes the scope after policy-restore validation and the production fix; subsequent evaluation commits validate the frozen implementation and do not modify `backend/app`.

## Repairs completed

- Versioned execution snapshots distinguish legacy V2 from Hybrid V3 and reject incompatible V3 implementation identities. Missing legacy snapshots are fixed to V2.
- V3 routes Markdown and text uploads through `StructuredTextParser`; enqueue remains pure configuration and does not initialize an embedding client.
- Structured PDF parsing restores native-text quality assessment, single-call OCR artifacts, spatial OCR blocks, native fallback, and table false-positive guards.
- Semantic chunking now has sentence splitting, constrained cross-page continuation, local boundary provenance, diagnostics, and no full semantic trace in production chunk metadata.
- Size Guard selects the weaker adjacent semantic boundary for tiny merges, preserves table/header and fenced-code structure during fallbacks, and enforces the final token bound.
- Rendering honors `include_heading_context`; slide titles become soft slide context; standalone V3 images are `image_description`; V3 citations use explicit page/slide/image/text locations.
- Algorithm fingerprints include the frozen component versions, and index identities use parser implementation, `hybrid-chunking-v3.1`, and the fingerprint.
- Hybrid errors now drive outbox retry semantics: embedding/provider failures retry, while snapshot, invariant, configuration, and structured-parser failures are terminal immediately.

## Final contracts

### Parser and chunker versions

- Binary structured parser: `document-parser-v4.1`
- Native Markdown/TXT parser: `text-parser-v1`
- Hybrid chunker: `hybrid-chunking-v3.1`
- Legacy identity remains `legacy-parser-v3:chunking-v2`.

### Frozen algorithm identities

- `structure-v3.1`
- `semantic-v3.1`
- `sentence-v3.1`
- `relations-v3.1`
- `renderer-v3.1`
- `size-v3.1`
- `table-v3.1`
- `code-v3.1`

The canonical V3 fingerprint is calculated from the complete `HybridChunkPolicy`, tokenizer identity, and this component-version map.

### Snapshot and provenance semantics

- V2 snapshots carry no V3 policy, fingerprint, or tokenizer fields.
- V3 snapshots persist implementation identity, policy, fingerprint, and tokenizer identity at enqueue time.
- V3 provenance records direct `file_type`, `source_format`, `processing_mode`, `source_element`, and `source_location_kind` values, plus aggregate source provenance and precise source spans.
- Citation locations are `page` for PDF, `slide` for PPTX, `image` for standalone images, and `text` for Markdown/TXT. Text citations do not fabricate a page number.

## Verification at freeze

- The policy-restore validation and production fix were verified before this scope was re-frozen; no provider-backed evaluation was run.
- Full repository test suite completed successfully in three isolated groups; each group reached `[100%]` and exited normally.
- V2 chunking, V2 ingestion, V3 parser/chunking, document-worker, index, and retrieval tests are included in that suite.
- `python -m compileall -q backend evals scripts` exited `0`.
- `git diff --check` exited `0`.

## Known limitations

- Promotion evidence is not yet established at this freeze. The previous `chunking-v3-v1` synthetic benchmark is smoke-only, not promotion evidence.
- Formal provider-backed evaluation requires a separately authorized isolated evaluation database, explicit remote permission, provider credentials, and cost confirmation.
- The next phase must construct the leakage-safe ablation corpus, calibrate D on development data only, run Phase 1 explicit-index isolation, and run Phase 2 through the real production retrieval orchestrator before any promotion decision.
