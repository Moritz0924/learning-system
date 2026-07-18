from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints


AssessmentIdentifier = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=255),
]
AssessmentAnswerText = Annotated[
    StrictStr,
    StringConstraints(max_length=8192),
]


class AssessmentOptionPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str
    label: str


class AssessmentItemPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    knowledge_node_id: str
    question_type: Literal["choice", "explain", "code_reading"]
    prompt: str
    options: list[AssessmentOptionPublic] = Field(default_factory=list)
    difficulty: int


class AssessmentPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str
    assessment_type: Literal["daily", "weekly", "phase"]
    status: Literal["active"]
    scope: dict[str, Any]
    items: list[AssessmentItemPublic]


class PhaseAssessmentPublicResponse(AssessmentPublicResponse):
    phase_assessment_state_id: str
    phase_code: str


class AssessmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: AssessmentIdentifier
    thread_id: AssessmentIdentifier
    assessment_type: Literal["daily", "weekly", "phase"] = "daily"
    knowledge_node_ids: list[AssessmentIdentifier] = Field(
        default_factory=list,
        max_length=100,
    )


class PhaseAssessmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: AssessmentIdentifier
    thread_id: AssessmentIdentifier
    phase_code: AssessmentIdentifier
    knowledge_node_ids: list[AssessmentIdentifier] = Field(
        default_factory=list,
        max_length=100,
    )


class AssessmentSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    answers: dict[AssessmentIdentifier, AssessmentAnswerText] = Field(max_length=100)
