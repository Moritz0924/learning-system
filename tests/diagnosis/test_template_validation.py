from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.domain.diagnosis.contracts import (
    DiagnosticKnowledgeAnswer,
    DiagnosticTemplate,
    SelfAssessmentAnswer,
    public_template,
)
from backend.app.domain.diagnosis.validation import DiagnosisValidationError, validate_diagnostic_answers
from backend.app.infrastructure.diagnosis.template_repository import DiagnosticTemplateRepository


def _template_data() -> dict:
    return {
        "template_version": "test_v1",
        "domain": "test",
        "title": "Test diagnostic",
        "self_assessment_dimensions": [
            {
                "code": "python",
                "title": "Python",
                "description": "Python confidence",
                "related_node_codes": ["python_foundations"],
            }
        ],
        "questions": [
            {
                "question_id": "python-1",
                "node_code": "python_foundations",
                "question_type": "single_choice",
                "prompt": "Which value is immutable?",
                "options": [
                    {"option_id": "a", "label": "list"},
                    {"option_id": "b", "label": "tuple"},
                ],
                "correct_option_id": "b",
                "weight": 1,
                "difficulty": 1,
            }
        ],
    }


def test_template_contract_rejects_unknown_fields() -> None:
    data = _template_data()
    data["internal_note"] = "must not be accepted"

    with pytest.raises(ValidationError):
        DiagnosticTemplate.model_validate(data)


@pytest.mark.parametrize(
    ("mutate", "expected_fragment"),
    [
        (
            lambda data: data["self_assessment_dimensions"].append(
                dict(data["self_assessment_dimensions"][0])
            ),
            "duplicate self-assessment dimension",
        ),
        (
            lambda data: data["questions"].append(dict(data["questions"][0])),
            "duplicate diagnostic question",
        ),
        (
            lambda data: data["questions"][0]["options"].append(
                dict(data["questions"][0]["options"][0])
            ),
            "duplicate option",
        ),
        (
            lambda data: data["questions"][0].update(correct_option_id="missing"),
            "correct option",
        ),
    ],
)
def test_template_contract_rejects_ambiguous_scoring_definitions(mutate, expected_fragment: str) -> None:
    data = _template_data()
    mutate(data)

    with pytest.raises(ValidationError, match=expected_fragment):
        DiagnosticTemplate.model_validate(data)


def test_public_template_does_not_leak_answers_or_scoring_internals(diagnostic_template) -> None:
    payload = public_template(diagnostic_template).model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True)

    assert "correct_option_id" not in encoded
    assert "weight" not in encoded
    assert "difficulty" not in encoded
    assert "related_node_codes" not in encoded
    assert payload["questions"][0]["node_code"] == "python_foundations"
    assert payload["questions"][0]["options"][1]["label"] == "tuple"


def test_answer_validation_accepts_missing_optional_self_assessment(diagnostic_template) -> None:
    validated = validate_diagnostic_answers(
        template=diagnostic_template,
        template_version="test_v1",
        self_answers=[],
        knowledge_answers=[
            DiagnosticKnowledgeAnswer(question_id="python-1", selected_option_id="b"),
            DiagnosticKnowledgeAnswer(question_id="python-2", selected_option_id="a"),
            DiagnosticKnowledgeAnswer(question_id="api-1", selected_option_id="a"),
        ],
    )

    assert validated.self_answers_by_dimension == {}
    assert validated.knowledge_answers_by_question["api-1"] == "a"


@pytest.mark.parametrize(
    ("template_version", "self_answers", "knowledge_answers", "error_code"),
    [
        (
            "wrong_version",
            [],
            [("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "template_version_mismatch",
        ),
        (
            "test_v1",
            [("unknown", 2)],
            [("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "unknown_self_assessment_dimension",
        ),
        (
            "test_v1",
            [("python", 2), ("python", 3)],
            [("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "duplicate_self_assessment_answer",
        ),
        (
            "test_v1",
            [("python", 5)],
            [("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "self_assessment_out_of_range",
        ),
        (
            "test_v1",
            [],
            [("unknown", "a"), ("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "unknown_diagnostic_question",
        ),
        (
            "test_v1",
            [],
            [("python-1", "b"), ("python-1", "b"), ("python-2", "a"), ("api-1", "a")],
            "duplicate_knowledge_answer",
        ),
        (
            "test_v1",
            [],
            [("python-1", "missing"), ("python-2", "a"), ("api-1", "a")],
            "invalid_diagnostic_option",
        ),
        (
            "test_v1",
            [],
            [("python-1", "b"), ("python-2", "a")],
            "missing_required_knowledge_answer",
        ),
    ],
)
def test_answer_validation_rejects_invalid_or_ambiguous_input(
    diagnostic_template,
    template_version: str,
    self_answers: list[tuple[str, int]],
    knowledge_answers: list[tuple[str, str]],
    error_code: str,
) -> None:
    with pytest.raises(DiagnosisValidationError) as exc_info:
        validate_diagnostic_answers(
            template=diagnostic_template,
            template_version=template_version,
            self_answers=[
                SelfAssessmentAnswer(dimension_code=dimension_code, level=value)
                for dimension_code, value in self_answers
            ],
            knowledge_answers=[
                DiagnosticKnowledgeAnswer(question_id=question_id, selected_option_id=option_id)
                for question_id, option_id in knowledge_answers
            ],
        )

    assert exc_info.value.code == error_code


def test_repository_loads_validates_and_caches_bundled_template() -> None:
    repository = DiagnosticTemplateRepository()

    first = repository.load(domain="ai_app_dev", template_version="ai_app_dev_v1")
    second = repository.load(domain="ai_app_dev", template_version="ai_app_dev_v1")

    assert first is second
    assert len(first.sha256) == 64
    assert first.sha256 == second.sha256
    assert first.template.domain == "ai_app_dev"
    assert first.template.questions


def test_repository_hash_is_canonical_and_rejects_version_mismatch(tmp_path: Path) -> None:
    data = _template_data()
    compact = tmp_path / "test_v1.json"
    compact.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    compact_hash = DiagnosticTemplateRepository(tmp_path).load(
        domain="test", template_version="test_v1"
    ).sha256

    pretty_dir = tmp_path / "pretty"
    pretty_dir.mkdir()
    (pretty_dir / "test_v1.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pretty_hash = DiagnosticTemplateRepository(pretty_dir).load(
        domain="test", template_version="test_v1"
    ).sha256

    assert compact_hash == pretty_hash

    data["template_version"] = "other_v1"
    compact.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DiagnosisValidationError) as exc_info:
        DiagnosticTemplateRepository(tmp_path).load(domain="test", template_version="test_v1")
    assert exc_info.value.code == "template_version_mismatch"
