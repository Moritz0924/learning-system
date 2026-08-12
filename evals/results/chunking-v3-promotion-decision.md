# Hybrid Chunking V3 Promotion Decision

## Decision

**REJECT promotion. Keep V2 as production default.**

The implementation and offline algorithm checks are complete, but deterministic/mock results cannot satisfy the Promotion Gate. A provider-backed isolation Test run and production-like A vs Best run are required before enabling `FEATURE_HYBRID_CHUNKING_V3=true`.

- Isolation Test dataset hash: `7a8c63bc689efe0c0586f4f74f24eb2caf6ad648d96ad4404a8939d2123ece9f`
- Isolation Test gold hash: `60e05cd2e28c17b68968c331ad7d71e00146a9a0a47dd2810ba7f358a1b11b07`
- Isolation Test promotion eligible: `False`
- Production-like promotion eligible: `False`
- Performance run promotion eligible: `True`

Rollback/new-job control: set `FEATURE_HYBRID_CHUNKING_V3=false`. Existing queued retries keep their frozen execution snapshot.
