"""Load and validate versioned evaluation datasets against the fixed corpus."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from jsonschema import Draft202012Validator

from evals.models import CorpusManifest, LearningQaEvaluationCase


@dataclass(frozen=True)
class DatasetValidationResult:
    cases: list[LearningQaEvaluationCase]
    errors: list[str]


def _read_manifest(corpus_dir: Path) -> CorpusManifest:
    return CorpusManifest.model_validate_json((corpus_dir / "manifest.json").read_text(encoding="utf-8"))


def load_and_validate_dataset(dataset_path: Path, *, corpus_dir: Path) -> DatasetValidationResult:
    errors: list[str] = []
    cases: list[LearningQaEvaluationCase] = []
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "dataset.schema.json"
    schema_validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    for line_number, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            schema_errors = list(schema_validator.iter_errors(payload))
            if schema_errors:
                errors.append(f"line {line_number}: JSON Schema: {schema_errors[0].message}")
                continue
            cases.append(LearningQaEvaluationCase.model_validate(payload))
        except (ValidationError, json.JSONDecodeError) as exc:
            errors.append(f"line {line_number}: {exc}")

    manifest = _read_manifest(corpus_dir)
    documents = {item.document_id: item for item in manifest.documents}
    corpus_text = {
        item.document_id: (corpus_dir / item.filename).read_text(encoding="utf-8")
        for item in manifest.documents
    }

    if not 30 <= len(cases) <= 50:
        errors.append(f"case count must be between 30 and 50, got {len(cases)}")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        errors.append("case_id values must be unique")
    splits = {case.split for case in cases}
    if splits != {"development", "test"}:
        errors.append("development and test splits must both be non-empty")
    if cases:
        total = len(cases)
        if sum(case.is_answerable for case in cases) / total < 0.75:
            errors.append("answerable cases must be at least 75%")
        if sum(not case.is_answerable for case in cases) / total < 0.10:
            errors.append("unanswerable cases must be at least 10%")
        if sum(case.category == "multi_evidence" for case in cases) / total < 0.15:
            errors.append("multi_evidence cases must be at least 15%")
        if sum(case.category == "multi_turn" for case in cases) < 3:
            errors.append("at least 3 multi_turn cases are required")
        if sum(case.category == "prompt_injection" for case in cases) < 3:
            errors.append("at least 3 prompt_injection cases are required")
        versions = {case.dataset_version for case in cases}
        if len(versions) != 1:
            errors.append("all cases must use one dataset_version")

    for case in cases:
        if case.is_answerable:
            if not case.gold_answer_points or not case.gold_document_ids or not case.gold_evidence_spans:
                errors.append(f"{case.case_id}: answerable case requires gold answer, documents, and evidence")
            if case.expected_behavior != "answer_with_citation":
                errors.append(f"{case.case_id}: answerable case must expect answer_with_citation")
        else:
            if case.gold_answer_points or case.gold_document_ids or case.gold_evidence_spans:
                errors.append(f"{case.case_id}: unanswerable case must not contain gold answers or evidence")
            if case.expected_behavior != "abstain":
                errors.append(f"{case.case_id}: unanswerable case must expect abstain")

        for document_id in [*case.gold_document_ids, *case.acceptable_alternative_document_ids]:
            if document_id not in documents:
                errors.append(f"{case.case_id}: unknown document_id {document_id}")
        for evidence in case.gold_evidence_spans:
            text = corpus_text.get(evidence.document_id)
            if text is None:
                errors.append(f"{case.case_id}/{evidence.evidence_id}: unknown evidence document")
            elif evidence.text not in text:
                errors.append(f"{case.case_id}/{evidence.evidence_id}: evidence text not found in full corpus")

    dataset_manifest = dataset_path.with_name(dataset_path.stem + "_manifest.json")
    if dataset_manifest.exists():
        payload = json.loads(dataset_manifest.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        if payload.get("sha256") != actual_hash:
            errors.append("dataset manifest sha256 mismatch")
        if payload.get("case_count") != len(cases):
            errors.append("dataset manifest case_count mismatch")
        if payload.get("development_count") != sum(case.split == "development" for case in cases):
            errors.append("dataset manifest development_count mismatch")
        if payload.get("test_count") != sum(case.split == "test" for case in cases):
            errors.append("dataset manifest test_count mismatch")

    return DatasetValidationResult(cases=cases, errors=errors)
