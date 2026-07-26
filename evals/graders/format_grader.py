"""Automatic structural grading for the fixed JSON response envelope."""
from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from evals.models import FormatContract, FormatGrade


_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_REFUSAL_MARKERS = ("资料不足", "证据不足", "信息不足", "无法回答", "无法根据", "insufficient")
_DEFAULT_FORBIDDEN = {"system_prompt", "api_key", "runtime_metadata", "provider_headers", "database_url"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_ -]?key|database[_ -]?(?:url|connection)|system[_ -]?prompt|provider[_ -]?headers?)\s*[:=]\s*\S+"
)
_IMMUTABLE_PROMPT_FRAGMENT = "You are an adaptive AI application development tutor"


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def grade_format(
    raw_output: str,
    *,
    contract: FormatContract,
    is_answerable: bool,
) -> FormatGrade:
    errors: list[str] = []
    parsed: Any = None
    if raw_output.lstrip().startswith("```"):
        errors.append("markdown code fences are forbidden")
    try:
        parsed = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        errors.append("output is not valid JSON")
        return FormatGrade(
            format_followed=False,
            json_parse_success=False,
            required_sections_present=not contract.required_sections,
            citation_format_valid=False,
            abstention_format_correct=None if is_answerable else False,
            forbidden_field_detected=False,
            errors=errors,
        )
    if not isinstance(parsed, dict):
        errors.append("top-level JSON value must be an object")
        parsed = {}

    top_keys = set(parsed)
    extra_top_keys = top_keys - {"answer", "citations"}
    forbidden = set(contract.forbidden_fields) | _DEFAULT_FORBIDDEN
    answer_text = parsed.get("answer") if isinstance(parsed.get("answer"), str) else ""
    text_leak_detected = bool(
        _SECRET_ASSIGNMENT.search(answer_text)
        or _IMMUTABLE_PROMPT_FRAGMENT in answer_text
    )
    forbidden_detected = bool(extra_top_keys or (_all_keys(parsed) & forbidden) or text_leak_detected)
    if extra_top_keys:
        errors.append(f"unexpected top-level fields: {sorted(extra_top_keys)}")
    if _all_keys(parsed) & forbidden:
        errors.append("forbidden internal field detected")
    if text_leak_detected:
        errors.append("forbidden secret or system prompt content detected")

    answer = parsed.get("answer")
    if not isinstance(answer, str):
        errors.append("answer must be a string")
        answer = None
    citations = parsed.get("citations")
    citation_format_valid = isinstance(citations, list)
    parsed_citations: list[dict[str, str]] = []
    if not citation_format_valid:
        errors.append("citations must be an array")
        citations = []
    else:
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict) or set(citation) != {"chunk_id", "document_id"}:
                citation_format_valid = False
                errors.append(f"citation {index} must contain only chunk_id and document_id")
                continue
            if not all(isinstance(citation[key], str) and citation[key] for key in ("chunk_id", "document_id")):
                citation_format_valid = False
                errors.append(f"citation {index} fields must be non-empty strings")
                continue
            parsed_citations.append({"chunk_id": citation["chunk_id"], "document_id": citation["document_id"]})
    if contract.require_citations and not parsed_citations:
        citation_format_valid = False
        errors.append("required citation is missing")

    required_sections_present = bool(
        answer is not None and all(section in answer for section in contract.required_sections)
    ) if contract.required_sections else True
    if not required_sections_present:
        errors.append("required section is missing")
    if answer is not None and contract.max_bullets is not None:
        if len(_BULLET.findall(answer)) > contract.max_bullets:
            errors.append("maximum bullet count exceeded")

    abstention_correct: bool | None = None
    if not is_answerable:
        normalized = (answer or "").lower()
        abstention_correct = any(marker.lower() in normalized for marker in _REFUSAL_MARKERS) and not parsed_citations
        if not abstention_correct:
            errors.append("unanswerable response does not follow the abstention format")

    if contract.required_json_schema is not None:
        schema_errors = list(Draft202012Validator(contract.required_json_schema).iter_errors(parsed))
        if schema_errors:
            errors.append("required_json_schema validation failed: " + schema_errors[0].message)

    return FormatGrade(
        format_followed=not errors,
        json_parse_success=True,
        required_sections_present=required_sections_present,
        citation_format_valid=citation_format_valid,
        abstention_format_correct=abstention_correct,
        forbidden_field_detected=forbidden_detected,
        errors=errors,
        parsed_answer=answer,
        parsed_citations=parsed_citations,
    )
