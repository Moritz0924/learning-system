from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"


def test_dataset_models_forbid_unknown_fields() -> None:
    from evals.models import LearningQaEvaluationCase

    payload = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        LearningQaEvaluationCase.model_validate(payload)


def test_learning_qa_v1_has_frozen_distribution_and_valid_evidence() -> None:
    from evals.runner.dataset_loader import load_and_validate_dataset

    result = load_and_validate_dataset(DATASET, corpus_dir=CORPUS)

    assert len(result.cases) == 40
    assert Counter(case.split for case in result.cases) == {"development": 24, "test": 16}
    assert Counter(case.category for case in result.cases) == {
        "single_source": 12,
        "paraphrase": 8,
        "multi_evidence": 8,
        "unanswerable": 5,
        "prompt_injection": 4,
        "multi_turn": 3,
    }
    assert Counter(case.is_answerable for case in result.cases) == {True: 35, False: 5}
    assert result.errors == []


def test_validator_reports_evidence_text_missing_from_full_corpus(tmp_path: Path) -> None:
    from evals.runner.dataset_loader import load_and_validate_dataset

    payload = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload["gold_evidence_spans"][0]["text"] = "不存在于语料的证据"
    dataset = tmp_path / "broken.jsonl"
    dataset.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    result = load_and_validate_dataset(dataset, corpus_dir=CORPUS)

    assert any("evidence text not found" in error for error in result.errors)


def test_versioned_evaluation_text_files_are_utf8_without_bom() -> None:
    paths = [
        *CORPUS.glob("*.md"),
        CORPUS / "manifest.json",
        DATASET,
        ROOT / "evals" / "datasets" / "learning_qa_v1_manifest.json",
        *list((ROOT / "evals" / "prompts").glob("*.txt")),
    ]

    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        raw.decode("utf-8")


def test_committed_json_schemas_match_pydantic_models() -> None:
    from evals.models import EvaluationCaseResult, LearningQaEvaluationCase

    dataset_schema = json.loads((ROOT / "evals" / "schemas" / "dataset.schema.json").read_text(encoding="utf-8"))
    result_schema = json.loads((ROOT / "evals" / "schemas" / "result.schema.json").read_text(encoding="utf-8"))

    assert dataset_schema == LearningQaEvaluationCase.model_json_schema()
    assert result_schema == EvaluationCaseResult.model_json_schema()
