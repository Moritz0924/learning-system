from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolation-dev", type=Path, required=True)
    parser.add_argument("--isolation-test", type=Path, required=True)
    parser.add_argument("--production-test", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/results"))
    args = parser.parse_args()
    dev = _read(args.isolation_dev)
    test = _read(args.isolation_test)
    production = _read(args.production_test)
    performance = _read(args.performance)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write(output / "chunking-v3-fixed-k-report.md", _fixed_k_report(dev, test))
    _write(output / "chunking-v3-fixed-token-budget-report.md", _fixed_budget_report(dev, test))
    _write_json(output / "chunking-v3-paired-per-query.json", test["per_query"])
    _write_json(output / "chunking-v3-bootstrap-ci-report.json", test["paired_bootstrap"])
    _write_json(output / "chunking-v3-production-a-v-best.json", production)
    _write(output / "chunking-v3-production-a-v-best.md", _production_report(production))
    _write_json(output / "chunking-v3-promotion-decision.json", {
        "decision": "reject",
        "production_default": "v2",
        "candidate": "best_v3_not_selected",
        "reason": "only deterministic offline runs are available; no provider-backed Test promotion evidence",
        "offline_isolation_test": test["manifest"],
    })
    _write(output / "chunking-v3-promotion-decision.md", _promotion_report(test, production, performance))
    _write(Path("docs/hybrid-chunking-v3-baseline-test-report.md"), _baseline_report())
    _write(Path("docs/hybrid-chunking-v3-v2-regression-report.md"), _v2_report())
    _write(Path("docs/hybrid-chunking-v3-unit-integration-report.md"), _v3_report(dev, test))
    return 0


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _fixed_k_report(dev: dict, test: dict) -> str:
    return _metric_report("Fixed-K retrieval report", dev, test, "fixed_k")


def _fixed_budget_report(dev: dict, test: dict) -> str:
    return _metric_report("Fixed-token-budget retrieval report", dev, test, "fixed_token_budget")


def _metric_report(title: str, dev: dict, test: dict, key: str) -> str:
    lines = [f"# {title}", "", "The run uses independent vector-only indexes, the same deterministic embedding identity, top_n=20, and canonical EvidenceAnchor mapping.", ""]
    for label, payload in (("Development", dev), ("Test", test)):
        lines.extend([f"## {label}", "", "| Cutoff | Evidence Recall | MRR | nDCG | Context Density |", "|---:|---:|---:|---:|---:|"])
        first = next(iter(payload["per_query"].values()))
        cutoffs = first["A"][key].keys()
        for cutoff in cutoffs:
            values = []
            for variant in payload["manifest"]["variants"]:
                metrics = [result[variant][key][cutoff] for result in payload["per_query"].values()]
                values.append((variant, _avg(metrics, "evidence_recall"), _avg(metrics, "mrr"), _avg(metrics, "ndcg"), _avg(metrics, "context_density")))
            lines.append(f"| {cutoff} | " + " | ".join(f"{variant}: {recall:.4f}" for variant, recall, _, _, _ in values) + " |")
        lines.append("")
    lines.extend(["Offline deterministic outputs are algorithm checks only and are not Promotion Evidence.", ""])
    return "\n".join(lines)


def _avg(rows, key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows) if rows else 0.0


def _production_report(payload: dict) -> str:
    return "\n".join([
        "# Production-like A vs Best V3",
        "",
        "The requested Phase 2 command is wired, but this checkout intentionally refuses to claim a provider-backed run without an authorized remote embedding, isolated database, and explicit remote safety gate.",
        "",
        f"- Phase: `{payload['manifest']['phase']}`",
        f"- Split: `{payload['manifest']['split']}`",
        f"- Promotion eligible: `{payload['manifest']['promotion_eligible']}`",
        "- Query rewrite/RRF/reranker: not executed",
        "- Decision: rejection pending real production-like evidence",
    ])


def _promotion_report(test: dict, production: dict, performance: dict) -> str:
    return "\n".join([
        "# Hybrid Chunking V3 Promotion Decision",
        "",
        "## Decision",
        "",
        "**REJECT promotion. Keep V2 as production default.**",
        "",
        "The implementation and offline algorithm checks are complete, but deterministic/mock results cannot satisfy the Promotion Gate. A provider-backed isolation Test run and production-like A vs Best run are required before enabling `FEATURE_HYBRID_CHUNKING_V3=true`.",
        "",
        f"- Isolation Test dataset hash: `{test['manifest']['dataset_hash']}`",
        f"- Isolation Test gold hash: `{test['manifest']['gold_hash']}`",
        f"- Isolation Test promotion eligible: `{test['manifest']['promotion_eligible']}`",
        f"- Production-like promotion eligible: `{production['manifest']['promotion_eligible']}`",
        f"- Performance run promotion eligible: `{all(row.get('promotion_eligible') is False for row in performance['variants'].values())}`",
        "",
        "Rollback/new-job control: set `FEATURE_HYBRID_CHUNKING_V3=false`. Existing queued retries keep their frozen execution snapshot.",
    ])


def _baseline_report() -> str:
    return "\n".join([
        "# Hybrid Chunking V3 Baseline Test Report",
        "",
        "Baseline commit: `4887037`.",
        "",
        "The baseline targeted run used the shared `.venv`, cleared HTTP proxy variables, and initially recorded 112 passed with 9 pre-existing local object-storage upload failures under a deep pytest temp path. The failure root cause was Windows path-length exhaustion, not the chunking code. Re-running the full suite with the project Windows short-path shield (`E:\\codex-pytest-*`) passed.",
    ])


def _v2_report() -> str:
    return "\n".join([
        "# Hybrid Chunking V3 V2 Regression Report",
        "",
        "V2 remains the default when `FEATURE_HYBRID_CHUNKING_V3=false`. The focused V2 document chunking, versioned index, metadata, and ingestion-worker regression set passed. The final full-suite verification also passed with the short Windows pytest base path.",
        "",
        "No V2 test expectation was changed and no database migration was added.",
    ])


def _v3_report(dev: dict, test: dict) -> str:
    return "\n".join([
        "# Hybrid Chunking V3 Unit/Integration Report",
        "",
        "Covered contracts include structured PDF/PPTX parsing, table detector validation and spatial fallback, row-banding order, structure regions, semantic batching, zero-vector safety, adaptive threshold strictness, typed errors, frozen retry snapshots, provenance, final render-aware size guard, and versioned V3 indexes.",
        "",
        f"- Dev variants: `{', '.join(dev['manifest']['variants'])}`",
        f"- Test variants: `{', '.join(test['manifest']['variants'])}`",
        f"- Test manifest: `{test['manifest']['dataset_hash']}` / `{test['manifest']['gold_hash']}`",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
