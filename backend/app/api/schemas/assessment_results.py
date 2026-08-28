from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicGradingMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["deterministic_exact", "remote_structured", "deterministic_fallback", "manual_review_required"]
    grader_version: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_review: bool
    automatic_mastery_eligible: bool


class AssessmentAnswerPublicResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    score: float | None = Field(default=None, ge=0, le=100)
    feedback: str
    wrong_reason_tags: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_review: bool


class MasteryUpdatePublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    previous_score: float = Field(ge=0, le=100)
    new_score: float = Field(ge=0, le=100)
    new_confidence: float = Field(ge=0, le=1)
    automatic_adjustment_eligible: bool
    reason_codes: list[str] = Field(default_factory=list)


class ObserverDecisionPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str
    decision: Literal["keep", "reduce", "remediate", "advance", "manual_review"]
    automation_allowed: bool
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    user_facing_rationale: str


class PlanAdjustmentPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjustment_id: str
    decision: str
    status: Literal["proposed"]
    automation_allowed: bool
    change_summary: dict[str, Any]
    rationale: str


class AssessmentSubmissionPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str
    attempt_id: str
    status: Literal["graded", "review_required"]
    score: float | None = Field(default=None, ge=0, le=100)
    feedback: str
    grading: PublicGradingMetadata
    answers: list[AssessmentAnswerPublicResult]
    mastery_updates: list[MasteryUpdatePublic]
    observer_decision: ObserverDecisionPublic
    plan_adjustment: PlanAdjustmentPublic | None = None
