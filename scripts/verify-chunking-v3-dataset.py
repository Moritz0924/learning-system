from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.chunking_v3 import validate_document_split
from evals.chunking_v3_dataset import build_fixture_bundle


def main() -> int:
    bundle = build_fixture_bundle()
    dataset = bundle.dataset
    errors = validate_document_split(dataset.documents, dataset.queries)
    if len(dataset.documents) != 30:
        errors.append("dataset must contain exactly 30 documents")
    if len(dataset.queries) != 30:
        errors.append("dataset must contain exactly 30 queries")
    if sum(document.split == "development" for document in dataset.documents) != 20:
        errors.append("development split must contain exactly 20 documents")
    if sum(document.split == "test" for document in dataset.documents) != 10:
        errors.append("test split must contain exactly 10 documents")
    expected_types = {"markdown": 10, "pdf": 10, "pptx": 5, "text": 5}
    actual_types = {
        source_type: sum(document.source_type == source_type for document in dataset.documents)
        for source_type in expected_types
    }
    if actual_types != expected_types:
        errors.append(f"source type distribution mismatch: {actual_types}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(json.dumps({
        "dataset_version": dataset.dataset_version,
        "dataset_hash": dataset.dataset_hash,
        "gold_hash": dataset.gold_hash,
        "documents": len(dataset.documents),
        "queries": len(dataset.queries),
        "source_types": actual_types,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

