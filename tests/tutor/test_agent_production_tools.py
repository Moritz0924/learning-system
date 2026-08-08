from __future__ import annotations

from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.models import ToolCall
from backend.app.services.official_sources import search_official_learning_sources_raw
from backend.app.services.tutor_tools import build_tutor_tool_router


def test_raw_official_search_has_no_session_dependency(monkeypatch) -> None:
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "url_template")

    results = search_official_learning_sources_raw(
        query="LangGraph checkpoint",
        domains=["docs.langchain.com"],
    )

    assert results[0]["url"].startswith("https://docs.langchain.com/search?")
    assert results[0]["is_live_search"] is False


def test_production_agent_registry_exposes_only_read_only_official_search(monkeypatch) -> None:
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "url_template")
    router = build_tutor_tool_router()

    specs = router.list_agent_tools()
    result = router.execute_agent(
        run_id="run-production-tool",
        user_id="user-1",
        tool_name="search_official_learning_sources",
        arguments={
            "query": "LangGraph checkpoint",
            "domains": ["docs.langchain.com"],
        },
    )

    assert [spec.name for spec in specs] == ["search_official_learning_sources"]
    assert result.value[0]["source_level"] == "official"


def test_audit_sink_persists_tool_result_metadata(db_session) -> None:
    SQLAlchemyAuditSink(db_session).record_tool_call(
        {
            "tool_name": "search",
            "request_hash": "request-hash",
            "status": "success",
            "cache_hit": True,
            "truncated": True,
            "error_code": None,
        }
    )
    db_session.commit()

    record = db_session.query(ToolCall).one()

    assert record.cache_hit is True
    assert record.truncated is True
    assert record.error_code is None
