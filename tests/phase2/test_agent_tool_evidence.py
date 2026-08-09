from __future__ import annotations

import json
from typing import Any

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.t3_contracts import GroundingStatus
from backend.app.services.tutor_tools import build_tutor_tool_router


class _ToolGroundedLlm:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.decisions = [
            '{"action":"call_tool","tool_call":{"tool_name":"search_official_learning_sources",'
            '"arguments":{"query":"LangGraph checkpoint","domains":["docs.langchain.com"]}},'
            '"reason_code":"external_information_needed"}',
            '{"action":"answer","reason_code":"tool_result_sufficient"}',
        ]

    def complete(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if kwargs["role"] == "agent_controller":
            return self.decisions.pop(0)
        if kwargs["role"] == "teacher":
            context = kwargs["context"]
            assert context
            assert all(isinstance(item, dict) for item in context)
            assert all("tool_results" not in item for item in context)
            tool_item = next(item for item in context if item["source_type"] == "tool")
            return json.dumps(
                {
                    "answer": "Tool-grounded answer.",
                    "claims": [
                        {
                            "claim_id": "c1",
                            "text": "Tool-grounded answer.",
                            "citation_refs": [{"evidence_id": tool_item["evidence_id"]}],
                        }
                    ],
                    "citations": [{"evidence_id": tool_item["evidence_id"]}],
                    "insufficient_evidence": False,
                    "missing_information": [],
                }
            )
        raise AssertionError(f"unexpected LLM role: {kwargs['role']}")


def test_tool_only_evidence_reaches_teacher_grounding_and_public_citation(monkeypatch) -> None:
    for name in (
        "FEATURE_AGENT_TOOL_LOOP_V1",
        "FEATURE_STRUCTURED_ANSWER_V2",
        "FEATURE_GROUNDING_V2",
        "FEATURE_EVIDENCE_PIPELINE_V2",
    ):
        monkeypatch.setenv(name, "true")

    def fake_search(*, query: str, domains: list[str]) -> list[dict[str, Any]]:
        assert query == "LangGraph checkpoint"
        assert domains == ["docs.langchain.com"]
        return [
            {
                "title": "LangGraph checkpoint docs",
                "url": "https://docs.langchain.com/checkpoint",
                "snippet": "Checkpoints persist graph state.",
                "published_at": None,
                "retrieved_at": "2026-08-09T00:00:00+00:00",
                "retrieval_mode": "brave_search",
                "is_live_search": True,
            }
        ]

    monkeypatch.setattr(
        "backend.app.services.tutor_tools.search_official_learning_sources_raw",
        fake_search,
    )
    dependencies = build_mock_phase2_dependencies()
    llm = _ToolGroundedLlm()
    dependencies.llm_client = llm
    dependencies.tool_router = build_tutor_tool_router()

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="tool-evidence-thread",
            user_message="Find official checkpoint guidance.",
        )
    )

    assert result.final_answer == "Tool-grounded answer."
    assert result.grounding_status == GroundingStatus.SEMANTIC_UNVERIFIED.value
    assert result.insufficient_evidence is False
    assert result.citations == []
    assert len(result.public_citations) == 1
    assert result.public_citations[0].source_type == "tool"
    assert result.public_citations[0].source_url == "https://docs.langchain.com/checkpoint"
    assert sum(call["role"] == "agent_controller" for call in llm.calls) == 2
    assert sum(call["role"] == "teacher" for call in llm.calls) == 1
    tool_audit = next(entry for entry in result.audit_log if entry.get("node") == "tool_router")
    assert tool_audit["evidence_count"] == 1
    grounding_audit = next(entry for entry in result.audit_log if entry.get("node") == "grounding")
    assert grounding_audit["status"] == GroundingStatus.SEMANTIC_UNVERIFIED.value
