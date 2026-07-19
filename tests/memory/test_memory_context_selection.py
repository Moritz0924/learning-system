from __future__ import annotations

import json
import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorMemoryContext
from backend.app.domain.memory import MemoryRecord
from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from tests.memory.helpers import add_memory_scope, mastery_command, preference_command


NOW = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
MODULE_NAME = "backend.app.application.memory_context_service"
memory_context_service = (
    importlib.import_module(MODULE_NAME) if importlib.util.find_spec(MODULE_NAME) is not None else None
)


class RecordingMemoryRepository:
    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []

    def list_active(self, **kwargs: Any) -> list[MemoryRecord]:
        self.calls.append(kwargs)
        return list(self.records)


def _content(memory_type: str, index: int = 0) -> dict[str, Any]:
    if memory_type == "learning_preference":
        return {"preference_key": f"style-{index}", "preference_value": f"value-{index}"}
    if memory_type == "long_term_goal":
        return {"title": f"Goal {index}", "target_outcome": f"Outcome {index}"}
    if memory_type == "mastery_summary":
        return {
            "knowledge_node_id": f"node-{index}",
            "score": float(20 + index),
            "confidence": 0.8,
            "evidence_count": 2,
            "calculation_version": "mastery-v1",
        }
    return {
        "milestone_code": f"milestone-{index}",
        "title": f"Milestone {index}",
        "achieved_at": (NOW + timedelta(minutes=index)).isoformat(),
        "evidence_refs": [],
    }


def _record(
    memory_id: str,
    memory_type: str,
    *,
    user_id: str = "user-1",
    goal_id: str | None = None,
    content: dict[str, Any] | None = None,
    importance: float = 0.5,
    confidence: float = 0.8,
    created_at: datetime = NOW,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        user_id=user_id,
        goal_id=goal_id,
        memory_type=memory_type,
        schema_version="memory-v1",
        content=content or _content(memory_type),
        content_hash="a" * 64,
        source_kind="system_derived",
        source_ref_id=None,
        source_metadata={"private": "must-not-leak"},
        importance=importance,
        confidence=confidence,
        is_enabled=True,
        expires_at=None,
        disabled_at=None,
        disabled_reason=None,
        idempotency_key=f"key:{memory_id}",
        created_at=created_at,
        updated_at=created_at,
    )


def _service(records: list[MemoryRecord]):
    repository = RecordingMemoryRepository(records)
    assert memory_context_service is not None
    assert hasattr(memory_context_service, "MemoryContextService")
    return memory_context_service.MemoryContextService(repository, clock=lambda: NOW), repository


def _canonical_items(items: list[TutorMemoryContext]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_build_scans_at_most_100_active_records_for_exact_user_and_goal_scope():
    service, repository = _service([])

    selection = service.build(user_id="user-1", goal_id="goal-1", current_task=None)

    assert selection.items == []
    assert repository.calls == [
        {
            "user_id": "user-1",
            "goal_id": "goal-1",
            "memory_types": {
                "learning_preference",
                "long_term_goal",
                "mastery_summary",
                "learning_milestone",
            },
            "include_user_scope": True,
            "limit": 100,
            "now": NOW,
        }
    ]


def test_selection_uses_repository_active_filter_for_user_goal_disabled_and_expiry(
    db_session: Session,
):
    add_memory_scope(db_session)
    add_memory_scope(db_session, user_id="test-user", goal_id="other-goal")
    add_memory_scope(db_session, user_id="other-user", goal_id="foreign-goal")
    repository = SQLAlchemyMemoryRepository(db_session)
    visible_preference = repository.create_or_get(
        preference_command(idempotency_key="visible-preference"),
        now=NOW - timedelta(days=1),
    )
    visible_mastery = repository.create_or_get(
        mastery_command(idempotency_key="visible-mastery", expires_at=NOW + timedelta(days=1)),
        now=NOW - timedelta(days=1),
    )
    disabled = repository.create_or_get(
        preference_command(idempotency_key="disabled-preference", content=_content("learning_preference", 5)),
        now=NOW - timedelta(days=1),
    )
    repository.disable(
        user_id="test-user",
        memory_id=disabled.id,
        reason="user_revoked",
        now=NOW - timedelta(hours=1),
    )
    expires_at_boundary = repository.create_or_get(
        mastery_command(
            idempotency_key="boundary-mastery",
            content={**_content("mastery_summary", 6), "knowledge_node_id": "boundary"},
            expires_at=NOW,
        ),
        now=NOW - timedelta(days=1),
    )
    other_goal = repository.create_or_get(
        mastery_command(
            goal_id="other-goal",
            idempotency_key="other-goal-mastery",
            expires_at=NOW + timedelta(days=1),
        ),
        now=NOW - timedelta(days=1),
    )
    foreign_user = repository.create_or_get(
        preference_command(user_id="other-user", idempotency_key="foreign-user-preference"),
        now=NOW - timedelta(days=1),
    )
    assert memory_context_service is not None
    service = memory_context_service.MemoryContextService(repository, clock=lambda: NOW)

    selection = service.build(user_id="test-user", goal_id="test-goal", current_task=None)

    assert set(selection.selected_memory_ids) == {visible_preference.id, visible_mastery.id}
    assert disabled.id not in selection.selected_memory_ids
    assert expires_at_boundary.id not in selection.selected_memory_ids
    assert other_goal.id not in selection.selected_memory_ids
    assert foreign_user.id not in selection.selected_memory_ids


def test_selection_enforces_type_scope_confidence_dedupe_and_quotas():
    records = [
        _record(
            "preference-old",
            "learning_preference",
            content={"preference_key": "style", "preference_value": "old"},
            importance=0.9,
            confidence=0.8,
        ),
        _record(
            "preference-winner",
            "learning_preference",
            content={"preference_key": "style", "preference_value": "new"},
            importance=0.9,
            confidence=0.9,
        ),
        _record("preference-wrong-scope", "learning_preference", goal_id="goal-1"),
        _record("goal-user", "long_term_goal", importance=1.0),
        _record("goal-current", "long_term_goal", goal_id="goal-1", importance=0.2),
        _record("goal-extra", "long_term_goal", goal_id="goal-1", importance=0.1),
        _record(
            "mastery-current",
            "mastery_summary",
            goal_id="goal-1",
            content={**_content("mastery_summary", 8), "knowledge_node_id": "current-node", "score": 95.0},
            importance=0.1,
            confidence=0.6,
        ),
        _record(
            "mastery-low",
            "mastery_summary",
            goal_id="goal-1",
            content={**_content("mastery_summary", 1), "score": 21.0},
        ),
        _record(
            "mastery-high",
            "mastery_summary",
            goal_id="goal-1",
            content={**_content("mastery_summary", 9), "score": 90.0},
        ),
        _record("mastery-untrusted", "mastery_summary", goal_id="goal-1", confidence=0.59),
        _record("milestone-old", "learning_milestone", goal_id="goal-1", content=_content("learning_milestone", 1)),
        _record("milestone-new", "learning_milestone", goal_id="goal-1", content=_content("learning_milestone", 2)),
        _record("milestone-user", "learning_milestone"),
    ]
    service, _ = _service(records)

    selection = service.build(
        user_id="user-1",
        goal_id="goal-1",
        current_task={"knowledge_node_id": "current-node"},
    )

    selected_ids = selection.selected_memory_ids
    assert "preference-winner" in selected_ids
    assert "preference-old" not in selected_ids
    assert "preference-wrong-scope" not in selected_ids
    assert {"goal-current", "goal-extra"}.issubset(selected_ids)
    assert "goal-user" not in selected_ids
    assert "mastery-untrusted" not in selected_ids
    assert selected_ids.index("mastery-current") < selected_ids.index("mastery-low")
    assert selected_ids.index("milestone-new") < selected_ids.index("milestone-old")
    assert "milestone-user" not in selected_ids
    assert selection.skipped_by_budget == 0


def test_mastery_quota_keeps_current_node_then_lowest_scores():
    current = _record(
        "mastery-current",
        "mastery_summary",
        goal_id="goal-1",
        content={**_content("mastery_summary"), "knowledge_node_id": "current-node", "score": 99.0},
    )
    others = [
        _record(
            f"mastery-rank-{100 - score:02d}",
            "mastery_summary",
            goal_id="goal-1",
            content={**_content("mastery_summary", score), "score": float(score)},
        )
        for score in range(10, 80, 10)
    ]
    service, _ = _service([current, *others])

    selection = service.build(
        user_id="user-1",
        goal_id="goal-1",
        current_task={"knowledge_node_id": "current-node"},
    )

    selected_scores = {
        item.content["score"]
        for item in selection.items
        if item.memory_type == "mastery_summary" and item.memory_id != "mastery-current"
    }
    assert "mastery-current" in selection.selected_memory_ids
    assert selected_scores == {10.0, 20.0, 30.0, 40.0, 50.0}


def test_each_type_quota_is_applied_before_global_budget():
    records = [
        *[_record(f"preference-{i}", "learning_preference", content=_content("learning_preference", i)) for i in range(10)],
        *[_record(f"goal-{i}", "long_term_goal", goal_id="goal-1", content=_content("long_term_goal", i)) for i in range(4)],
        *[_record(f"mastery-{i}", "mastery_summary", goal_id="goal-1", content=_content("mastery_summary", i)) for i in range(8)],
        *[_record(f"milestone-{i}", "learning_milestone", goal_id="goal-1", content=_content("learning_milestone", i)) for i in range(6)],
    ]
    service, _ = _service(records)

    selection = service.build(user_id="user-1", goal_id="goal-1", current_task=None)

    counts = {
        memory_type: sum(item.memory_type == memory_type for item in selection.items)
        for memory_type in {item.memory_type for item in selection.items}
    }
    assert counts == {
        "learning_preference": 4,
        "long_term_goal": 2,
        "mastery_summary": 6,
        "learning_milestone": 4,
    }
    assert selection.skipped_by_budget == 4


@pytest.mark.parametrize(
    "record",
    [
        _record("foreign-user", "learning_preference", user_id="user-2"),
        _record("foreign-goal", "mastery_summary", goal_id="goal-2"),
    ],
)
def test_build_fails_closed_when_repository_returns_foreign_record(record: MemoryRecord):
    service, _ = _service([record])

    assert memory_context_service is not None
    with pytest.raises(memory_context_service.MemoryContextOwnershipError):
        service.build(user_id="user-1", goal_id="goal-1", current_task=None)


def test_unicode_is_counted_as_characters_and_selected_payload_is_canonical_json():
    unicode_value = "学习" * 1200
    record = _record(
        "unicode-preference",
        "learning_preference",
        content={"preference_key": "language", "preference_value": unicode_value},
    )
    service, _ = _service([record])

    selection = service.build(user_id="user-1", goal_id="goal-1", current_task=None)

    canonical = _canonical_items(selection.items)
    assert selection.selected_memory_ids == ["unicode-preference"]
    assert selection.serialized_char_count == len(canonical)
    assert selection.serialized_char_count < 6000
    assert len(canonical.encode("utf-8")) > 6000


def test_character_budget_accepts_exact_limit_and_stops_at_first_oversized_item():
    empty = _record(
        "exact",
        "learning_preference",
        content={"preference_key": "padding", "preference_value": ""},
        importance=1.0,
    )
    empty_item = TutorMemoryContext(
        memory_id=empty.id,
        memory_type=empty.memory_type,
        scope="user",
        content=empty.content,
        importance=empty.importance,
        confidence=empty.confidence,
        source_kind=empty.source_kind,
        expires_at=empty.expires_at,
    )
    padding = "x" * (6000 - len(_canonical_items([empty_item])))
    exact = empty.model_copy(
        update={"content": {"preference_key": "padding", "preference_value": padding}}
    )
    service, _ = _service([exact])

    exact_selection = service.build(user_id="user-1", goal_id="goal-1", current_task=None)

    assert exact_selection.serialized_char_count == 6000
    assert exact_selection.selected_memory_ids == ["exact"]

    oversized = exact.model_copy(
        update={
            "id": "oversized",
            "content": {"preference_key": "padding", "preference_value": padding + "x"},
        }
    )
    later = _record("later-small", "learning_preference", content=_content("learning_preference", 2))
    service, _ = _service([oversized, later])

    truncated = service.build(user_id="user-1", goal_id="goal-1", current_task=None)

    assert truncated.items == []
    assert truncated.serialized_char_count == 2
    assert truncated.skipped_by_budget == 2
