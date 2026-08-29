from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictStr, StringConstraints


ShortIdentifier = Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
PreferenceString = Annotated[StrictStr, StringConstraints(max_length=1000)]
PreferenceList = Annotated[list[PreferenceString], Field(min_length=1, max_length=20)]
FinitePreferenceFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]


class LearningPreferenceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    memory_type: Literal["learning_preference"]
    request_id: UUID
    preference_key: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
    preference_value: PreferenceString | StrictBool | FinitePreferenceFloat | PreferenceList


class LongTermGoalDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    memory_type: Literal["long_term_goal"]
    request_id: UUID
    title: Annotated[StrictStr, StringConstraints(min_length=1, max_length=200)]
    target_outcome: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)]
    deadline: date | None = None


MemoryDeclaration = Annotated[
    LearningPreferenceDeclaration | LongTermGoalDeclaration,
    Field(discriminator="memory_type"),
]


class TutorChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal_id: ShortIdentifier
    thread_id: ShortIdentifier
    message: Annotated[StrictStr, StringConstraints(min_length=1, max_length=8192)]
    locale: Literal["zh-CN", "en-US"] = "en-US"
    model_tier: Literal["flash", "pro"] | None = None
    skill_ids: Annotated[list[ShortIdentifier], Field(max_length=20)] | None = None
    memory_declaration: MemoryDeclaration | None = None


class TutorFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    helpful: bool
    citation_correct: bool | None = None
    difficulty_fit: bool | None = None
    reason_code: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
    optional_comment: Annotated[StrictStr, StringConstraints(max_length=2000)] | None = None


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    goal_id: ShortIdentifier
    title: Annotated[StrictStr, StringConstraints(min_length=1, max_length=200)] | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    goal_id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: list[ConversationResponse]


class RunCancellationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str


class ToolApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
