from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
