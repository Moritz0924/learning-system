from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_tutor.tutor.agent_contracts import (
    AgentDecision,
    AgentLoopPolicy,
    AgentLoopState,
    AgentToolCall,
    AgentToolObservation,
    ToolEvidence,
    ToolSpec,
)


def test_agent_decision_requires_tool_call_for_call_tool() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(
            action="call_tool",
            reason_code="external_information_needed",
        )


def test_agent_decision_rejects_tool_call_for_answer() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(
            action="answer",
            tool_call=AgentToolCall(tool_name="search"),
            reason_code="context_sufficient",
        )


def test_agent_contracts_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name="search", description="Search", unexpected=True)


def test_agent_loop_policy_enforces_bounded_decision_budget() -> None:
    assert AgentLoopPolicy().max_decisions == 4
    with pytest.raises(ValidationError):
        AgentLoopPolicy(max_decisions=0)
    with pytest.raises(ValidationError):
        AgentLoopPolicy(max_decisions=9)


def test_agent_loop_state_roundtrips_as_json() -> None:
    state = AgentLoopState(
        active=True,
        decision_count=1,
        pending_tool_call=AgentToolCall(
            tool_name="search",
            arguments={"query": "checkpoint"},
        ),
        observations=[
            AgentToolObservation(
                tool_name="search",
                arguments={"query": "checkpoint"},
                fingerprint="abc",
                status="success",
                value={"items": []},
            )
        ],
        last_reason_code="external_information_needed",
    )

    restored = AgentLoopState.model_validate(state.model_dump(mode="json"))

    assert restored == state


def test_tool_evidence_and_observation_are_distinct_contracts() -> None:
    observation = AgentToolObservation(
        tool_name="search",
        arguments={},
        fingerprint="abc",
        status="failed",
        error_code="T3_TOOL_TIMEOUT",
    )
    evidence = ToolEvidence(
        content="A source excerpt",
        citation_label="source-1",
        trusted_level=1,
    )

    assert observation.status == "failed"
    assert evidence.trusted_level == 1
