"""Runtime-safe telemetry contracts shared by production and offline evaluation."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import RetrievedChunk


class RetrievalScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_value: float
    score_kind: Literal["cosine_similarity", "cosine_distance"]
    higher_is_better: bool


class TimedRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks: list[RetrievedChunk]
    scores: list[RetrievalScore]
    embedding_latency_ms: float | None = Field(default=None, ge=0)
    vector_search_latency_ms: float | None = Field(default=None, ge=0)
    postprocess_latency_ms: float = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    backend: str
    top_k: int = Field(ge=1)
    status: Literal["grounded", "no_context", "failed"]
    error_code: str | None = None


class TimedLlmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    mode: Literal["remote", "mock", "offline", "degraded"]
    request_latency_ms: float = Field(ge=0)
    parse_latency_ms: float = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    retry_count: int = Field(ge=0)
