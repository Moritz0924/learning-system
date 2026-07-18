from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from collections import UserDict
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.domain.memory.contracts import CreateMemoryCommand
from backend.app.domain.memory.errors import (
    InvalidMemoryContent,
    InvalidMemoryScope,
    UnsupportedMemoryType,
)
from backend.app.domain.memory.validation import validate_memory_command
from tests.memory.helpers import FIXED_NOW, mastery_command, milestone_command, preference_command


class _CustomMapping(dict[str, Any]):
    pass


def _validate(command: CreateMemoryCommand):
    return validate_memory_command(command, now=FIXED_NOW)


@pytest.mark.parametrize(
    "memory_type",
    ["learning_preference", "long_term_goal", "mastery_summary", "learning_milestone"],
)
def test_command_accepts_all_legal_memory_types(memory_type: str) -> None:
    command = preference_command(memory_type=memory_type)

    assert command.memory_type == memory_type


def test_command_rejects_illegal_memory_type() -> None:
    with pytest.raises(ValidationError):
        preference_command(memory_type="conversation_summary")


@pytest.mark.parametrize(
    "command",
    [
        preference_command(),
        preference_command(
            memory_type="long_term_goal",
            goal_id="test-goal",
            content={"title": "Ship tutor", "target_outcome": "A reliable tutor", "deadline": "2026-12-01"},
        ),
        mastery_command(),
        milestone_command(),
    ],
)
def test_validation_accepts_each_memory_type(command: CreateMemoryCommand) -> None:
    assert _validate(command).command.content


@pytest.mark.parametrize("preference_value", [True, 7, 1.5, "x" * 21])
def test_validation_accepts_boolean_numeric_and_long_string_preference_values(
    preference_value: bool | int | float | str,
) -> None:
    command = preference_command(
        content={"preference_key": "session_style", "preference_value": preference_value}
    )

    assert _validate(command).command.content["preference_value"] == preference_value


@pytest.mark.parametrize(
    "memory_type,content",
    [
        ("learning_preference", {"preference_key": "key"}),
        ("long_term_goal", {"title": "Goal", "target_outcome": "Outcome", "unknown": True}),
        ("mastery_summary", {"knowledge_node_id": "node"}),
        ("learning_milestone", {"milestone_code": "done", "title": "Done", "achieved_at": FIXED_NOW}),
    ],
)
def test_validation_rejects_missing_or_unknown_content_fields(memory_type: str, content: dict[str, Any]) -> None:
    if memory_type == "mastery_summary":
        command = mastery_command(content=content)
    elif memory_type == "learning_milestone":
        command = milestone_command(content=content)
    else:
        command = preference_command(memory_type=memory_type, content=content)

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    "command,should_pass",
    [
        (preference_command(goal_id="test-goal"), False),
        (preference_command(memory_type="long_term_goal", content={"title": "Goal", "target_outcome": "Outcome"}, goal_id=None), True),
        (preference_command(memory_type="long_term_goal", content={"title": "Goal", "target_outcome": "Outcome"}, goal_id="test-goal"), True),
        (mastery_command(goal_id=None), False),
        (milestone_command(goal_id=None), False),
    ],
)
def test_validation_enforces_memory_scope_rules(command: CreateMemoryCommand, should_pass: bool) -> None:
    if should_pass:
        assert _validate(command)
    else:
        with pytest.raises(InvalidMemoryScope):
            _validate(command)


@pytest.mark.parametrize(
    "content,expires_at",
    [
        ({"score": -0.01}, FIXED_NOW + timedelta(days=1)),
        ({"score": 100.01}, FIXED_NOW + timedelta(days=1)),
        ({"confidence": -0.01}, FIXED_NOW + timedelta(days=1)),
        ({"confidence": 1.01}, FIXED_NOW + timedelta(days=1)),
        ({"score": float("nan")}, FIXED_NOW + timedelta(days=1)),
        ({"confidence": float("inf")}, FIXED_NOW + timedelta(days=1)),
        ({}, None),
        ({}, FIXED_NOW + timedelta(days=30, seconds=1)),
    ],
)
def test_mastery_validation_enforces_bounds_and_expiry(content: dict[str, Any], expires_at: datetime | None) -> None:
    command = mastery_command(content={**mastery_command().content, **content}, expires_at=expires_at)

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("score", True),
        ("score", "78.5"),
        ("confidence", False),
        ("confidence", "0.8"),
        ("evidence_count", True),
        ("evidence_count", 1.0),
        ("evidence_count", "1"),
    ],
)
def test_mastery_validation_rejects_coercible_scalar_representations(
    field: str,
    value: object,
) -> None:
    command = mastery_command(content={**mastery_command().content, field: value})

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    ("score", "confidence"),
    [(78, 1), (78.5, 0.8)],
)
def test_mastery_validation_accepts_real_numeric_scalars(
    score: int | float,
    confidence: int | float,
) -> None:
    command = mastery_command(
        content={
            **mastery_command().content,
            "score": score,
            "confidence": confidence,
        }
    )

    validated = _validate(command)

    assert validated.command.content["score"] == score
    assert validated.command.content["confidence"] == confidence


@pytest.mark.parametrize(
    "deadline",
    [
        0,
        0.0,
        False,
        "0",
        "20261201",
        datetime(2026, 12, 1, tzinfo=timezone.utc),
        "2026-13-40",
        "not-a-date",
    ],
)
def test_long_term_goal_rejects_non_date_deadlines(deadline: object) -> None:
    command = preference_command(
        memory_type="long_term_goal",
        goal_id="test-goal",
        content={"title": "Goal", "target_outcome": "Outcome", "deadline": deadline},
    )

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize("deadline", [None, date(2026, 12, 1), "2026-12-01"])
def test_long_term_goal_accepts_null_python_date_and_iso_date(deadline: date | str | None) -> None:
    command = preference_command(
        memory_type="long_term_goal",
        goal_id="test-goal",
        content={"title": "Goal", "target_outcome": "Outcome", "deadline": deadline},
    )

    assert _validate(command).command.content["deadline"] == (
        None if deadline is None else "2026-12-01"
    )


def test_long_term_goal_strips_surrounding_whitespace_from_iso_deadline() -> None:
    command = preference_command(
        memory_type="long_term_goal",
        goal_id="test-goal",
        content={
            "title": "Goal",
            "target_outcome": "Outcome",
            "deadline": " 2027-06-30 ",
        },
    )

    assert _validate(command).command.content["deadline"] == "2027-06-30"


@pytest.mark.parametrize(
    "achieved_at",
    [0, 0.0, False, "0", "2026-07-18", "not-a-datetime"],
)
def test_milestone_rejects_non_datetime_achieved_at(achieved_at: object) -> None:
    command = milestone_command(
        content={**milestone_command().content, "achieved_at": achieved_at}
    )

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    "achieved_at",
    [
        datetime(2026, 7, 18, 20, tzinfo=timezone(timedelta(hours=8))),
        "2026-07-18T20:00:00+08:00",
    ],
)
def test_milestone_accepts_datetime_values_and_normalizes_them_to_utc(
    achieved_at: datetime | str,
) -> None:
    command = milestone_command(
        content={**milestone_command().content, "achieved_at": achieved_at}
    )

    assert _validate(command).command.content["achieved_at"] == "2026-07-18T12:00:00Z"


def test_milestone_strips_surrounding_whitespace_from_iso_achieved_at() -> None:
    command = milestone_command(
        content={
            **milestone_command().content,
            "achieved_at": " 2026-07-18T12:00:00Z ",
        }
    )

    assert _validate(command).command.content["achieved_at"] == "2026-07-18T12:00:00Z"


def test_milestone_rejects_expiry() -> None:
    with pytest.raises(InvalidMemoryContent):
        _validate(milestone_command(expires_at=FIXED_NOW + timedelta(days=1)))


@pytest.mark.parametrize("evidence_ref", ["", "   "])
def test_milestone_rejects_blank_evidence_references(evidence_ref: str) -> None:
    command = milestone_command(content={**milestone_command().content, "evidence_refs": [evidence_ref]})

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    "bad_value",
    [BytesIO(b"not-json"), b"not-json", _CustomMapping(value="custom"), float("nan"), float("inf")],
)
def test_validation_rejects_non_json_and_non_finite_values(bad_value: object) -> None:
    with pytest.raises(InvalidMemoryContent):
        _validate(preference_command(content={"preference_key": "key", "preference_value": bad_value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", _CustomMapping(preference_key="key", preference_value="value")),
        ("source_metadata", _CustomMapping(value="custom")),
        ("content", UserDict(preference_key="key", preference_value="value")),
        ("source_metadata", UserDict(value="custom")),
    ],
)
def test_command_rejects_custom_top_level_mappings(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        preference_command(**{field: value})


def test_validation_rejects_content_larger_than_8192_utf8_bytes() -> None:
    command = preference_command(content={"preference_key": "key", "preference_value": "界" * 3000})

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize(
    "tree",
    [
        {"prompt": "secret"},
        {"nested": {" RAW_CHAT ": "secret"}},
        {"items": [{"Api_Key": "secret"}]},
    ],
)
def test_validation_rejects_forbidden_keys_recursively(tree: dict[str, Any]) -> None:
    command = preference_command(source_metadata=tree)

    with pytest.raises(InvalidMemoryContent):
        _validate(command)


@pytest.mark.parametrize("key", ["", "key with spaces", "key?", "é", "x" * 161])
def test_command_rejects_illegal_idempotency_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        preference_command(idempotency_key=key)


@pytest.mark.parametrize(
    "source_kind,source_ref_id,should_pass",
    [
        ("explicit_user", None, True),
        ("explicit_user", "  ", False),
        ("assessment", None, False),
        ("learning_event", "  ", False),
        ("mastery_record", "record-1", True),
        ("system_derived", "derived-1", True),
    ],
)
def test_validation_enforces_source_reference_rules(
    source_kind: str, source_ref_id: str | None, should_pass: bool
) -> None:
    command = preference_command(source_kind=source_kind, source_ref_id=source_ref_id)

    if should_pass:
        assert _validate(command)
    else:
        with pytest.raises(InvalidMemoryContent):
            _validate(command)


def test_validation_normalizes_utc_and_computes_stable_canonical_hash() -> None:
    command = preference_command(
        content={"preference_value": [" one ", "two"], "preference_key": " style "},
        expires_at=datetime(2026, 7, 19, 4, 0, tzinfo=timezone(timedelta(hours=8))),
        source_metadata={"when": datetime(2026, 7, 18, 20, tzinfo=timezone(timedelta(hours=8)))},
    )

    validated = _validate(command)
    expected_content = {"preference_key": "style", "preference_value": ["one", "two"]}
    canonical = json.dumps(expected_content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    assert validated.command.content == expected_content
    assert validated.command.expires_at == datetime(2026, 7, 18, 20, tzinfo=timezone.utc)
    assert validated.command.source_metadata == {"when": "2026-07-18T12:00:00+00:00"}
    assert validated.content_hash == sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "metadata",
    [
        {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}},
        {"items": list(range(101))},
        {str(index): list(range(10)) for index in range(100)},
    ],
)
def test_validation_enforces_tree_depth_container_and_node_caps(metadata: dict[str, Any]) -> None:
    with pytest.raises(InvalidMemoryContent):
        _validate(preference_command(source_metadata=metadata))
