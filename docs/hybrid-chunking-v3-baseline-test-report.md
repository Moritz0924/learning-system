# Hybrid Chunking V3 Baseline Test Report

Baseline commit: `4887037`.

The baseline targeted run used the shared `.venv`, cleared HTTP proxy variables, and initially recorded 112 passed with 9 pre-existing local object-storage upload failures under a deep pytest temp path. The failure root cause was Windows path-length exhaustion, not the chunking code. Re-running the full suite with the project Windows short-path shield (`E:\codex-pytest-*`) passed.
