from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.memory import MemoryPrivacySettings, MemorySourceKind, MemoryType


MemoryOriginPublic = Literal[
    "explicit_user_statement",
    "system_inference",
    "learning_result",
]


class MemoryPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    goal_id: str | None
    scope: Literal["user", "goal"]
    memory_type: MemoryType
    content: dict[str, Any]
    origin: MemoryOriginPublic
    source_kind: MemorySourceKind
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    is_enabled: bool
    expires_at: datetime | None
    disabled_at: datetime | None
    disabled_reason: str | None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[MemoryPublicResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    returned_count: int = Field(ge=0)


MemoryPrivacyResponse = MemoryPrivacySettings
