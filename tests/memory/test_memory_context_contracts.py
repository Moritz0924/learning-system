from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adaptive_tutor.phase2 import schemas


def _memory_context(**overrides: object):
    assert hasattr(schemas, "TutorMemoryContext")
    data: dict[str, object] = {
        "memory_id": "memory-1",
        "memory_type": "learning_preference",
        "scope": "user",
        "content": {
            "preference_key": "explanation_style",
            "preference_value": "examples_first",
        },
        "importance": 0.8,
        "confidence": 0.9,
        "source_kind": "explicit_user",
        "expires_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return schemas.TutorMemoryContext(**data)


def _goal_context():
    return schemas.TutorGoalContext(
        goal_id="goal-1",
        title="Learn Python",
        target_outcome="Build a reliable application",
        domain="software_engineering",
        deadline=None,
        weekly_hours_target=5,
    )


def test_tutor_memory_context_is_frozen_strict_and_exposes_only_prompt_safe_fields():
    memory = _memory_context()

    assert set(memory.model_dump()) == {
        "memory_id",
        "memory_type",
        "scope",
        "content",
        "importance",
        "confidence",
        "source_kind",
        "expires_at",
    }
    with pytest.raises(ValidationError):
        _memory_context(idempotency_key="private-key")
    with pytest.raises(ValidationError):
        memory.importance = 0.1


def test_memory_context_selection_is_frozen_strict_and_defaults_to_empty_selection():
    assert hasattr(schemas, "MemoryContextSelection")
    selection = schemas.MemoryContextSelection()

    assert selection.items == []
    assert selection.selected_memory_ids == []
    assert selection.skipped_by_budget == 0
    assert selection.policy_version == "memory-context-v1"
    assert selection.serialized_char_count == 2
    with pytest.raises(ValidationError):
        schemas.MemoryContextSelection(secret="not allowed")
    with pytest.raises(ValidationError):
        selection.skipped_by_budget = 1


def test_tutor_context_defaults_long_term_memories_to_empty_for_compatibility():
    context = schemas.TutorContext(learning_goal=_goal_context())

    assert context.long_term_memories == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_type", "unknown"),
        ("scope", "another-goal"),
        ("source_kind", "provider_raw"),
        ("importance", 1.1),
        ("confidence", -0.1),
    ],
)
def test_tutor_memory_context_rejects_values_outside_public_contract(field: str, value: object):
    with pytest.raises(ValidationError):
        _memory_context(**{field: value})
