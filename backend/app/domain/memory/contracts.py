from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


MemoryType = Literal[
    "learning_preference",
    "long_term_goal",
    "mastery_summary",
    "learning_milestone",
]

MemorySourceKind = Literal[
    "explicit_user",
    "learning_event",
    "assessment",
    "mastery_record",
    "system_derived",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_plain_dict(value: Any) -> Any:
    if type(value) is not dict:
        raise ValueError("value must be a plain dictionary")
    return value


class CreateMemoryCommand(_StrictFrozenModel):
    user_id: str = Field(min_length=1)
    goal_id: str | None = Field(default=None, min_length=1)
    memory_type: MemoryType
    schema_version: str = Field(default="memory-v1", min_length=1)
    content: dict[str, Any]
    source_kind: MemorySourceKind
    source_ref_id: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    expires_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9:._/\-]+$")

    @field_validator("expires_at")
    @classmethod
    def normalize_expires_at(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("content", "source_metadata", mode="before")
    @classmethod
    def require_plain_dictionaries(cls, value: Any) -> Any:
        return _require_plain_dict(value)


class MemoryRecord(_StrictFrozenModel):
    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    goal_id: str | None = Field(default=None, min_length=1)
    memory_type: MemoryType
    schema_version: str = Field(min_length=1)
    content: dict[str, Any]
    content_hash: str = Field(min_length=1)
    source_kind: MemorySourceKind
    source_ref_id: str | None = None
    source_metadata: dict[str, Any]
    importance: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    is_enabled: bool
    expires_at: datetime | None = None
    disabled_at: datetime | None = None
    disabled_reason: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9:._/\-]+$")
    created_at: datetime
    updated_at: datetime

    @field_validator("expires_at", "disabled_at", "created_at", "updated_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("content", "source_metadata", mode="before")
    @classmethod
    def require_plain_dictionaries(cls, value: Any) -> Any:
        return _require_plain_dict(value)


class MemoryRepository(Protocol):
    def create_or_get(self, command: CreateMemoryCommand, *, now: datetime | None = None) -> MemoryRecord: ...

    def get_by_id(
        self,
        *,
        user_id: str,
        memory_id: str,
        include_inactive: bool = False,
        now: datetime | None = None,
    ) -> MemoryRecord | None: ...

    def list_active(
        self,
        *,
        user_id: str,
        goal_id: str | None = None,
        memory_types: set[MemoryType] | None = None,
        include_user_scope: bool = True,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[MemoryRecord]: ...

    def disable(
        self,
        *,
        user_id: str,
        memory_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> MemoryRecord: ...
