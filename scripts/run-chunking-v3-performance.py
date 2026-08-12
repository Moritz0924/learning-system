from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.chunking_v3_dataset import build_fixture_bundle
from evals.chunking_v3_runner import build_variant_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure cold/warm Hybrid Chunking V3 ingestion separately from quality runs.")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--variants", nargs="+", default=["A", "P", "B", "C", "D", "E"])
    parser.add_argument("--output", type=Path, default=Path("evals/results/chunking-v3-performance.json"))
    args = parser.parse_args()
    if not args.offline:
        parser.error("performance fixture run requires --offline; provider-backed benchmarking must be explicitly integrated")
    bundle = build_fixture_bundle()
    documents = tuple((document, bundle.sources[document.document_id]) for document in bundle.dataset.documents if document.split == "development")
    rows = {}
    for variant in args.variants:
        first_started = time.perf_counter()
        cold = build_variant_index(documents, variant=variant)
        cold_wall = time.perf_counter() - first_started
        second_started = time.perf_counter()
        warm = build_variant_index(documents, variant=variant)
        warm_wall = time.perf_counter() - second_started
        rows[variant] = {
            "cold": {**cold.timings, "wall_seconds": cold_wall},
            "warm": {**warm.timings, "wall_seconds": warm_wall},
            "cache_hit_rate": 0.0,
            "index_bytes": sum(len(chunk.content.encode("utf-8")) for chunk in cold.chunks),
            "promotion_eligible": False,
        }
    output = {
        "dataset_version": bundle.dataset.dataset_version,
        "dataset_hash": bundle.dataset.dataset_hash,
        "offline": True,
        "quality_and_performance_separated": True,
        "variants": rows,
        "note": "Deterministic offline timings are diagnostic only and are not Promotion Evidence.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output), "promotion_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
