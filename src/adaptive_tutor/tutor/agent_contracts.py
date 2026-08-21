"""Contracts for the bounded Agent-to-Tool decision loop."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    safety_class: Literal["read_only", "proposal_only"] = "read_only"
    agent_visible: bool = False


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["answer", "call_tool"]
    tool_call: AgentToolCall | None = None
    reason_code: Literal[
        "context_sufficient",
        "external_information_needed",
        "tool_result_sufficient",
        "tool_failed_fallback",
        "budget_exhausted",
        "invalid_model_output",
        "duplicate_tool_call",
    ]

    @model_validator(mode="after")
    def validate_action(self) -> AgentDecision:
        if self.action == "call_tool" and self.tool_call is None:
            raise ValueError("call_tool requires tool_call")
        if self.action == "answer" and self.tool_call is not None:
            raise ValueError("answer cannot include tool_call")
        return self


class AgentToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any]
    fingerprint: str
    status: Literal["success", "failed"]
    value: Any | None = None
    error_code: str | None = None
    cache_hit: bool = False
    truncated: bool = False


class ToolEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    citation_label: str
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)


class AgentLoopPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_decisions: int = Field(default=4, ge=1, le=8)


class AgentLoopState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    decision_count: int = Field(default=0, ge=0)
    max_decisions: int = Field(default=4, ge=1, le=8)
    pending_tool_call: AgentToolCall | None = None
    observations: list[AgentToolObservation] = Field(default_factory=list)
    last_reason_code: str | None = None
    stop_reason: str | None = None
