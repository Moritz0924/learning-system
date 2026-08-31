from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _normalized_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retrieval date filters must be timezone-aware")
    return value.astimezone(timezone.utc)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(nested) for key, nested in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(nested) for nested in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(nested) for nested in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(nested) for nested in value]
    if isinstance(value, frozenset):
        return sorted((_deep_thaw(nested) for nested in value), key=repr)
    return value


class RetrievalFilters(_StrictFrozenModel):
    document_ids: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    slide_numbers: tuple[int, ...] = ()
    min_trusted_level: int | None = Field(default=None, ge=0, le=5)
    max_trusted_level: int | None = Field(default=None, ge=0, le=5)
    created_from: datetime | None = None
    created_to: datetime | None = None
    index_version_ids: tuple[str, ...] = ()

    @field_validator("document_ids", "node_ids", "source_types", "index_version_ids")
    @classmethod
    def normalize_string_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalized_unique(value)

    @field_validator("page_numbers", "slide_numbers")
    @classmethod
    def normalize_positive_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if any(number <= 0 for number in normalized):
            raise ValueError("page and slide filters must be positive")
        return normalized

    @field_validator("created_from", "created_to")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def validate_ranges(self) -> "RetrievalFilters":
        if (
            self.min_trusted_level is not None
            and self.max_trusted_level is not None
            and self.min_trusted_level > self.max_trusted_level
        ):
            raise ValueError("minimum trusted level cannot exceed maximum trusted level")
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from cannot be after created_to")
        return self

    @property
    def has_metadata_constraints(self) -> bool:
        return bool(
            self.document_ids
            or self.node_ids
            or self.source_types
            or self.page_numbers
            or self.slide_numbers
            or self.min_trusted_level is not None
            or self.max_trusted_level is not None
            or self.created_from is not None
            or self.created_to is not None
            or self.index_version_ids
        )


class RetrievalRequest(_StrictFrozenModel):
    query: str = Field(min_length=1)
    user_id: str | None = Field(default=None, min_length=1)
    goal_id: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)


class QueryAnalysis(_StrictFrozenModel):
    original_query: str = Field(min_length=1)
    normalized_query: str = Field(min_length=1)
    tokens: tuple[str, ...]
    exact_terms: tuple[str, ...]


RetrievalSource = Literal["vector", "keyword", "metadata"]
RetrievalStatus = Literal["grounded", "no_context", "failed"]


class RetrievalCandidate(_StrictFrozenModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    index_version_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    citation_label: str = Field(min_length=1)
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    retriever: RetrievalSource
    query: str = Field(min_length=1)
    rank: int = Field(ge=1)
    raw_score: float = Field(allow_inf_nan=False)
    score_kind: str = Field(min_length=1)
    higher_is_better: bool

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _deep_freeze(value)

    @field_serializer("metadata", return_type=dict[str, Any])
    def serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _deep_thaw(value)


class CandidateScoreProvenance(_StrictFrozenModel):
    retriever: RetrievalSource
    query: str = Field(min_length=1)
    rank: int = Field(ge=1)
    raw_score: float = Field(allow_inf_nan=False)
    score_kind: str = Field(min_length=1)
    higher_is_better: bool
    rrf_contribution: float = Field(gt=0, allow_inf_nan=False)


class FusedCandidate(_StrictFrozenModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    index_version_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    citation_label: str = Field(min_length=1)
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    rrf_score: float = Field(gt=0, allow_inf_nan=False)
    fused_rank: int = Field(ge=1)
    provenance: tuple[CandidateScoreProvenance, ...] = Field(min_length=1)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False)
    reranked_rank: int | None = Field(default=None, ge=1)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return _deep_freeze(value)

    @field_serializer("metadata", return_type=dict[str, Any])
    def serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _deep_thaw(value)


class QueryRewriteTrace(_StrictFrozenModel):
    status: Literal["not_configured", "succeeded", "failed"]
    rewritten_queries: tuple[str, ...] = ()
    error_code: str | None = None


class RetrievalSourceTrace(_StrictFrozenModel):
    source: RetrievalSource
    query: str = Field(min_length=1)
    status: Literal["succeeded", "failed"]
    candidate_ids: tuple[str, ...] = ()
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    error_code: str | None = None


class RetrievalTrace(_StrictFrozenModel):
    original_query: str = Field(min_length=1)
    normalized_query: str = Field(min_length=1)
    exact_terms: tuple[str, ...]
    queries: tuple[str, ...]
    rewrite: QueryRewriteTrace
    source_attempts: tuple[RetrievalSourceTrace, ...]
    fused_candidates: tuple[FusedCandidate, ...] = ()
    reranked_candidates: tuple[FusedCandidate, ...] = ()
    selected_candidates: tuple[FusedCandidate, ...] = ()
    fusion_elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    rerank_elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    selection_elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    selected_char_count: int = Field(default=0, ge=0)
    rerank_status: Literal["not_run", "succeeded", "failed", "timed_out"] = (
        "not_run"
    )
    fallback_reasons: tuple[str, ...] = ()


class RetrievalResult(_StrictFrozenModel):
    status: RetrievalStatus
    request: RetrievalRequest
    analysis: QueryAnalysis
    queries: tuple[str, ...]
    candidates_by_source: Mapping[RetrievalSource, tuple[RetrievalCandidate, ...]]
    fused_candidates: tuple[FusedCandidate, ...] = ()
    reranked_candidates: tuple[FusedCandidate, ...] = ()
    selected_candidates: tuple[FusedCandidate, ...] = ()
    trace: RetrievalTrace
    error_code: str | None = None

    @field_validator("candidates_by_source", mode="after")
    @classmethod
    def freeze_candidate_lists(
        cls,
        value: Mapping[RetrievalSource, tuple[RetrievalCandidate, ...]],
    ) -> Mapping[RetrievalSource, tuple[RetrievalCandidate, ...]]:
        return MappingProxyType(
            {source: tuple(candidates) for source, candidates in value.items()}
        )

    @field_serializer("candidates_by_source", return_type=dict[str, Any])
    def serialize_candidate_lists(
        self,
        value: Mapping[RetrievalSource, tuple[RetrievalCandidate, ...]],
    ) -> dict[str, Any]:
        return {
            source: [candidate.model_dump() for candidate in candidates]
            for source, candidates in value.items()
        }

    @property
    def raw_candidate_lists(
        self,
    ) -> Mapping[RetrievalSource, tuple[RetrievalCandidate, ...]]:
        return self.candidates_by_source
