from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.agent_contracts import ToolSpec
from adaptive_tutor.tutor.evidence import EvidenceItem
from adaptive_tutor.tutor.tool_router import RegisteredTool, ToolRouter
from adaptive_tutor.tutor.t3_contracts import Thread3ErrorCode, content_hash


class _SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class _ScriptedLlm:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if kwargs["role"] == "teacher":
            return "teacher answer"
        return self.responses.pop(0)


def _request(*, trigger_type: str = "chat", metadata: dict[str, Any] | None = None) -> TutorRunRequest:
    return TutorRunRequest(
        trigger_type=trigger_type,
        user_id="user-1",
        goal_id="goal-1",
        thread_id="agent-loop-thread",
        user_message="Find official checkpoint guidance.",
        metadata=metadata or {},
    )


def _router(handler, *, evidence_mapper=None):
    return ToolRouter(
        {
            "search": RegisteredTool(
                spec=ToolSpec(
                    name="search",
                    description="Search official sources.",
                    input_schema=_SearchArguments.model_json_schema(),
                    agent_visible=True,
                ),
                handler=handler,
                argument_model=_SearchArguments,
                evidence_mapper=evidence_mapper,
            )
        }
    )


def _test_evidence_mapper(value, fingerprint):
    content = str(value["items"][0])
    return (
        EvidenceItem(
            evidence_id="tool:search:test-evidence",
            source_type="tool",
            content=content,
            content_hash=content_hash(content),
            citation_label="Search result",
            source_title="Search result",
            source_url="https://docs.example.test/page",
            trusted_level=4,
            tool_name="search",
            tool_call_fingerprint=fingerprint,
        ),
    )


def test_agent_loop_calls_one_tool_then_returns_to_teacher(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    calls = []
    dependencies = build_mock_phase2_dependencies()
    dependencies.llm_client = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
        '{"action":"answer","reason_code":"tool_result_sufficient"}',
    )
    dependencies.tool_router = _router(lambda arguments: calls.append(arguments) or {"items": ["result"]})

    result = Phase2TutorEngine(dependencies).run(_request())

    assert result.final_answer == "teacher answer"
    assert calls == [{"query": "checkpoint"}]
    assert result.runtime_metadata["agent"]["enabled"] is True
    assert result.runtime_metadata["agent"]["decision_count"] == 2
    assert result.runtime_metadata["agent"]["tool_call_count"] == 1
    assert [entry["node"] for entry in result.audit_log if entry["node"] in {"agent_decide", "tool_router", "teacher"}] == [
        "agent_decide",
        "tool_router",
        "agent_decide",
        "teacher",
    ]


def test_agent_flag_off_keeps_normal_chat_on_teacher(monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_AGENT_TOOL_LOOP_V1", raising=False)
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{}},"reason_code":"external_information_needed"}'
    )
    dependencies.llm_client = llm
    dependencies.tool_router = _router(lambda _arguments: {"items": ["must not run"]})

    result = Phase2TutorEngine(dependencies).run(_request())

    assert result.final_answer == "teacher answer"
    assert [call["role"] for call in llm.calls] == ["teacher"]


def test_agent_decision_budget_forces_teacher(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    monkeypatch.setenv("AGENT_MAX_DECISIONS", "1")
    calls = []
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}'
    )
    dependencies.llm_client = llm
    dependencies.tool_router = _router(lambda arguments: calls.append(arguments) or {"items": ["result"]})

    result = Phase2TutorEngine(dependencies).run(_request())

    assert calls == [{"query": "checkpoint"}]
    assert [call["role"] for call in llm.calls] == ["agent_controller", "teacher"]
    assert any(
        entry.get("stop_reason") == "budget_exhausted"
        for entry in result.audit_log
        if entry.get("node") == "agent_decide"
    )


def test_recoverable_tool_failure_returns_to_agent_and_then_teacher(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
        '{"action":"answer","reason_code":"tool_failed_fallback"}',
    )
    dependencies.llm_client = llm

    def fail(_arguments):
        raise RuntimeError("upstream unavailable")

    dependencies.tool_router = _router(fail)

    result = Phase2TutorEngine(dependencies).run(_request())

    assert result.final_answer == "teacher answer"
    assert [call["role"] for call in llm.calls] == ["agent_controller", "agent_controller", "teacher"]
    assert any(
        entry.get("status") == "failed" and entry.get("error_code") == "T3_TOOL_EXECUTION_FAILED"
        for entry in result.audit_log
        if entry.get("node") == "tool_router"
    )


def test_duplicate_agent_tool_call_stops_without_second_handler_execution(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    calls = []
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
    )
    dependencies.llm_client = llm
    dependencies.tool_router = _router(lambda arguments: calls.append(arguments) or {"items": ["result"]})

    result = Phase2TutorEngine(dependencies).run(_request())

    assert result.final_answer == "teacher answer"
    assert calls == [{"query": "checkpoint"}]
    assert any(
        entry.get("stop_reason") == "duplicate_tool_call"
        for entry in result.audit_log
        if entry.get("node") == "tool_router"
    )


def test_agent_tool_loop_records_mapped_evidence_count(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    dependencies = build_mock_phase2_dependencies()
    dependencies.llm_client = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
        '{"action":"answer","reason_code":"tool_result_sufficient"}',
    )
    dependencies.tool_router = _router(
        lambda _arguments: {"items": ["mapped result"]},
        evidence_mapper=_test_evidence_mapper,
    )

    result = Phase2TutorEngine(dependencies).run(_request())

    tool_audits = [entry for entry in result.audit_log if entry.get("node") == "tool_router"]
    assert tool_audits[0]["evidence_count"] == 1


def test_evidence_mapper_failure_is_recoverable_in_agent_loop(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_AGENT_TOOL_LOOP_V1", "true")
    dependencies = build_mock_phase2_dependencies()
    llm = _ScriptedLlm(
        '{"action":"call_tool","tool_call":{"tool_name":"search","arguments":{"query":"checkpoint"}},"reason_code":"external_information_needed"}',
        '{"action":"answer","reason_code":"tool_failed_fallback"}',
    )
    dependencies.llm_client = llm
    dependencies.tool_router = _router(
        lambda _arguments: {"items": ["result"]},
        evidence_mapper=lambda _value, _fingerprint: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    result = Phase2TutorEngine(dependencies).run(_request())

    assert result.final_answer == "teacher answer"
    assert [call["role"] for call in llm.calls] == ["agent_controller", "agent_controller", "teacher"]
    assert any(
        entry.get("error_code") == Thread3ErrorCode.TOOL_EVIDENCE_MAPPING_FAILED.value
        and entry.get("status") == "failed"
        for entry in result.audit_log
        if entry.get("node") == "tool_router"
    )
