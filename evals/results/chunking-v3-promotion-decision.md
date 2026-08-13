# Hybrid Chunking V3 Promotion Decision

## Decision

**KEEP V2 DEFAULT.**

The implementation and offline algorithm checks are complete, but deterministic/mock results cannot satisfy the Promotion Gate. A provider-backed isolation Test run and a production-orchestrator A vs Best run are required before enabling `FEATURE_HYBRID_CHUNKING_V3=true`.

- Isolation Test dataset hash: `af757d45b15a36adccdba8d8a7f4daed3fbcdd29f0c6229bdc528d9abd91ed9e`
- Isolation Test gold hash: `2633612625d50a6d39115383ed6d3b81e8715e54f6ea1e051055fbb4417e7e92`
- Isolation Test promotion eligible: `False`
- Production-like promotion eligible: `False`
- Performance run promotion eligible: `False`

Rollback/new-job control: set `FEATURE_HYBRID_CHUNKING_V3=false`. Existing queued retries keep their frozen execution snapshot.
