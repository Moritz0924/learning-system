"""Planner Proposal lifecycle and deterministic validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PlannerProposalStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    APPLY_FAILED = "apply_failed"


class PlannerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    user_id: str
    goal_id: str
    base_plan_version: int = Field(ge=1)
    created_at: datetime
    expires_at: datetime
    status: PlannerProposalStatus = PlannerProposalStatus.PROPOSED
    risk_level: str
    requires_confirmation: bool
    operations: tuple[dict, ...]
    decision: str | None = None
    rationale: str | None = None
    expected_outcome: str | None = None
    evidence_refs: tuple[str, ...] = ()


ALLOWED_OPERATIONS = frozenset(
    {"load_multiplier", "defer_nonessential", "insert_review", "unlock_next_nodes", "increase_difficulty"}
)


def proposal_expires_at(created_at: datetime) -> datetime:
    return created_at + timedelta(hours=24)


def transition_proposal(status: PlannerProposalStatus, action: str) -> PlannerProposalStatus:
    if status is not PlannerProposalStatus.PROPOSED:
        raise ValueError("terminal proposal cannot transition")
    transitions = {"accept": PlannerProposalStatus.ACCEPTED, "reject": PlannerProposalStatus.REJECTED}
    try:
        return transitions[action]
    except KeyError as exc:
        raise ValueError(f"unsupported proposal action: {action}") from exc


def validate_proposal(
    proposal: PlannerProposal,
    *,
    current_plan_version: int,
    now: datetime,
) -> PlannerProposal:
    if proposal.status is not PlannerProposalStatus.PROPOSED:
        raise ValueError("proposal is not proposed")
    if now >= proposal.expires_at:
        raise ValueError("proposal is expired")
    if proposal.base_plan_version != current_plan_version:
        raise ValueError("proposal plan version is stale")
    for operation in proposal.operations:
        if operation.get("op") not in ALLOWED_OPERATIONS:
            raise ValueError("proposal operation is not allowed")
    return proposal
