from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


TriggerType = Literal[
    "onboarding",
    "chat",
    "task_completed",
    "assessment_due",
    "assessment_submitted",
    "manual_replan",
]
Route = Literal["diagnostic", "teaching", "assessment", "observe", "replan"]
AssessmentType = Literal["daily", "weekly", "phase"]
ObserverAction = Literal["keep", "reduce", "remediate", "advance"]


class TutorState(TypedDict, total=False):
    request: "TutorRunRequest"
    thread_id: str
    user_id: str
    goal_id: str
    trigger_type: TriggerType
    user_message: str
    state_snapshot: dict[str, Any]
    active_plan: dict[str, Any]
    current_task: dict[str, Any] | None
    mastery_snapshot: dict[str, Any]
    recent_learning_events: list[dict[str, Any]]
    route: Route
    retrieved_context: list["RetrievedChunk"]
    citations: list["RetrievedChunk"]
    assessment_draft: "AssessmentDraft"
    assessment_result: "AssessmentAttemptResult"
    mastery_updates: list["MasteryUpdate"]
    observer_signals: dict[str, Any]
    observer_decision: "ObserverDecision"
    plan_adjustment: "PlanAdjustment"
    approved_memories: list[dict[str, Any]]
    final_answer: str
    audit_log: list[dict[str, Any]]


class TutorRunRequest(BaseModel):
    trigger_type: TriggerType
    user_id: str
    goal_id: str
    thread_id: str
    user_message: str = ""
    assessment_type: AssessmentType = "daily"
    assessment_id: str | None = None
    knowledge_node_ids: list[str] = Field(default_factory=list)
    submitted_answers: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    citation_label: str
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssessmentItem(BaseModel):
    item_id: str
    knowledge_node_id: str
    question_type: Literal["choice", "explain", "code_reading"]
    prompt: str
    options_json: dict[str, Any] = Field(default_factory=dict)
    reference_answer: str
    rubric_json: dict[str, Any] = Field(default_factory=dict)
    difficulty: int = Field(ge=1, le=5)
    source_chunk_ids: list[str] = Field(default_factory=list)


class AssessmentDraft(BaseModel):
    assessment_id: str
    assessment_type: AssessmentType
    status: Literal["draft", "active", "submitted", "graded"] = "draft"
    scope: dict[str, Any]
    items: list[AssessmentItem]

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, value: list[AssessmentItem]) -> list[AssessmentItem]:
        if not value:
            raise ValueError("assessment draft must include items")
        return value


class AssessmentAnswerResult(BaseModel):
    item_id: str
    answer_text: str
    score: float = Field(ge=0, le=100)
    grader_type: Literal["rule", "llm"] = "rule"
    grader_reason: str
    evidence_json: dict[str, Any] = Field(default_factory=dict)


class AssessmentAttemptResult(BaseModel):
    assessment_id: str
    attempt_id: str
    score: float = Field(ge=0, le=100)
    feedback: str
    status: Literal["in_progress", "graded"] = "graded"
    answers: list[AssessmentAnswerResult]


class MasteryUpdate(BaseModel):
    knowledge_node_id: str
    previous_score: float = Field(ge=0, le=100)
    new_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    calculation_version: str
    source_breakdown: dict[str, Any]
    missing_data_strategy: dict[str, Any]


class ObserverDecision(BaseModel):
    decision: ObserverAction
    evidence_json: dict[str, Any]
    rationale: str


class PlanAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adjustment_id: str | None = None
    user_id: str | None = None
    goal_id: str | None = None
    previous_plan_id: str | None = None
    new_plan_id: str | None = None
    trigger_type: str
    decision: ObserverAction
    status: Literal["proposed", "applied", "rejected"] = "proposed"
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    before_snapshot: dict[str, Any] = Field(default_factory=dict)
    after_snapshot: dict[str, Any] = Field(default_factory=dict)
    plan_patch: dict[str, Any] = Field(default_factory=dict)
    change_summary: dict[str, Any]
    rationale_json: dict[str, Any]


class TutorRunResult(BaseModel):
    route: Route
    final_answer: str = ""
    citations: list[RetrievedChunk] = Field(default_factory=list)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    assessment_draft: AssessmentDraft | None = None
    assessment_result: AssessmentAttemptResult | None = None
    mastery_updates: list[MasteryUpdate] = Field(default_factory=list)
    observer_decision: ObserverDecision | None = None
    plan_adjustment: PlanAdjustment | None = None
    audit_log: list[dict[str, Any]] = Field(default_factory=list)
