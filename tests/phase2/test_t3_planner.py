from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adaptive_tutor.phase2.planner_proposals import (
    PlannerProposal,
    PlannerProposalStatus,
    proposal_expires_at,
    transition_proposal,
    validate_proposal,
)
from adaptive_tutor.phase2.replanning import generate_plan_adjustment
from adaptive_tutor.phase2.schemas import ObserverDecision


def _proposal(**overrides) -> PlannerProposal:
    values = {
        "proposal_id": "proposal-1",
        "user_id": "user-1",
        "goal_id": "goal-1",
        "base_plan_version": 2,
        "created_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "status": PlannerProposalStatus.PROPOSED,
        "risk_level": "medium",
        "requires_confirmation": True,
        "operations": ({"op": "load_multiplier", "value": 0.8},),
    }
    values.update(overrides)
    return PlannerProposal.model_validate(values)


def test_proposal_status_has_only_forward_transitions() -> None:
    assert transition_proposal(PlannerProposalStatus.PROPOSED, "accept") is PlannerProposalStatus.ACCEPTED
    assert transition_proposal(PlannerProposalStatus.PROPOSED, "reject") is PlannerProposalStatus.REJECTED
    with pytest.raises(ValueError, match="terminal"):
        transition_proposal(PlannerProposalStatus.ACCEPTED, "accept")


def test_proposal_validator_rejects_stale_plan_version_and_unknown_operation() -> None:
    proposal = _proposal()
    with pytest.raises(ValueError, match="plan version"):
        validate_proposal(proposal, current_plan_version=3, now=proposal.created_at)
    with pytest.raises(ValueError, match="operation"):
        validate_proposal(
            proposal.model_copy(update={"operations": ({"op": "delete_everything"},)}),
            current_plan_version=2,
            now=proposal.created_at,
        )


def test_default_expiry_is_created_at_plus_24_hours() -> None:
    created = datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert proposal_expires_at(created) == created + timedelta(hours=24)


def test_generated_adjustment_carries_versioned_operations() -> None:
    adjustment = generate_plan_adjustment(
        user_id="user-1",
        goal_id="goal-1",
        previous_plan_id="plan-1",
        decision=ObserverDecision(decision="reduce", evidence_json={}, rationale="load"),
        state_snapshot={"active_plan": {"id": "plan-1", "version": 2}},
    )
    assert adjustment.base_plan_version == 2
    assert adjustment.operations
    assert adjustment.requires_confirmation is True
