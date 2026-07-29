from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"
CHECKED_IN_MAP = ROOT / "evals" / "generated" / "learning_qa_v1_chunk_map.json"


def test_gold_map_is_built_from_complete_corpus_and_is_deterministic() -> None:
    from evals.runner.gold_chunk_map import build_gold_chunk_map, gold_chunk_map_json

    first = build_gold_chunk_map(DATASET, corpus_dir=CORPUS)
    second = build_gold_chunk_map(DATASET, corpus_dir=CORPUS)

    assert first == second
    serialized = json.loads(gold_chunk_map_json(first))
    assert all(
        group["acceptable_chunk_ids"] == sorted(group["acceptable_chunk_ids"])
        for case in serialized["cases"].values()
        for group in case["evidence_groups"]
    )
    assert len(first.cases) == 35
    assert all(group.acceptable_chunk_ids for case in first.cases.values() for group in case.evidence_groups)
    assert all(
        chunk_id.startswith("eval-chunk-")
        for case in first.cases.values()
        for group in case.evidence_groups
        for chunk_id in group.acceptable_chunk_ids
    )


def test_checked_in_v1_gold_map_and_acceptable_chunk_ids_remain_valid() -> None:
    from evals.models import GoldChunkMap
    from evals.runner.gold_chunk_map import build_gold_chunk_map, validate_gold_chunk_map

    checked_in = GoldChunkMap.model_validate_json(CHECKED_IN_MAP.read_text(encoding="utf-8"))

    assert validate_gold_chunk_map(checked_in, corpus_dir=CORPUS, max_chars=500) == []
    assert build_gold_chunk_map(DATASET, corpus_dir=CORPUS, max_chars=500) == checked_in


def test_cross_chunk_evidence_accepts_every_overlapping_chunk(tmp_path: Path) -> None:
    from evals.runner.gold_chunk_map import build_gold_chunk_map

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = "# Demo\n\n## Section\n\nalpha beta gamma delta epsilon zeta"
    (corpus / "demo.md").write_text(document, encoding="utf-8")
    from evals.runner.hashing import canonical_text_sha256

    digest = canonical_text_sha256(corpus / "demo.md")
    (corpus / "manifest.json").write_text(
        json.dumps({
            "corpus_version": "demo-v1",
            "documents": [{
                "document_id": "doc-1",
                "filename": "demo.md",
                "title": "Demo",
                "source_type": "markdown",
                "version": 1,
                "sha256": digest,
            }],
        }),
        encoding="utf-8",
    )
    case = {
        "case_id": "case-1",
        "dataset_version": "demo-v1",
        "split": "development",
        "category": "single_source",
        "difficulty": "easy",
        "question": "q",
        "conversation_history": [],
        "gold_answer_points": ["a"],
        "gold_document_ids": ["doc-1"],
        "gold_evidence_spans": [{
            "evidence_id": "ev-1",
            "document_id": "doc-1",
            "section": "Section",
            "text": "gamma delta epsilon",
        }],
        "acceptable_alternative_document_ids": [],
        "is_answerable": True,
        "expected_behavior": "answer_with_citation",
        "format_contract": {
            "type": "strict_json",
            "required_sections": [],
            "required_json_schema": None,
            "max_bullets": None,
            "require_citations": True,
            "forbidden_fields": [],
        },
        "tags": [],
    }
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")

    mapping = build_gold_chunk_map(dataset, corpus_dir=corpus, max_chars=18)

    group = mapping.cases["case-1"].evidence_groups[0]
    assert len(group.acceptable_chunk_ids) == 2


def test_gold_map_fails_when_evidence_cannot_be_located(tmp_path: Path) -> None:
    from evals.runner.gold_chunk_map import GoldChunkMappingError, build_gold_chunk_map

    payload = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload["gold_evidence_spans"][0]["text"] = "not in corpus"
    dataset = tmp_path / "broken.jsonl"
    dataset.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(GoldChunkMappingError, match="not found"):
        build_gold_chunk_map(dataset, corpus_dir=CORPUS)


def test_chunk_map_hash_validation_detects_corpus_or_chunking_changes() -> None:
    from evals.runner.gold_chunk_map import build_gold_chunk_map, validate_gold_chunk_map

    mapping = build_gold_chunk_map(DATASET, corpus_dir=CORPUS)

    assert validate_gold_chunk_map(mapping, corpus_dir=CORPUS, max_chars=500) == []
    errors = validate_gold_chunk_map(mapping, corpus_dir=CORPUS, max_chars=499)
    assert errors == ["chunking_config_hash mismatch"]
