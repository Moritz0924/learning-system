from __future__ import annotations

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.rag import ingest_markdown_document
from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.tool_router import ToolRouter


def test_tool_request_enters_graph_only_when_tool_flag_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_MCP_TOOL_ROUTER_V2", "true")
    calls = []
    dependencies = build_mock_phase2_dependencies()
    dependencies.tool_router = ToolRouter({"search": lambda arguments: calls.append(arguments) or {"items": ["tool result"]}})
    ingest_markdown_document(
        dependencies.rag_repository,
        filename="course.md",
        content="# RAG\nUse retrieved evidence.",
        corpus_type="curated",
    )

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="t3-tool-thread",
            user_message="Search for RAG guidance.",
            metadata={"tool_request": {"tool_name": "search", "arguments": {"q": "rag"}}},
        )
    )

    assert calls == [{"q": "rag"}]
    assert any(entry["node"] == "tool_router" for entry in result.audit_log)
