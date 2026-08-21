from __future__ import annotations

from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.models import ToolCall
from backend.app.services.official_sources import search_official_learning_sources_raw
from backend.app.services.tool_evidence import map_official_search_evidence
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
    assert router.registry["search_official_learning_sources"].evidence_mapper is not None
    assert result.value[0]["source_level"] == "official"
    assert result.evidence_items == ()


def test_official_mapper_rejects_non_live_empty_and_non_allowlisted_results() -> None:
    assert map_official_search_evidence(
        [
            {
                "title": "Template",
                "url": "https://docs.langchain.com/search?q=checkpoint",
                "snippet": "template result",
                "is_live_search": False,
            }
        ],
        "fingerprint",
    ) == ()
    assert map_official_search_evidence(
        [
            {
                "title": "Empty",
                "url": "https://docs.langchain.com/checkpoint",
                "snippet": "",
                "is_live_search": True,
            }
        ],
        "fingerprint",
    ) == ()
    assert map_official_search_evidence(
        [
            {
                "title": "Blocked",
                "url": "https://not-official.example.test/checkpoint",
                "snippet": "live result",
                "is_live_search": True,
            }
        ],
        "fingerprint",
    ) == ()


def test_official_mapper_builds_deterministic_live_tool_evidence() -> None:
    value = [
        {
            "title": "LangGraph checkpoint docs",
            "url": "https://docs.langchain.com/checkpoint",
            "snippet": "Checkpoints persist graph state.",
            "published_at": "2026-08-01",
            "retrieved_at": "2026-08-09T00:00:00+00:00",
            "retrieval_mode": "brave_search",
            "is_live_search": True,
            "trusted_level": 0,
        }
    ]
    first = map_official_search_evidence(value, "fingerprint")
    second = map_official_search_evidence(value, "fingerprint")

    assert first == second
    assert len(first) == 1
    assert first[0].source_type == "tool"
    assert first[0].tool_name == "search_official_learning_sources"
    assert first[0].tool_call_fingerprint == "fingerprint"
    assert first[0].trusted_level == 4
    assert first[0].content == "Checkpoints persist graph state."
    assert first[0].metadata == {
        "retrieval_mode": "brave_search",
        "retrieved_at": "2026-08-09T00:00:00+00:00",
        "published_at": "2026-08-01",
        "is_live_search": True,
    }


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
