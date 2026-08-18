from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


StrictModelConfig = ConfigDict(extra="forbid", allow_inf_nan=False)
FrozenStrictModelConfig = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

AssessmentType = Literal["daily", "weekly", "phase"]
AssessmentQuestionType = Literal["choice", "explain", "code_reading", "scenario"]
AssessmentTargetSkill = Literal["recall", "explain", "apply", "debug", "design"]
GenerationMode = Literal["remote", "offline", "degraded", "invalid"]
GradingMode = Literal[
    "deterministic_exact",
    "remote_structured",
    "deterministic_fallback",
    "manual_review_required",
]


class AssessmentGoalContext(BaseModel):
    model_config = FrozenStrictModelConfig

    title: str = Field(min_length=1, max_length=500)
    target_outcome: str = Field(min_length=1, max_length=4000)


class AssessmentTaskContext(BaseModel):
    model_config = FrozenStrictModelConfig

    task_id: str
    title: str
    objective: str
    knowledge_node_ids: list[str] = Field(default_factory=list)


class AssessmentKnowledgeNodeContext(BaseModel):
    model_config = FrozenStrictModelConfig

    knowledge_node_id: str
    code: str
    title: str
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=5)
    mastery_threshold: float = Field(ge=0, le=100)
    common_misconceptions: list[str] = Field(default_factory=list)


class AssessmentMasteryContext(BaseModel):
    model_config = FrozenStrictModelConfig

    knowledge_node_id: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    last_evidence_at: datetime | None = None


class RecentAttemptSummary(BaseModel):
    model_config = FrozenStrictModelConfig

    assessment_id: str
    score: float | None = Field(default=None, ge=0, le=100)
    status: str
    completed_at: datetime | None = None


class AssessmentSourceExcerpt(BaseModel):
    model_config = FrozenStrictModelConfig

    chunk_id: str
    document_id: str
    citation_label: str
    content: str = Field(max_length=1500)
    trusted_level: int
    untrusted_input: bool = True


class AssessmentGenerationPolicy(BaseModel):
    model_config = FrozenStrictModelConfig

    policy_version: Literal["assessment-generation-policy-v2"] = "assessment-generation-policy-v2"
    max_context_chars: int = Field(default=16000, ge=1)
    max_source_excerpts: int = Field(default=8, ge=0)


class AssessmentGenerationContextV2(BaseModel):
    model_config = FrozenStrictModelConfig

    schema_version: Literal["assessment-generation-context-v2"]
    user_id: str
    goal_id: str
    assessment_type: AssessmentType
    requested_item_count: int = Field(ge=1, le=100)
    requested_knowledge_node_ids: list[str] = Field(min_length=1, max_length=100)
    goal: AssessmentGoalContext
    current_task: AssessmentTaskContext | None
    knowledge_nodes: list[AssessmentKnowledgeNodeContext] = Field(min_length=1)
    mastery: list[AssessmentMasteryContext] = Field(default_factory=list)
    recent_misconceptions: list[str] = Field(default_factory=list)
    recent_attempt_summaries: list[RecentAttemptSummary] = Field(default_factory=list)
    source_excerpts: list[AssessmentSourceExcerpt] = Field(default_factory=list)
    generation_policy: AssessmentGenerationPolicy
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GeneratedOptionV2(BaseModel):
    model_config = StrictModelConfig

    option_key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=2000)


class RubricCriterionV2(BaseModel):
    model_config = StrictModelConfig

    criterion_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    max_points: int = Field(ge=1, le=100)
    required_evidence: list[str] = Field(default_factory=list)
    accepted_concepts: list[str] = Field(default_factory=list)
    common_error_tags: list[str] = Field(default_factory=list)
    deterministic_signals: list[str] = Field(default_factory=list)


class GeneratedAssessmentItemV2(BaseModel):
    model_config = StrictModelConfig

    item_key: str = Field(min_length=1, max_length=128)
    knowledge_node_id: str
    question_type: AssessmentQuestionType
    target_skill: AssessmentTargetSkill
    prompt: str = Field(min_length=10, max_length=4000)
    options: list[GeneratedOptionV2] = Field(default_factory=list)
    reference_answer: str = Field(min_length=1, max_length=8000)
    rubric: list[RubricCriterionV2] = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    source_chunk_ids: list[str] = Field(default_factory=list)


class AssessmentGenerationBundleV2(BaseModel):
    model_config = StrictModelConfig

    schema_version: Literal["assessment-generation-v2"]
    generator_version: Literal["assessment-generator-v2"]
    items: list[GeneratedAssessmentItemV2] = Field(min_length=1)


class AssessmentItemForGrading(BaseModel):
    model_config = FrozenStrictModelConfig

    item_id: str
    knowledge_node_id: str
    question_type: AssessmentQuestionType
    prompt: str
    options: list[GeneratedOptionV2] = Field(default_factory=list)
    reference_answer: str
    rubric: list[RubricCriterionV2]
    difficulty: int = Field(ge=1, le=5)


class AssessmentGradingContextV2(BaseModel):
    model_config = FrozenStrictModelConfig

    schema_version: Literal["assessment-grading-context-v2"]
    assessment_id: str
    attempt_id: str
    assessment_type: AssessmentType
    items: list[AssessmentItemForGrading] = Field(min_length=1)
    submitted_answers: dict[str, str]
    grading_policy_version: Literal["assessment-grading-policy-v2"]
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CriterionGradeV2(BaseModel):
    model_config = StrictModelConfig

    criterion_id: str
    points_awarded: int = Field(ge=0, le=100)
    evidence_quote: str = Field(max_length=500)
    reason_code: Literal["satisfied", "partially_satisfied", "missing", "incorrect", "contradictory"]
    feedback: str = Field(max_length=1000)


class ItemGradeV2(BaseModel):
    model_config = StrictModelConfig

    item_id: str
    criterion_grades: list[CriterionGradeV2]
    wrong_reason_tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool
    feedback: str = Field(max_length=2000)


class AssessmentGradeBundleV2(BaseModel):
    model_config = StrictModelConfig

    schema_version: Literal["assessment-grade-v2"]
    grader_version: Literal["assessment-grader-v2"]
    item_grades: list[ItemGradeV2]
    overall_feedback: str = Field(max_length=4000)


class OpenAnswerGradeBundleV2(BaseModel):
    model_config = StrictModelConfig

    schema_version: Literal["assessment-open-grade-v2"]
    grader_version: Literal["assessment-grader-v2"]
    item_grades: list[ItemGradeV2]
    overall_feedback: str = Field(max_length=4000)


class MasteryEvidenceV2(BaseModel):
    model_config = FrozenStrictModelConfig

    knowledge_node_id: str
    assessment_id: str
    attempt_id: str
    item_id: str
    question_type: AssessmentQuestionType
    score: float = Field(ge=0, le=100)
    grader_confidence: float = Field(ge=0, le=1)
    grading_mode: GradingMode
    reliability_weight: float = Field(ge=0, le=2)
    eligible_for_mastery: bool
    wrong_reason_tags: list[str] = Field(default_factory=list)
    occurred_at: datetime


class MasteryUpdateV2(BaseModel):
    model_config = StrictModelConfig

    knowledge_node_id: str
    previous_score: float = Field(ge=0, le=100)
    evidence_score: float | None = Field(default=None, ge=0, le=100)
    new_score: float = Field(ge=0, le=100)
    previous_confidence: float = Field(ge=0, le=1)
    new_confidence: float = Field(ge=0, le=1)
    accepted_evidence_count: int = Field(ge=0)
    rejected_evidence_count: int = Field(ge=0)
    total_evidence_weight: float = Field(ge=0)
    automatic_adjustment_eligible: bool
    calculation_version: Literal["mastery-v2"] = "mastery-v2"
    source_breakdown: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)


class ObserverSignalBundleV2(BaseModel):
    model_config = FrozenStrictModelConfig

    phase_status: str | None = None
    readiness_score: float | None = Field(default=None, ge=0, le=100)
    mastery_score: float | None = Field(default=None, ge=0, le=100)
    mastery_confidence: float = Field(ge=0, le=1)
    completion_rate_7d: float | None = Field(default=None, ge=0, le=1)
    recent_task_count: int = Field(ge=0)
    low_prerequisite_count: int = Field(ge=0)
    valid_sessions: int = Field(ge=0)
    repeated_misconceptions: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    has_reliable_evidence: bool = False
    automatic_adjustment_eligible: bool = False


class ObserverDecisionV2(BaseModel):
    model_config = StrictModelConfig

    policy_version: Literal["observer-policy-v2"] = "observer-policy-v2"
    decision: Literal["keep", "reduce", "remediate", "advance", "manual_review"]
    automation_allowed: bool
    confidence: float = Field(ge=0, le=1)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    user_facing_rationale: str


class PlanProposalV2(BaseModel):
    model_config = StrictModelConfig

    decision: Literal["keep", "reduce", "remediate", "advance", "manual_review"]
    policy_version: Literal["observer-policy-v2"] = "observer-policy-v2"
    automation_allowed: bool
    plan_patch: dict[str, Any]
    change_summary: dict[str, Any]
    rationale_json: dict[str, Any]


T = TypeVar("T", bound=BaseModel)


class StructuredOutputResult(BaseModel, Generic[T]):
    model_config = StrictModelConfig

    value: T | None = None
    mode: GenerationMode
    model: str
    retry_count: int = Field(ge=0)
    repair_count: int = Field(ge=0)
    error_code: str | None = None
