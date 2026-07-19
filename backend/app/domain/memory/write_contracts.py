from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from .contracts import CreateMemoryCommand


MemoryCandidateOrigin = Literal[
    "explicit_user_statement",
    "system_inference",
    "learning_result",
]
MemoryDecisionKind = Literal["approved", "rejected"]
MemoryWriteStatus = Literal["saved", "reused", "rejected", "conflict"]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MemoryPrivacySettings(_StrictFrozenModel):
    enabled: StrictBool = True
    allow_explicit_user: StrictBool = True
    allow_system_inference: StrictBool = False
    allow_learning_results: StrictBool = True


class MemoryCandidate(_StrictFrozenModel):
    candidate_id: StrictStr = Field(min_length=1, max_length=160)
    origin: MemoryCandidateOrigin
    command: CreateMemoryCommand
    semantic_key: StrictStr = Field(min_length=1, max_length=320)
    policy_version: Literal["memory-gate-v1"] = "memory-gate-v1"


class MemoryDecision(_StrictFrozenModel):
    candidate: MemoryCandidate
    decision: MemoryDecisionKind
    reason_code: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")


class MemoryWriteReceipt(_StrictFrozenModel):
    candidate_id: StrictStr = Field(min_length=1, max_length=160)
    origin: MemoryCandidateOrigin
    status: MemoryWriteStatus
    reason_code: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    memory_id: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_memory_id(self) -> "MemoryWriteReceipt":
        if self.status in {"saved", "reused"} and self.memory_id is None:
            raise ValueError("saved memory receipts require a memory id")
        if self.status == "rejected" and self.memory_id is not None:
            raise ValueError("rejected memory receipts cannot include a memory id")
        return self
