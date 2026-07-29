from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator


MemoryType = Literal["learning_preference", "long_term_goal", "mastery_summary", "learning_milestone"]
MemorySourceKind = Literal["explicit_user", "learning_event", "assessment", "mastery_record", "system_derived"]
MemoryCandidateOrigin = Literal["explicit_user_statement", "system_inference", "learning_result"]
MemoryDecisionKind = Literal["approved", "rejected"]
MemoryWriteStatus = Literal["saved", "reused", "rejected", "conflict"]
MEMORY_GATE_POLICY_VERSION = "memory-gate-v1"


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
    idempotency_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9:._/\\-]+$")

    @field_validator("expires_at")
    @classmethod
    def normalize_expires_at(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("content", "source_metadata", mode="before")
    @classmethod
    def require_plain_dictionaries(cls, value: Any) -> Any:
        return _require_plain_dict(value)


class MemoryPrivacySettings(_StrictFrozenModel):
    enabled: StrictBool = True
    allow_explicit_user: StrictBool = True
    allow_system_inference: StrictBool = False
    allow_learning_results: StrictBool = True


class MemoryCandidate(_StrictFrozenModel):
    candidate_id: StrictStr = Field(min_length=1, max_length=160)
    origin: MemoryCandidateOrigin
    command: CreateMemoryCommand
    semantic_key: StrictStr = Field(min_length=1, max_length=320)
    policy_version: Literal["memory-gate-v1"] = "memory-gate-v1"


class MemoryDecision(_StrictFrozenModel):
    candidate: MemoryCandidate
    decision: MemoryDecisionKind
    reason_code: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")


class MemoryWriteReceipt(_StrictFrozenModel):
    candidate_id: StrictStr = Field(min_length=1, max_length=160)
    origin: MemoryCandidateOrigin
    status: MemoryWriteStatus
    reason_code: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    memory_id: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_memory_id(self) -> "MemoryWriteReceipt":
        if self.status in {"saved", "reused"} and self.memory_id is None:
            raise ValueError("saved memory receipts require a memory id")
        if self.status == "rejected" and self.memory_id is not None:
            raise ValueError("rejected memory receipts cannot include a memory id")
        return self
