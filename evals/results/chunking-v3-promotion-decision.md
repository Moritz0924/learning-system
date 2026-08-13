# Hybrid Chunking V3 Promotion Decision

## Decision

**KEEP V2 DEFAULT.**

The implementation and offline algorithm checks are complete, but deterministic/mock results cannot satisfy the Promotion Gate. A provider-backed isolation Test run and a production-orchestrator A vs Best run are required before enabling `FEATURE_HYBRID_CHUNKING_V3=true`.

- Isolation Test dataset hash: `df3890baca2610a88f8410e7df1f3d7ec24880b549a65a292d78716bb035befb`
- Isolation Test gold hash: `b114d657f88ed08633b5e402260cbf7e421124b21e7e8596b6548919e8bf1c98`
- Isolation Test promotion eligible: `False`
- Production-like promotion eligible: `False`
- Performance run promotion eligible: `False`

Rollback/new-job control: set `FEATURE_HYBRID_CHUNKING_V3=false`. Existing queued retries keep their frozen execution snapshot.
