from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class GoalCreateRequest(BaseModel):
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    title: str
    target_outcome: str
    deadline: date
    weekly_hours_target: int = Field(ge=1, le=80)
    available_slots: dict[str, Any] = Field(default_factory=dict)
    learning_preferences: dict[str, Any] = Field(default_factory=dict)


class GoalCreateResponse(BaseModel):
    user_id: str
    goal_id: str
    status: str


class DiagnosisRequest(BaseModel):
    user_id: str
    goal_id: str
    self_assessment: dict[str, Any] = Field(default_factory=dict)
    submitted_answers: dict[str, Any] = Field(default_factory=dict)


class DiagnosisResponse(BaseModel):
    baseline_diagnostic_id: str
    entry_node_id: str
    entry_node_code: str
    baseline_summary: str
    knowledge_gaps: list[dict[str, Any]]
    initial_mastery: dict[str, Any]
    evidence_json: dict[str, Any]
    active_plan_id: str
    active_plan_version: int


class TaskSummary(BaseModel):
    id: str
    title: str
    objective: str
    task_type: str
    scheduled_date: date
    estimated_minutes: int
    status: str
    knowledge_node_id: str
    knowledge_node_code: str
    knowledge_node_title: str


class StateResponse(BaseModel):
    user_id: str
    goal: dict[str, Any]
    active_plan: dict[str, Any]
    baseline_diagnostic: dict[str, Any]
    mastery_summary: dict[str, Any]
    current_state: dict[str, Any]
    generated_from: dict[str, Any]
    latest_plan_adjustment: dict[str, Any] | None = None
    today_tasks: list[TaskSummary]
    updated_at: datetime


class TodayTasksResponse(BaseModel):
    user_id: str
    goal_id: str
    tasks: list[TaskSummary]
