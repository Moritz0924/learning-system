from __future__ import annotations

import json

from evals.graders.format_grader import grade_format
from evals.models import FormatContract


def _contract(**updates) -> FormatContract:
    payload = {
        "type": "strict_json",
        "required_sections": [],
        "required_json_schema": None,
        "max_bullets": None,
        "require_citations": True,
        "forbidden_fields": ["system_prompt", "api_key", "runtime_metadata"],
    }
    payload.update(updates)
    return FormatContract.model_validate(payload)


def test_valid_strict_json_envelope_passes() -> None:
    raw = json.dumps({"answer": "Explanation", "citations": [{"chunk_id": "c1", "document_id": "d1"}]})
    grade = grade_format(raw, contract=_contract(), is_answerable=True)

    assert grade.format_followed is True
    assert grade.parsed_answer == "Explanation"
    assert grade.parsed_citations == [{"chunk_id": "c1", "document_id": "d1"}]


def test_non_json_markdown_and_missing_fields_fail_cleanly() -> None:
    markdown = grade_format("```json\n{}\n```", contract=_contract(), is_answerable=True)
    missing = grade_format('{"answer":"x"}', contract=_contract(), is_answerable=True)

    assert markdown.json_parse_success is False
    assert markdown.format_followed is False
    assert missing.citation_format_valid is False
    assert "citations must be an array" in missing.errors


def test_required_sections_bullet_limit_and_forbidden_fields_are_checked() -> None:
    raw = json.dumps({
        "answer": "- one\n- two\napi_key: leaked",
        "citations": [],
        "runtime_metadata": {},
    })
    grade = grade_format(
        raw,
        contract=_contract(required_sections=["结论"], max_bullets=1),
        is_answerable=True,
    )

    assert grade.required_sections_present is False
    assert grade.forbidden_field_detected is True
    assert grade.format_followed is False


def test_unanswerable_format_requires_clear_refusal_and_no_citations() -> None:
    correct = grade_format(
        json.dumps({"answer": "当前资料不足，无法回答。", "citations": []}, ensure_ascii=False),
        contract=_contract(require_citations=False),
        is_answerable=False,
    )
    wrong = grade_format(
        json.dumps({"answer": "答案是 2028 年发布。", "citations": [{"chunk_id": "c", "document_id": "d"}]}, ensure_ascii=False),
        contract=_contract(require_citations=False),
        is_answerable=False,
    )

    assert correct.abstention_format_correct is True
    assert wrong.abstention_format_correct is False


def test_required_json_schema_is_enforced() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "minLength": 3}},
        "required": ["answer"],
    }
    grade = grade_format(
        json.dumps({"answer": "x", "citations": []}),
        contract=_contract(required_json_schema=schema, require_citations=False),
        is_answerable=True,
    )

    assert grade.format_followed is False
    assert any("required_json_schema" in error for error in grade.errors)


def test_chinese_insufficient_evidence_response_is_a_valid_abstention() -> None:
    grade = grade_format(
        '{"answer":"资料不足，无法根据当前语料回答。","citations":[]}',
        contract=FormatContract(type="insufficient_evidence"),
        is_answerable=False,
    )

    assert grade.abstention_format_correct is True
    assert grade.format_followed is True


def test_secret_assignments_in_answer_text_are_rejected() -> None:
    grade = grade_format(
        json.dumps({
            "answer": "api_key=secret database_url=postgresql://private system_prompt=hidden",
            "citations": [],
        }),
        contract=_contract(require_citations=False),
        is_answerable=True,
    )

    assert grade.forbidden_field_detected is True
    assert grade.format_followed is False
