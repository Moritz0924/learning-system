from __future__ import annotations

from datetime import datetime, timezone

from backend.app.application.memory_context_service import MemoryContextService
from backend.app.domain.memory import MemoryPrivacySettings, MemoryRecord


NOW = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records
        self.calls = 0

    def list_active(self, **kwargs):
        self.calls += 1
        return list(self.records)


def _record(memory_id: str, source_kind: str) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        user_id="user-1",
        goal_id=None,
        memory_type="learning_preference",
        schema_version="memory-v1",
        content={"preference_key": memory_id, "preference_value": "value"},
        content_hash="a" * 64,
        source_kind=source_kind,
        source_ref_id=None if source_kind == "explicit_user" else f"source-{memory_id}",
        source_metadata={},
        importance=0.5,
        confidence=0.9,
        is_enabled=True,
        expires_at=None,
        disabled_at=None,
        disabled_reason=None,
        idempotency_key=f"key:{memory_id}",
        created_at=NOW,
        updated_at=NOW,
    )


def test_master_privacy_switch_returns_empty_context_without_scanning_records() -> None:
    repository = _Repository([_record("explicit", "explicit_user")])

    selection = MemoryContextService(repository, clock=lambda: NOW).build(
        user_id="user-1",
        goal_id="goal-1",
        current_task=None,
        privacy_settings=MemoryPrivacySettings(enabled=False),
    )

    assert selection.items == []
    assert repository.calls == 0


def test_source_switches_hide_and_restore_historical_memories() -> None:
    repository = _Repository(
        [
            _record("explicit", "explicit_user"),
            _record("system", "system_derived"),
            _record("learning", "assessment"),
        ]
    )
    service = MemoryContextService(repository, clock=lambda: NOW)

    hidden = service.build(
        user_id="user-1",
        goal_id="goal-1",
        current_task=None,
        privacy_settings=MemoryPrivacySettings(
            allow_explicit_user=False,
            allow_system_inference=False,
            allow_learning_results=True,
        ),
    )
    restored = service.build(
        user_id="user-1",
        goal_id="goal-1",
        current_task=None,
        privacy_settings=MemoryPrivacySettings(
            allow_explicit_user=True,
            allow_system_inference=True,
            allow_learning_results=True,
        ),
    )

    assert hidden.selected_memory_ids == ["learning"]
    assert set(restored.selected_memory_ids) == {"explicit", "system", "learning"}
