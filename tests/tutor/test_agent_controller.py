from __future__ import annotations

from typing import Any

from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.agent_contracts import (
    AgentLoopPolicy,
    AgentLoopState,
    AgentToolObservation,
    ToolSpec,
)
from adaptive_tutor.tutor.agent_controller import (
    AgentControllerService,
    load_agent_loop_state,
    save_agent_loop_state,
)
from adaptive_tutor.tutor.models import ExecutionState
from adaptive_tutor.tutor.state import LegacyTutorStateAdapter


class _ScriptedAgentLlm:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _state():
    request = TutorRunRequest(
        trigger_type="chat",
        user_id="user-1",
        goal_id="goal-1",
        thread_id="thread-1",
        user_message="Find checkpoint guidance.",
    )
    workflow_state = LegacyTutorStateAdapter().ingress(
        {"request": request},
        run_id="run-1",
        graph_version="phase2-v1",
    )
    return {"request": request, "workflow_state": workflow_state}


def _search_spec() -> ToolSpec:
    return ToolSpec(
        name="search",
        description="Search official sources.",
        agent_visible=True,
    )


def test_controller_parses_call_tool_decision_without_executing_tools() -> None:
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedAgentLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}'
    )
    dependencies.llm_client = llm

    decision = AgentControllerService().decide(
        _state(),
        dependencies,
        tools=[_search_spec()],
        policy=AgentLoopPolicy(),
    )

    assert decision.action == "call_tool"
    assert decision.tool_call is not None
    assert decision.tool_call.tool_name == "search"
    assert len(llm.calls) == 1


def test_controller_falls_back_to_teacher_for_invalid_or_unknown_model_output() -> None:
    for response in (
        "not json",
        '{"action":"call_tool","tool_call":{"tool_name":"admin.delete_all","arguments":{}},"reason_code":"external_information_needed"}',
    ):
        dependencies = build_mock_phase2_dependencies()
        dependencies.llm_client = _ScriptedAgentLlm(response)

        decision = AgentControllerService().decide(
            _state(),
            dependencies,
            tools=[_search_spec()],
            policy=AgentLoopPolicy(),
        )

        assert decision.action == "answer"
        assert decision.reason_code == "invalid_model_output"


def test_controller_passes_previous_observations_as_json_context() -> None:
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedAgentLlm(
        '{"action":"answer","reason_code":"tool_result_sufficient"}'
    )
    dependencies.llm_client = llm
    state = _state()
    state["workflow_state"] = save_agent_loop_state(
        state["workflow_state"],
        AgentLoopState(
            observations=[
                AgentToolObservation(
                    tool_name="search",
                    arguments={"query": "checkpoint"},
                    fingerprint="abc",
                    status="success",
                    value={"items": []},
                )
            ]
        ),
    )

    AgentControllerService().decide(
        state,
        dependencies,
        tools=[_search_spec()],
        policy=AgentLoopPolicy(),
    )

    context = llm.calls[0]["context"]
    assert context[-1] == {
        "tool_name": "search",
        "arguments": {"query": "checkpoint"},
        "fingerprint": "abc",
        "status": "success",
        "value": {"items": []},
        "error_code": None,
        "cache_hit": False,
        "truncated": False,
    }


def test_agent_state_roundtrips_and_legacy_execution_state_defaults_empty() -> None:
    workflow_state = _state()["workflow_state"]
    saved = save_agent_loop_state(
        workflow_state,
        AgentLoopState(active=True, decision_count=1),
    )

    restored = load_agent_loop_state(saved)

    assert restored.active is True
    assert restored.decision_count == 1
    assert saved.execution.agent_state["active"] is True


def test_legacy_execution_state_without_agent_state_is_compatible() -> None:
    restored = ExecutionState.model_validate(
        {"run_id": "run-legacy", "graph_version": "phase2-v1"}
    )

    assert restored.agent_state == {}
