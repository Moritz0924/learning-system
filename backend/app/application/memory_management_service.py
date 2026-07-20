from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.api.schemas.memories import MemoryListResponse, MemoryPublicResponse
from backend.app.application.memory_privacy_service import MemoryPrivacyService
from backend.app.domain.memory import (
    MemoryListStatus,
    MemoryNotFound,
    MemoryPrivacySettings,
    MemoryRecord,
    MemorySourceKind,
    MemoryType,
)
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository


_SOURCE_CATEGORY_KINDS: dict[str, set[MemorySourceKind]] = {
    "explicit_user_statement": {"explicit_user"},
    "system_inference": {"system_derived"},
    "learning_result": {"assessment", "mastery_record", "learning_event"},
}


@dataclass
class MemoryManagementService:
    session: Session
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def list(
        self,
        *,
        user_id: str,
        goal_id: str | None,
        memory_type: MemoryType | None,
        source_category: str | None,
        status: MemoryListStatus,
        include_user_scope: bool,
        limit: int,
        offset: int,
    ) -> MemoryListResponse:
        source_kinds = None if source_category is None else _SOURCE_CATEGORY_KINDS[source_category]
        records = SQLAlchemyMemoryRepository(self.session).list_memories(
            user_id=user_id,
            goal_id=goal_id,
            memory_types=None if memory_type is None else {memory_type},
            source_kinds=source_kinds,
            status=status,
            include_user_scope=include_user_scope,
            limit=limit,
            offset=offset,
            now=self.clock(),
        )
        items = [_to_public(record) for record in records]
        return MemoryListResponse(
            items=items,
            limit=limit,
            offset=offset,
            returned_count=len(items),
        )

    def get(self, *, user_id: str, memory_id: str) -> MemoryPublicResponse:
        record = SQLAlchemyMemoryRepository(self.session).get_by_id(
            user_id=user_id,
            memory_id=memory_id,
            include_inactive=True,
            now=self.clock(),
        )
        if record is None:
            raise MemoryNotFound("Memory was not found.")
        return _to_public(record)

    def disable(self, *, user_id: str, memory_id: str) -> MemoryPublicResponse:
        record = SQLAlchemyMemoryRepository(self.session).disable(
            user_id=user_id,
            memory_id=memory_id,
            reason="user_revoked",
            now=self.clock(),
        )
        return _to_public(record)

    def get_privacy(self, *, user_id: str) -> MemoryPrivacySettings:
        return MemoryPrivacyService(self.session).get(user_id=user_id)

    def update_privacy(
        self,
        *,
        user_id: str,
        settings: MemoryPrivacySettings,
    ) -> MemoryPrivacySettings:
        return MemoryPrivacyService(self.session).update(user_id=user_id, settings=settings)


def _to_public(record: MemoryRecord) -> MemoryPublicResponse:
    if record.source_kind == "explicit_user":
        origin = "explicit_user_statement"
    elif record.source_kind == "system_derived":
        origin = "system_inference"
    else:
        origin = "learning_result"
    return MemoryPublicResponse(
        memory_id=record.id,
        goal_id=record.goal_id,
        scope="goal" if record.goal_id is not None else "user",
        memory_type=record.memory_type,
        content=record.content,
        origin=origin,
        source_kind=record.source_kind,
        importance=record.importance,
        confidence=record.confidence,
        is_enabled=record.is_enabled,
        expires_at=record.expires_at,
        disabled_at=record.disabled_at,
        disabled_reason=record.disabled_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
