from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationTurn(_WorkflowModel):
    user_message: str
    assistant_message: str


class ConversationState(_WorkflowModel):
    thread_id: str
    user_id: str
    user_message: str = ""
    conversation_summary: str = ""
    recent_turns: list[ConversationTurn] = Field(default_factory=list)
    last_user_intent: str | None = None
    referenced_entities: list[str] = Field(default_factory=list)


class LearningState(_WorkflowModel):
    goal_id: str
    active_plan: dict[str, Any] = Field(default_factory=dict)
    current_task: dict[str, Any] | None = None
    learning_preferences: dict[str, Any] = Field(default_factory=dict)
    mastery_summary: dict[str, Any] = Field(default_factory=dict)
    recent_learning_events: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceState(_WorkflowModel):
    rewritten_query: str = ""
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieval_scores: dict[str, float] = Field(default_factory=dict)
    selected_context_ids: list[str] = Field(default_factory=list)
    generated_citation_ids: list[str] = Field(default_factory=list)
    grounding_result: dict[str, Any] = Field(default_factory=dict)


class ExecutionState(_WorkflowModel):
    run_id: str
    graph_version: str
    prompt_version: str | None = None
    model: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost: float | None = None
    error_code: str | None = None
    retry_count: int = 0


class TutorWorkflowState(_WorkflowModel):
    conversation: ConversationState
    learning: LearningState
    evidence: EvidenceState
    execution: ExecutionState
