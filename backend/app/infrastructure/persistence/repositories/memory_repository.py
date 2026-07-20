from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import get_args
from uuid import uuid4

from sqlalchemy import and_, not_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.domain.memory import (
    CreateMemoryCommand,
    MemoryIdempotencyConflict,
    MemoryNotFound,
    MemoryRecord,
    MemoryScopeNotFound,
    MemorySourceKind,
    MemoryType,
    UnsupportedMemoryType,
    validate_memory_command,
)
from backend.app.models import LearningGoal, Memory, User


_DISABLE_REASONS = frozenset(
    {
        "user_revoked",
        "superseded",
        "incorrect",
        "expired_by_policy",
        "source_invalidated",
        "privacy_request",
    }
)
_MEMORY_TYPES = frozenset(get_args(MemoryType))
_MEMORY_SOURCE_KINDS = frozenset(get_args(MemorySourceKind))
_MEMORY_LIST_STATUSES = frozenset({"active", "inactive", "all"})


def _effective_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _database_datetime_as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_record(memory: Memory) -> MemoryRecord:
    return MemoryRecord(
        id=memory.id,
        user_id=memory.user_id,
        goal_id=memory.goal_id,
        memory_type=memory.memory_type,
        schema_version=memory.schema_version,
        content=memory.content_json,
        content_hash=memory.content_hash,
        source_kind=memory.source_kind,
        source_ref_id=memory.source_ref_id,
        source_metadata=memory.source_metadata,
        importance=memory.importance,
        confidence=memory.confidence,
        is_enabled=memory.is_enabled,
        expires_at=_database_datetime_as_utc(memory.expires_at),
        disabled_at=_database_datetime_as_utc(memory.disabled_at),
        disabled_reason=memory.disabled_reason,
        idempotency_key=memory.idempotency_key,
        created_at=_database_datetime_as_utc(memory.created_at),
        updated_at=_database_datetime_as_utc(memory.updated_at),
    )


def _immutable_fields_match(memory: Memory, command: CreateMemoryCommand, content_hash: str) -> bool:
    return (
        memory.goal_id == command.goal_id
        and memory.memory_type == command.memory_type
        and memory.schema_version == command.schema_version
        and memory.content_hash == content_hash
        and memory.source_kind == command.source_kind
        and memory.source_ref_id == command.source_ref_id
        and memory.source_metadata == command.source_metadata
        and memory.importance == command.importance
        and memory.confidence == command.confidence
        and _database_datetime_as_utc(memory.expires_at) == command.expires_at
    )


def _idempotency_result(
    memory: Memory,
    command: CreateMemoryCommand,
    content_hash: str,
) -> MemoryRecord:
    if not _immutable_fields_match(memory, command, content_hash):
        raise MemoryIdempotencyConflict("Memory idempotency conflict.")
    return _to_record(memory)


@dataclass
class SQLAlchemyMemoryRepository:
    session: Session

    def create_or_get(
        self,
        command: CreateMemoryCommand,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        effective_now = _effective_now(now)
        validated = validate_memory_command(command, now=effective_now)
        normalized = validated.command

        user_exists = self.session.scalar(select(User.id).where(User.id == normalized.user_id))
        if user_exists is None:
            raise MemoryScopeNotFound("Memory scope was not found.")

        if normalized.goal_id is not None:
            goal_exists = self.session.scalar(
                select(LearningGoal.id).where(
                    LearningGoal.id == normalized.goal_id,
                    LearningGoal.user_id == normalized.user_id,
                )
            )
            if goal_exists is None:
                raise MemoryScopeNotFound("Memory scope was not found.")

        existing = self._find_by_user_and_key(normalized.user_id, normalized.idempotency_key)
        if existing is not None:
            return _idempotency_result(existing, normalized, validated.content_hash)

        memory = Memory(
            id=f"memory-{uuid4()}",
            user_id=normalized.user_id,
            goal_id=normalized.goal_id,
            memory_type=normalized.memory_type,
            schema_version=normalized.schema_version,
            content_json=normalized.content,
            content_hash=validated.content_hash,
            source_kind=normalized.source_kind,
            source_ref_id=normalized.source_ref_id,
            source_metadata=normalized.source_metadata,
            importance=normalized.importance,
            confidence=normalized.confidence,
            is_enabled=True,
            expires_at=normalized.expires_at,
            disabled_at=None,
            disabled_reason=None,
            idempotency_key=normalized.idempotency_key,
            created_at=effective_now,
            updated_at=effective_now,
        )
        try:
            with self.session.begin_nested():
                self.session.add(memory)
                self.session.flush()
        except IntegrityError:
            existing = self._find_by_user_and_key(normalized.user_id, normalized.idempotency_key)
            if existing is None:
                raise
            return _idempotency_result(existing, normalized, validated.content_hash)
        return _to_record(memory)

    def get_by_id(
        self,
        *,
        user_id: str,
        memory_id: str,
        include_inactive: bool = False,
        now: datetime | None = None,
    ) -> MemoryRecord | None:
        effective_now = _effective_now(now)
        statement = select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        if not include_inactive:
            statement = statement.where(*self._active_filters(effective_now))
        memory = self.session.scalar(statement)
        return None if memory is None else _to_record(memory)

    def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> MemoryRecord | None:
        memory = self._find_by_user_and_key(user_id, idempotency_key)
        return None if memory is None else _to_record(memory)

    def list_active(
        self,
        *,
        user_id: str,
        goal_id: str | None = None,
        memory_types: set[MemoryType] | None = None,
        include_user_scope: bool = True,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Memory list limit is invalid.")
        if memory_types is not None:
            if any(memory_type not in _MEMORY_TYPES for memory_type in memory_types):
                raise UnsupportedMemoryType("Unsupported memory type.")
            if not memory_types:
                return []

        effective_now = _effective_now(now)
        statement = select(Memory).where(
            Memory.user_id == user_id,
            *self._active_filters(effective_now),
        )
        if goal_id is None:
            statement = statement.where(Memory.goal_id.is_(None))
        elif include_user_scope:
            statement = statement.where(or_(Memory.goal_id.is_(None), Memory.goal_id == goal_id))
        else:
            statement = statement.where(Memory.goal_id == goal_id)
        if memory_types is not None:
            statement = statement.where(Memory.memory_type.in_(memory_types))
        statement = statement.order_by(
            Memory.importance.desc(),
            Memory.created_at.desc(),
            Memory.id.asc(),
        ).limit(limit)
        return [_to_record(memory) for memory in self.session.scalars(statement)]

    def disable(
        self,
        *,
        user_id: str,
        memory_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> MemoryRecord:
        normalized_reason = reason.strip() if isinstance(reason, str) else ""
        if normalized_reason not in _DISABLE_REASONS:
            raise ValueError("Memory disable reason is invalid.")

        effective_now = _effective_now(now)
        self.session.execute(
            update(Memory)
            .where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                Memory.is_enabled.is_(True),
            )
            .values(
                is_enabled=False,
                disabled_at=effective_now,
                disabled_reason=normalized_reason,
                updated_at=effective_now,
            ),
            execution_options={"synchronize_session": False},
        )
        self.session.flush()
        memory = self.session.scalar(
            select(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if memory is None:
            raise MemoryNotFound("Memory was not found.")
        return _to_record(memory)

    def list_memories(
        self,
        *,
        user_id: str,
        goal_id: str | None = None,
        memory_types: set[MemoryType] | None = None,
        source_kinds: set[MemorySourceKind] | None = None,
        status: str = "all",
        include_user_scope: bool = True,
        limit: int = 50,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("Memory list limit is invalid.")
        if offset < 0:
            raise ValueError("Memory list offset is invalid.")
        if status not in _MEMORY_LIST_STATUSES:
            raise ValueError("Memory list status is invalid.")
        if memory_types is not None:
            if any(memory_type not in _MEMORY_TYPES for memory_type in memory_types):
                raise UnsupportedMemoryType("Unsupported memory type.")
            if not memory_types:
                return []
        if source_kinds is not None:
            if any(source_kind not in _MEMORY_SOURCE_KINDS for source_kind in source_kinds):
                raise ValueError("Memory source kind is invalid.")
            if not source_kinds:
                return []

        effective_now = _effective_now(now)
        statement = select(Memory).where(Memory.user_id == user_id)
        if goal_id is not None:
            if include_user_scope:
                statement = statement.where(or_(Memory.goal_id.is_(None), Memory.goal_id == goal_id))
            else:
                statement = statement.where(Memory.goal_id == goal_id)
        if memory_types is not None:
            statement = statement.where(Memory.memory_type.in_(memory_types))
        if source_kinds is not None:
            statement = statement.where(Memory.source_kind.in_(source_kinds))
        active_expression = and_(*self._active_filters(effective_now))
        if status == "active":
            statement = statement.where(active_expression)
        elif status == "inactive":
            statement = statement.where(not_(active_expression))
        statement = statement.order_by(Memory.created_at.desc(), Memory.id.asc()).offset(offset).limit(limit)
        return [_to_record(memory) for memory in self.session.scalars(statement)]

    def _find_by_user_and_key(self, user_id: str, idempotency_key: str) -> Memory | None:
        return self.session.scalar(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _active_filters(now: datetime):
        return (
            Memory.is_enabled.is_(True),
            or_(Memory.expires_at.is_(None), Memory.expires_at > now),
        )
