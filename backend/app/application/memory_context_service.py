from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from adaptive_tutor.phase2.schemas import (
    MemoryContextSelection,
    TutorContext,
    TutorMasteryItem,
    TutorMemoryContext,
    TutorTaskContext,
)
from backend.app.domain.memory import MemoryPrivacySettings, MemoryRecord, MemoryRepository, MemoryType


MEMORY_CONTEXT_POLICY_VERSION = "memory-context-v1"
MEMORY_CONTEXT_SCAN_LIMIT = 100
MEMORY_CONTEXT_ITEM_LIMIT = 16
MEMORY_CONTEXT_CHAR_LIMIT = 6000
_MEMORY_TYPES: set[MemoryType] = {
    "learning_preference",
    "long_term_goal",
    "mastery_summary",
    "learning_milestone",
}


class MemoryContextError(ValueError):
    pass


class MemoryContextOwnershipError(MemoryContextError):
    pass


class MemoryContextAssemblyError(MemoryContextError):
    pass


class MemoryContextService:
    def __init__(
        self,
        repository: MemoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def build(
        self,
        *,
        user_id: str,
        goal_id: str,
        current_task: Mapping[str, Any] | TutorTaskContext | None,
        privacy_settings: MemoryPrivacySettings | None = None,
    ) -> MemoryContextSelection:
        if privacy_settings is not None and not privacy_settings.enabled:
            return MemoryContextSelection()
        records = self._repository.list_active(
            user_id=user_id,
            goal_id=goal_id,
            memory_types=set(_MEMORY_TYPES),
            include_user_scope=True,
            limit=MEMORY_CONTEXT_SCAN_LIMIT,
            now=self._clock(),
        )
        if privacy_settings is not None:
            records = [
                record
                for record in records
                if _source_is_allowed(record, privacy_settings)
            ]
        self._verify_ownership(records, user_id=user_id, goal_id=goal_id)
        current_node_id = _current_node_id(current_task)
        eligible = self._eligible_records(records, goal_id=goal_id, current_node_id=current_node_id)
        ordered = sorted(
            eligible,
            key=lambda record: _global_sort_key(
                record,
                goal_id=goal_id,
                current_node_id=current_node_id,
            ),
        )
        return _apply_global_budget(ordered)

    @staticmethod
    def _verify_ownership(records: list[MemoryRecord], *, user_id: str, goal_id: str) -> None:
        if any(
            record.user_id != user_id or record.goal_id not in {None, goal_id}
            for record in records
        ):
            raise MemoryContextOwnershipError("Memory context ownership validation failed.")

    @staticmethod
    def _eligible_records(
        records: list[MemoryRecord],
        *,
        goal_id: str,
        current_node_id: str | None,
    ) -> list[MemoryRecord]:
        preferences = _select_preferences(records)
        long_term_goals = sorted(
            (record for record in records if record.memory_type == "long_term_goal"),
            key=lambda record: (
                0 if record.goal_id == goal_id else 1,
                *_quality_sort_key(record),
            ),
        )[:2]
        mastery = sorted(
            (
                record
                for record in records
                if record.memory_type == "mastery_summary"
                and record.goal_id == goal_id
                and record.confidence >= 0.6
            ),
            key=lambda record: (
                0 if _mastery_node_id(record) == current_node_id and current_node_id is not None else 1,
                _mastery_score(record),
                *_quality_sort_key(record),
            ),
        )[:6]
        milestones = sorted(
            (
                record
                for record in records
                if record.memory_type == "learning_milestone" and record.goal_id == goal_id
            ),
            key=lambda record: (
                -_milestone_achieved_at(record).timestamp(),
                *_quality_sort_key(record),
            ),
        )[:4]
        return [*preferences, *long_term_goals, *mastery, *milestones]


def build_tutor_context(
    snapshot: dict[str, Any],
    *,
    memory_selection: MemoryContextSelection | None = None,
) -> TutorContext:
    current_task = snapshot.get("current_task")
    selection = memory_selection or MemoryContextSelection()
    return TutorContext(
        learning_goal=snapshot["learning_goal"],
        current_task=current_task,
        mastery_summary=_tutor_mastery_summary(
            snapshot.get("mastery_summary", {}),
            current_task,
        ),
        learning_preferences=snapshot.get("learning_preferences", {}),
        recent_learning_events=snapshot.get("recent_learning_events", []),
        long_term_memories=selection.items,
    )


def _tutor_mastery_summary(
    mastery_summary: dict[str, Any],
    current_task: dict[str, Any] | None,
) -> list[TutorMasteryItem]:
    items: list[TutorMasteryItem] = []
    for knowledge_node_id, raw_value in mastery_summary.items():
        if not isinstance(raw_value, dict):
            continue
        score = raw_value.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        confidence = raw_value.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = None
        evidence_count = raw_value.get("evidence_count")
        if isinstance(evidence_count, bool) or not isinstance(evidence_count, int):
            evidence_count = None
        items.append(
            TutorMasteryItem(
                knowledge_node_id=knowledge_node_id,
                score=float(score),
                confidence=float(confidence) if confidence is not None else None,
                evidence_count=evidence_count,
            )
        )
    current_node_id = current_task.get("knowledge_node_id") if current_task else None
    items.sort(
        key=lambda item: (
            0 if item.knowledge_node_id == current_node_id else 1,
            item.score,
            item.knowledge_node_id,
        )
    )
    return items[:12]


def _select_preferences(records: list[MemoryRecord]) -> list[MemoryRecord]:
    winners: dict[str, MemoryRecord] = {}
    for record in records:
        if record.memory_type != "learning_preference" or record.goal_id is not None:
            continue
        key = _required_string(record, "preference_key")
        existing = winners.get(key)
        if existing is None or _quality_sort_key(record) < _quality_sort_key(existing):
            winners[key] = record
    return sorted(winners.values(), key=_quality_sort_key)[:8]


def _quality_sort_key(record: MemoryRecord) -> tuple[float, float, float, str]:
    return (-record.importance, -record.confidence, -record.created_at.timestamp(), record.id)


def _global_sort_key(
    record: MemoryRecord,
    *,
    goal_id: str,
    current_node_id: str | None,
) -> tuple[int, int, float, float, float, str]:
    return (
        0 if record.goal_id == goal_id else 1,
        0
        if record.memory_type == "mastery_summary"
        and current_node_id is not None
        and _mastery_node_id(record) == current_node_id
        else 1,
        *_quality_sort_key(record),
    )


def _current_node_id(current_task: Mapping[str, Any] | TutorTaskContext | None) -> str | None:
    if current_task is None:
        return None
    value = (
        current_task.knowledge_node_id
        if isinstance(current_task, TutorTaskContext)
        else current_task.get("knowledge_node_id")
    )
    return value if isinstance(value, str) and value else None


def _required_string(record: MemoryRecord, field: str) -> str:
    value = record.content.get(field)
    if not isinstance(value, str) or not value:
        raise MemoryContextAssemblyError("Memory context content is invalid.")
    return value


def _mastery_node_id(record: MemoryRecord) -> str:
    return _required_string(record, "knowledge_node_id")


def _mastery_score(record: MemoryRecord) -> float:
    value = record.content.get("score")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MemoryContextAssemblyError("Memory context content is invalid.")
    return float(value)


def _milestone_achieved_at(record: MemoryRecord) -> datetime:
    raw_value = _required_string(record, "achieved_at")
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MemoryContextAssemblyError("Memory context content is invalid.") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise MemoryContextAssemblyError("Memory context content is invalid.")
    return value


def _to_tutor_memory(record: MemoryRecord) -> TutorMemoryContext:
    return TutorMemoryContext(
        memory_id=record.id,
        memory_type=record.memory_type,
        scope="goal" if record.goal_id is not None else "user",
        content=record.content,
        importance=record.importance,
        confidence=record.confidence,
        source_kind=record.source_kind,
        expires_at=record.expires_at,
    )


def _canonical_items(items: list[TutorMemoryContext]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _apply_global_budget(records: list[MemoryRecord]) -> MemoryContextSelection:
    items: list[TutorMemoryContext] = []
    serialized = "[]"
    skipped_by_budget = 0
    for index, record in enumerate(records):
        if len(items) >= MEMORY_CONTEXT_ITEM_LIMIT:
            skipped_by_budget = len(records) - index
            break
        item = _to_tutor_memory(record)
        candidate_serialized = _canonical_items([*items, item])
        if len(candidate_serialized) > MEMORY_CONTEXT_CHAR_LIMIT:
            skipped_by_budget = len(records) - index
            break
        items.append(item)
        serialized = candidate_serialized
    return MemoryContextSelection(
        items=items,
        selected_memory_ids=[item.memory_id for item in items],
        skipped_by_budget=skipped_by_budget,
        policy_version=MEMORY_CONTEXT_POLICY_VERSION,
        serialized_char_count=len(serialized),
    )


def _source_is_allowed(record: MemoryRecord, settings: MemoryPrivacySettings) -> bool:
    if record.source_kind == "explicit_user":
        return settings.allow_explicit_user
    if record.source_kind == "system_derived":
        return settings.allow_system_inference
    return settings.allow_learning_results
