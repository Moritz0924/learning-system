from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from .contracts import CreateMemoryCommand
from .errors import InvalidMemoryContent, InvalidMemoryScope, UnsupportedMemoryType


_FORBIDDEN_KEYS = frozenset(
    {
        "messages",
        "raw_chat",
        "chat_history",
        "conversation_history",
        "transcript",
        "prompt",
        "system_prompt",
        "rag_text",
        "document_text",
        "document_content",
        "access_token",
        "refresh_token",
        "api_key",
        "password",
    }
)
_MEMORY_TYPES = frozenset(
    {"learning_preference", "long_term_goal", "mastery_summary", "learning_milestone"}
)


class _ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _LearningPreferenceContent(_ContentModel):
    preference_key: str = Field(min_length=1, max_length=64)
    preference_value: str | bool | float | list[str]

    @field_validator("preference_value")
    @classmethod
    def validate_preference_value(cls, value: str | bool | float | list[str]) -> str | bool | float | list[str]:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("preference value must be finite")
        if isinstance(value, list):
            if not value or len(value) > 20 or any(not isinstance(item, str) or not item for item in value):
                raise ValueError("preference list is invalid")
        return value


class _LongTermGoalContent(_ContentModel):
    title: str = Field(min_length=1, max_length=200)
    target_outcome: str = Field(min_length=1, max_length=1000)
    deadline: date | None = None

    @field_validator("deadline", mode="before")
    @classmethod
    def validate_deadline(cls, value: object) -> date | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
        if (
            not isinstance(value, str)
            or len(value) != 10
            or value[4] != "-"
            or value[7] != "-"
            or not (value[:4] + value[5:7] + value[8:]).isdigit()
        ):
            raise ValueError("deadline must be an ISO date")
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("deadline must be an ISO date") from error


class _MasterySummaryContent(_ContentModel):
    knowledge_node_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence_count: StrictInt = Field(ge=1)
    calculation_version: str = Field(min_length=1)

    @field_validator("score", "confidence", mode="before")
    @classmethod
    def validate_numeric_scalar(cls, value: object) -> int | float:
        if type(value) not in {int, float}:
            raise ValueError("numeric value must be an integer or float")
        return value


class _LearningMilestoneContent(_ContentModel):
    milestone_code: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    achieved_at: datetime
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("achieved_at", mode="before")
    @classmethod
    def validate_achieved_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = value.strip()
        if not isinstance(value, str) or "T" not in value and " " not in value:
            raise ValueError("achieved_at must be an ISO datetime")
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("achieved_at must be an ISO datetime") from error

    @field_validator("achieved_at")
    @classmethod
    def normalize_achieved_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("achieved_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("evidence references must be non-empty")
        return value


_CONTENT_MODELS: dict[str, type[_ContentModel]] = {
    "learning_preference": _LearningPreferenceContent,
    "long_term_goal": _LongTermGoalContent,
    "mastery_summary": _MasterySummaryContent,
    "learning_milestone": _LearningMilestoneContent,
}


@dataclass(frozen=True)
class ValidatedMemoryCommand:
    command: CreateMemoryCommand
    content_hash: str


def _invalid_content() -> InvalidMemoryContent:
    return InvalidMemoryContent("Memory content is invalid.")


def _normalize_tree(value: Any, *, depth: int = 1, nodes: list[int] | None = None) -> Any:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > 1000 or depth > 8:
        raise _invalid_content()

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise _invalid_content()
        return value.astimezone(timezone.utc).isoformat()
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid_content()
        return value
    if type(value) is dict:
        if len(value) > 100:
            raise _invalid_content()
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.strip().casefold() in _FORBIDDEN_KEYS:
                raise _invalid_content()
            normalized[key] = _normalize_tree(item, depth=depth + 1, nodes=nodes)
        return normalized
    if type(value) is list:
        if len(value) > 100:
            raise _invalid_content()
        return [_normalize_tree(item, depth=depth + 1, nodes=nodes) for item in value]
    raise _invalid_content()


def _validate_scope(command: CreateMemoryCommand) -> None:
    if command.memory_type == "learning_preference" and command.goal_id is not None:
        raise InvalidMemoryScope("Memory scope is invalid.")
    if command.memory_type in {"mastery_summary", "learning_milestone"} and command.goal_id is None:
        raise InvalidMemoryScope("Memory scope is invalid.")


def _validate_source(command: CreateMemoryCommand) -> None:
    source_ref_id = command.source_ref_id
    if source_ref_id is not None and not source_ref_id:
        raise _invalid_content()
    if command.source_kind != "explicit_user" and not source_ref_id:
        raise _invalid_content()


def _validate_expiry(command: CreateMemoryCommand, *, now: datetime) -> None:
    expires_at = command.expires_at
    if command.memory_type == "mastery_summary":
        if expires_at is None or expires_at > now + timedelta(days=30):
            raise _invalid_content()
    if command.memory_type == "learning_milestone" and expires_at is not None:
        raise _invalid_content()
    if expires_at is not None and expires_at <= now:
        raise _invalid_content()


def validate_memory_command(
    command: CreateMemoryCommand,
    *,
    now: datetime,
) -> ValidatedMemoryCommand:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)

    if command.memory_type not in _MEMORY_TYPES:
        raise UnsupportedMemoryType("Unsupported memory type.")

    _validate_scope(command)
    _validate_source(command)
    _validate_expiry(command, now=now)

    raw_content = command.content
    if command.memory_type == "long_term_goal" and type(raw_content.get("deadline")) is date:
        raw_content = {**raw_content, "deadline": raw_content["deadline"].isoformat()}
    normalized_content = _normalize_tree(raw_content)
    normalized_metadata = _normalize_tree(command.source_metadata)
    content_model = _CONTENT_MODELS[command.memory_type]
    try:
        content = content_model.model_validate(normalized_content).model_dump(mode="json")
    except ValidationError as error:
        raise _invalid_content() from error

    canonical_content = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoded_content = canonical_content.encode("utf-8")
    if len(encoded_content) > 8192:
        raise _invalid_content()

    return ValidatedMemoryCommand(
        command=command.model_copy(
            update={"content": content, "source_metadata": normalized_metadata, "expires_at": command.expires_at}
        ),
        content_hash=sha256(encoded_content).hexdigest(),
    )
