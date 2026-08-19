from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.diagnosis.contracts import (
    DiagnosticKnowledgeAnswer,
    DiagnosticOption,
    SelfAssessmentAnswer,
)
from backend.app.schemas import DiagnosisResponse, GoalCreateResponse, StateResponse


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearningPreferencesInput(_StrictRequest):
    explanation_order: list[
        Literal["analogy", "definition", "principle", "engineering"]
    ] = Field(min_length=1, max_length=4)
    preferred_session_minutes: int = Field(ge=15, le=180)
    code_first: bool = False

    @model_validator(mode="after")
    def reject_duplicate_explanation_modes(self) -> "LearningPreferencesInput":
        if len(set(self.explanation_order)) != len(self.explanation_order):
            raise ValueError("explanation_order must not contain duplicates")
        return self


class GoalInitializationInput(_StrictRequest):
    title: str = Field(min_length=2, max_length=120)
    target_outcome: str = Field(min_length=10, max_length=1000)
    deadline: date | None = None
    weekly_hours_target: int = Field(ge=1, le=60)
    learning_preferences: LearningPreferencesInput


class OnboardingInitializeRequest(_StrictRequest):
    request_id: UUID
    template_version: str = Field(min_length=1, max_length=64)
    locale: Literal["zh-CN", "en-US"] = "en-US"
    goal: GoalInitializationInput
    self_assessment_answers: list[SelfAssessmentAnswer] = Field(default_factory=list)
    knowledge_answers: list[DiagnosticKnowledgeAnswer] = Field(min_length=1)


class DynamicDiagnosticDraftRequest(_StrictRequest):
    request_id: UUID
    locale: Literal["zh-CN", "en-US"] = "en-US"
    goal: GoalInitializationInput


class DynamicDiagnosticQuestionResponse(BaseModel):
    question_id: str
    prompt: str
    options: tuple[DiagnosticOption, ...]


class DynamicDiagnosticDraftResponse(BaseModel):
    draft_id: str
    expires_at: str
    title: str
    questions: tuple[DynamicDiagnosticQuestionResponse, ...]


class InitializeFromDraftRequest(_StrictRequest):
    request_id: UUID
    draft_id: str = Field(min_length=1, max_length=128)
    knowledge_answers: list[DiagnosticKnowledgeAnswer] = Field(min_length=1, max_length=5)


class SelfAssessmentDimensionResponse(BaseModel):
    code: str
    title: str
    description: str
    minimum: int
    maximum: int


class DiagnosticQuestionResponse(BaseModel):
    question_id: str
    node_code: str
    question_type: Literal["single_choice"]
    prompt: str
    options: tuple[DiagnosticOption, ...]


class DiagnosticTemplateResponse(BaseModel):
    template_version: str
    domain: str
    title: str
    self_assessment_dimensions: tuple[SelfAssessmentDimensionResponse, ...]
    questions: tuple[DiagnosticQuestionResponse, ...]


class OnboardingInitializeResponse(BaseModel):
    goal: GoalCreateResponse
    diagnosis: DiagnosisResponse
    state: StateResponse
    replayed: bool = False
