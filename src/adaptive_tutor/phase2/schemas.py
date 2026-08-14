from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.channels import UntrackedValue
from pydantic import BaseModel, ConfigDict, Field, field_validator

from adaptive_tutor.tutor.memory import (
    MemoryCandidate,
    MemoryDecision,
    MemoryPrivacySettings,
    MemorySourceKind,
    MemoryType,
)
from adaptive_tutor.tutor.agent_contracts import AgentDecision
from adaptive_tutor.tutor.evidence import EvidenceItem, EvidenceSnapshot
from adaptive_tutor.tutor.models import TutorWorkflowState
from adaptive_tutor.tutor.t3_contracts import PublicCitation


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
WorkflowActionType = Literal[
    "record_agent_run",
    "record_tool_call",
    "save_assessment_draft",
    "save_attempt_result",
    "save_mastery_updates",
    "save_plan_adjustment",
    "refresh_state_snapshot",
    "save_memory",
]


class TutorGoalContext(BaseModel):
    goal_id: str
    title: str
    target_outcome: str
    domain: str
    deadline: date | None
    weekly_hours_target: int


class TutorTaskContext(BaseModel):
    task_id: str
    title: str
    objective: str
    task_type: str
    knowledge_node_id: str
    estimated_minutes: int
    status: str


class TutorMasteryItem(BaseModel):
    knowledge_node_id: str
    score: float
    confidence: float | None = None
    evidence_count: int | None = None


class TutorLearningEvent(BaseModel):
    event_type: str
    source: str
    task_id: str | None
    occurred_at: datetime | None
    details: dict[str, str | int | float | bool | None]


class TutorRagCitation(BaseModel):
    chunk_id: str
    document_id: str
    citation_label: str
    source_title: str | None
    source_url: str | None
    trusted_level: int


class TutorMemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1)
    memory_type: MemoryType
    scope: Literal["user", "goal"]
    content: dict[str, Any]
    importance: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_kind: MemorySourceKind
    expires_at: datetime | None = None


class MemoryContextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[TutorMemoryContext] = Field(default_factory=list)
    selected_memory_ids: list[str] = Field(default_factory=list)
    skipped_by_budget: int = Field(default=0, ge=0)
    policy_version: Literal["memory-context-v1"] = "memory-context-v1"
    serialized_char_count: int = Field(default=2, ge=2)


class TutorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_goal: TutorGoalContext
    current_task: TutorTaskContext | None = None
    mastery_summary: list[TutorMasteryItem] = Field(default_factory=list)
    learning_preferences: dict[str, Any] = Field(default_factory=dict)
    recent_learning_events: list[TutorLearningEvent] = Field(default_factory=list)
    rag_citations: list[TutorRagCitation] = Field(default_factory=list)
    long_term_memories: list[TutorMemoryContext] = Field(default_factory=list)


class TutorState(TypedDict, total=False):
    request: Annotated["TutorRunRequest", UntrackedValue]
    thread_id: Annotated[str, UntrackedValue]
    user_id: Annotated[str, UntrackedValue]
    goal_id: Annotated[str, UntrackedValue]
    trigger_type: Annotated[TriggerType, UntrackedValue]
    user_message: Annotated[str, UntrackedValue]
    prepared_context: Annotated["PreparedTutorContext", UntrackedValue]
    workflow_state: TutorWorkflowState
    tutor_context: Annotated[TutorContext, UntrackedValue]
    state_snapshot: Annotated[dict[str, Any], UntrackedValue]
    route: Annotated[Route, UntrackedValue]
    retrieved_context: Annotated[list["RetrievedChunk"], UntrackedValue]
    citations: Annotated[list["RetrievedChunk"], UntrackedValue]
    retrieval_run_id: Annotated[str, UntrackedValue]
    retrieval_snapshot: Annotated[object, UntrackedValue]
    assessment_draft: Annotated["AssessmentDraft", UntrackedValue]
    assessment_result: Annotated["AssessmentAttemptResult", UntrackedValue]
    mastery_updates: Annotated[list["MasteryUpdate"], UntrackedValue]
    observer_signals: Annotated[dict[str, Any], UntrackedValue]
    observer_decision: Annotated["ObserverDecision", UntrackedValue]
    plan_adjustment: Annotated["PlanAdjustment", UntrackedValue]
    memory_decisions: Annotated[list[MemoryDecision], UntrackedValue]
    workflow_actions: Annotated[list["WorkflowAction"], UntrackedValue]
    final_answer: Annotated[str, UntrackedValue]
    grounding_status: Annotated[str, UntrackedValue]
    insufficient_evidence: Annotated[bool, UntrackedValue]
    missing_information: Annotated[list[str], UntrackedValue]
    public_citations: Annotated[list[PublicCitation], UntrackedValue]
    tool_results: Annotated[list[object], UntrackedValue]
    evidence_items: Annotated[list["EvidenceItem"], UntrackedValue]
    selected_evidence_items: Annotated[list["EvidenceItem"], UntrackedValue]
    evidence_snapshot: Annotated["EvidenceSnapshot", UntrackedValue]
    audit_log: Annotated[list[dict[str, Any]], UntrackedValue]
    agent_decision: Annotated[AgentDecision, UntrackedValue]


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
    skill_ids: list[str] | None = Field(default=None, max_length=20)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=32)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    citation_label: str
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreparedTutorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_snapshot: dict[str, Any]
    tutor_context: TutorContext
    retrieved_context: list[RetrievedChunk] = Field(default_factory=list)
    retrieval_status: Literal["grounded", "no_context", "failed"]
    degraded_reason: str | None = None
    embedding_provider: str
    retrieval_backend: str
    retrieval_run_id: str = ""
    memory_selection: MemoryContextSelection
    memory_privacy_settings: MemoryPrivacySettings = Field(default_factory=MemoryPrivacySettings)


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
    grader_type: Literal["rule", "llm", "objective_rule", "rubric_llm", "code_sandbox"] = "rule"
    confidence: float = Field(default=0.95, ge=0, le=1)
    grader_reason: str
    evidence_json: dict[str, Any] = Field(default_factory=dict)


class AssessmentAttemptResult(BaseModel):
    assessment_id: str
    attempt_id: str
    score: float = Field(ge=0, le=100)
    feedback: str
    status: Literal["in_progress", "graded", "pending_review"] = "graded"
    answers: list[AssessmentAnswerResult]
    submission_id: str | None = None
    payload_hash: str | None = None


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
    status: Literal["proposed", "applied", "rejected", "accepted", "expired", "superseded", "apply_failed"] = "proposed"
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    before_snapshot: dict[str, Any] = Field(default_factory=dict)
    after_snapshot: dict[str, Any] = Field(default_factory=dict)
    plan_patch: dict[str, Any] = Field(default_factory=dict)
    change_summary: dict[str, Any]
    rationale_json: dict[str, Any]
    base_plan_version: int | None = None
    expires_at: datetime | None = None
    risk_level: str = "low"
    requires_confirmation: bool = False
    operations: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowAction(BaseModel):
    action_type: WorkflowActionType
    user_id: str | None = None
    goal_id: str | None = None
    assessment_draft: AssessmentDraft | None = None
    assessment_result: AssessmentAttemptResult | None = None
    mastery_updates: list[MasteryUpdate] = Field(default_factory=list)
    plan_adjustment: PlanAdjustment | None = None
    snapshot_updates: dict[str, Any] = Field(default_factory=dict)
    audit_payload: dict[str, Any] = Field(default_factory=dict)
    memory_decisions: list[MemoryDecision] = Field(default_factory=list)


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
    workflow_actions: list[WorkflowAction] = Field(default_factory=list)
    memory_decisions: list[MemoryDecision] = Field(default_factory=list)
    grounding_status: str | None = None
    insufficient_evidence: bool = False
    missing_information: list[str] = Field(default_factory=list)
    public_citations: list[PublicCitation] = Field(default_factory=list)
