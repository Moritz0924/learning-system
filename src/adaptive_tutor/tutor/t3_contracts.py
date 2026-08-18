"""Small, dependency-free contracts for the grounded-intelligence path."""

from __future__ import annotations

import json
import re
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroundingStatus(str, Enum):
    SUPPORTED = "supported"
    SEMANTIC_UNVERIFIED = "semantic_unverified"
    REPAIR_REQUIRED = "repair_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SAFE_REFUSAL = "safe_refusal"
    VALIDATION_ERROR = "validation_error"


class Thread3ErrorCode(str, Enum):
    UPSTREAM_TIMEOUT = "T3_UPSTREAM_TIMEOUT"
    UPSTREAM_INVALID_STRUCTURED_OUTPUT = "T3_UPSTREAM_INVALID_STRUCTURED_OUTPUT"
    RETRIEVAL_SNAPSHOT_MISMATCH = "T3_RETRIEVAL_SNAPSHOT_MISMATCH"
    CITATION_PROVENANCE_INVALID = "T3_CITATION_PROVENANCE_INVALID"
    IDEMPOTENCY_CONFLICT = "T3_IDEMPOTENCY_CONFLICT"
    PLAN_VERSION_CONFLICT = "T3_PLAN_VERSION_CONFLICT"
    PLAN_PROPOSAL_NOT_ACTIONABLE = "T3_PLAN_PROPOSAL_NOT_ACTIONABLE"
    TOOL_NOT_ALLOWED = "T3_TOOL_NOT_ALLOWED"
    TOOL_BUDGET_EXCEEDED = "T3_TOOL_BUDGET_EXCEEDED"
    TOOL_ARGUMENT_INVALID = "T3_TOOL_ARGUMENT_INVALID"
    TOOL_TIMEOUT = "T3_TOOL_TIMEOUT"
    TOOL_RESULT_TOO_LARGE = "T3_TOOL_RESULT_TOO_LARGE"
    TOOL_EXECUTION_FAILED = "T3_TOOL_EXECUTION_FAILED"
    TOOL_EVIDENCE_MAPPING_FAILED = "T3_TOOL_EVIDENCE_MAPPING_FAILED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalEvidenceItem(_FrozenModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)


class RetrievalEvidenceSnapshot(_FrozenModel):
    snapshot_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    retrieval_run_id: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    selected_context: tuple[RetrievalEvidenceItem, ...]


class PublicCitation(_FrozenModel):
    citation_id: str = Field(min_length=1)
    title: str | None = None
    source_type: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    excerpt: str | None = Field(default=None, max_length=500)
    citation_label: str | None = None
    source_title: str | None = None
    source_url: str | None = None


class GroundedCitationRef(_FrozenModel):
    evidence_id: str | None = Field(default=None, min_length=1)
    chunk_id: str | None = Field(default=None, min_length=1)
    document_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_provenance_mode(self) -> GroundedCitationRef:
        has_evidence = self.evidence_id is not None
        has_legacy = self.chunk_id is not None or self.document_id is not None
        if has_evidence == has_legacy:
            raise ValueError("citation must use exactly one provenance mode")
        if has_legacy and (self.chunk_id is None or self.document_id is None):
            raise ValueError("legacy citation requires chunk_id and document_id")
        return self


class GroundedClaim(_FrozenModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    citation_refs: tuple[GroundedCitationRef, ...] = ()


class GroundedAnswerDraft(_FrozenModel):
    answer: str
    claims: tuple[GroundedClaim, ...] = ()
    citations: tuple[GroundedCitationRef, ...] = ()
    insufficient_evidence: bool = False
    missing_information: tuple[str, ...] = ()


class ClaimSupportResult(_FrozenModel):
    supported: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None


class ClaimSupportJudgePort(Protocol):
    def validate(self, claim: GroundedClaim, cited_chunks: list[Any]) -> ClaimSupportResult:
        ...


class MasteryPolicy(_FrozenModel):
    minimum_confidence_for_update: float = Field(default=0.55, ge=0, le=1)
    evidence_count_for_full_weight: int = Field(default=3, ge=1)
    base_learning_rate: float = Field(default=0.35, ge=0, le=1)
    max_positive_delta: float = Field(default=0.15, ge=0, le=1)
    max_negative_delta: float = Field(default=0.15, ge=0, le=1)
    decay_half_life_days: float = Field(default=30.0, gt=0)
    neutral_mastery: float = Field(default=0.50, ge=0, le=1)


class PlannerProposalStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    APPLY_FAILED = "apply_failed"


class ToolPolicy(_FrozenModel):
    max_calls_per_run: int = Field(default=5, ge=0)
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_argument_bytes: int = Field(default=16 * 1024, ge=1)
    max_raw_result_bytes: int = Field(default=256 * 1024, ge=1)
    max_normalized_result_chars: int = Field(default=32_000, ge=1)
    max_result_items: int = Field(default=50, ge=1)


def normalize_chunk_text(text: str) -> str:
    return re.sub(r"[ \t]+$", "", text.replace("\r\n", "\n").replace("\r", "\n"), flags=re.MULTILINE).rstrip()


def content_hash(text: str) -> str:
    return sha256(normalize_chunk_text(text).encode("utf-8")).hexdigest()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def validate_feature_flags(flags: Mapping[str, bool]) -> None:
    if flags.get("FEATURE_GROUNDING_V2", False) and not flags.get("FEATURE_STRUCTURED_ANSWER_V2", False):
        raise ValueError("FEATURE_GROUNDING_V2 requires FEATURE_STRUCTURED_ANSWER_V2")
    if flags.get("FEATURE_EVIDENCE_PIPELINE_V2", False) and not flags.get("FEATURE_STRUCTURED_ANSWER_V2", False):
        raise ValueError("FEATURE_EVIDENCE_PIPELINE_V2 requires FEATURE_STRUCTURED_ANSWER_V2")
    if flags.get("FEATURE_EVIDENCE_PIPELINE_V2", False) and not flags.get("FEATURE_GROUNDING_V2", False):
        raise ValueError("FEATURE_EVIDENCE_PIPELINE_V2 requires FEATURE_GROUNDING_V2")


def feature_flags_from_env(environ: Mapping[str, str]) -> dict[str, bool]:
    names = (
        "FEATURE_STRUCTURED_ANSWER_V2",
        "FEATURE_GROUNDING_V2",
        "FEATURE_ASSESSMENT_INTELLIGENCE_V2",
        "FEATURE_PLANNER_PROPOSAL_V2",
        "FEATURE_MCP_TOOL_ROUTER_V2",
        "FEATURE_AGENT_TOOL_LOOP_V1",
        "FEATURE_EVIDENCE_PIPELINE_V2",
    )
    flags = {name: environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"} for name in names}
    validate_feature_flags(flags)
    return flags
