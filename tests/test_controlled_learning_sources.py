from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from backend.app.models import ToolCall
from backend.app.routers.tools import LearningSourceSearchRequest, search_learning_sources_endpoint
from backend.app.services.learning_sources import (
    LearningSourceSearchUnavailable,
    search_learning_sources,
    search_learning_sources_raw,
)
from backend.app.services.tool_evidence import map_learning_source_search_evidence
from backend.app.services.tutor_tools import build_tutor_tool_router


CONTROLLED_RESULT_KEYS = {
    "title",
    "url",
    "snippet",
    "retrieved_at",
    "source_level",
    "retrieval_mode",
    "is_live_search",
    "trust_label",
}


def test_raw_search_filters_unsafe_results_truncates_fields_and_limits_to_five(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": f"Reflected {request.headers['x-subscription-token']}",
                            "url": "https://reflected-secret.example.test/",
                            "description": "blocked",
                        },
                        {"title": "T" * 300, "url": "https://example.test/" + "a" * 2100, "description": "S" * 1100},
                        {"title": "FTP", "url": "ftp://example.test/file", "description": "blocked"},
                        {"title": "No host", "url": "https:///path", "description": "blocked"},
                        {"title": "Userinfo", "url": "https://user:pass@example.test/private", "description": "blocked"},
                        {"title": "Local", "url": "https://localhost/path", "description": "blocked"},
                        {"title": "Local subdomain", "url": "https://docs.localhost/path", "description": "blocked"},
                        {"title": "Private", "url": "https://10.0.0.7/path", "description": "blocked"},
                        {"title": "Link local", "url": "https://169.254.169.254/path", "description": "blocked"},
                        {"title": "Unspecified", "url": "https://0.0.0.0/path", "description": "blocked"},
                        {"title": "Multicast", "url": "https://224.0.0.1/path", "description": "blocked"},
                        {"title": "Reserved", "url": "https://240.0.0.1/path", "description": "blocked"},
                        {"title": "IPv6 loopback", "url": "https://[::1]/path", "description": "blocked"},
                        {"title": "IPv6 private", "url": "https://[fd00::1]/path", "description": "blocked"},
                        {"title": "Metadata", "url": "https://metadata.google.internal/path", "description": "blocked"},
                        {"title": "AWS metadata", "url": "https://instance-data/path", "description": "blocked"},
                        {"title": "HTTP allowed", "url": "http://public-one.example.test/path", "description": "one"},
                        {"title": "Two", "url": "https://public-two.example.test/path", "description": "two"},
                        {"title": "Three", "url": "https://public-three.example.test/path", "description": "three"},
                        {"title": "Four", "url": "https://public-four.example.test/path", "description": "four"},
                        {"title": "Sixth", "url": "https://public-six.example.test/path", "description": "sixth"},
                    ]
                }
            },
        )

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")

    results = search_learning_sources_raw(
        query="  Python web security  ",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert len(results) == 5
    assert seen["url"] == "https://api.search.brave.com/res/v1/web/search?q=Python+web+security&count=10"
    assert seen["headers"]["x-subscription-token"] == "brave-secret"
    assert len(results[0]["title"]) == 256
    assert len(results[0]["url"]) == 2048
    assert len(results[0]["snippet"]) == 1000
    assert results[1]["url"].startswith("http://")
    assert all(result["source_level"] == "web" for result in results)
    assert all(result["retrieval_mode"] == "brave_search" for result in results)
    assert all(result["is_live_search"] is True for result in results)
    assert all(result["trust_label"] == "external_unverified" for result in results)
    assert all(set(result) == CONTROLLED_RESULT_KEYS for result in results)
    assert "brave-secret" not in str(results)


def test_raw_search_drops_entries_with_non_string_fields(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": {"unexpected": "mapping"},
                            "url": "https://invalid-title.example.test/",
                            "description": "description",
                        },
                        {
                            "title": "Invalid description",
                            "url": "https://invalid-description.example.test/",
                            "description": ["unexpected", "list"],
                        },
                        {
                            "title": "Valid",
                            "url": "https://valid.example.test/",
                            "description": "valid description",
                        },
                    ]
                }
            },
        )

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")

    results = search_learning_sources_raw(
        query="Python web security",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [result["url"] for result in results] == ["https://valid.example.test/"]


def test_search_rejects_missing_key_without_contacting_brave_and_audits_failure(db_session, monkeypatch) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    with pytest.raises(LearningSourceSearchUnavailable):
        search_learning_sources(
            db_session,
            query="Python web security",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    record = db_session.scalar(select(ToolCall).order_by(ToolCall.created_at.desc()))
    assert called is False
    assert record.tool_name == "search_learning_sources"
    assert record.status == "failed"
    assert record.response_summary == {"result_count": 0, "source_level": "web"}
    assert record.source_urls == []


def test_search_audits_only_result_count_source_level_and_safe_urls(db_session, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Safe guide",
                            "url": "https://public.example.test/guide",
                            "description": "Use validated learning material.",
                        }
                    ]
                }
            },
            request=request,
        )

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")
    results = search_learning_sources(
        db_session,
        query="Python web security",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    record = db_session.scalar(select(ToolCall).order_by(ToolCall.created_at.desc()))
    assert record.status == "success"
    assert record.response_summary == {"result_count": 1, "source_level": "web"}
    assert record.source_urls == ["https://public.example.test/guide"]
    assert record.source_urls == [result["url"] for result in results]
    assert "brave-secret" not in str(record.response_summary) + str(record.source_urls)


def test_endpoint_returns_stable_unavailable_error_for_missing_key(db_session, monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    with pytest.raises(HTTPException) as error:
        search_learning_sources_endpoint(
            LearningSourceSearchRequest(query="Python web security"),
            session=db_session,
        )

    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "source_search.unavailable",
        "message": "Online learning source search is unavailable.",
    }


def test_endpoint_returns_stable_unavailable_error_for_upstream_failure(db_session, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream leaked detail", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setattr("backend.app.services.learning_sources.httpx.Client", lambda **_kwargs: client)

    with pytest.raises(HTTPException) as error:
        search_learning_sources_endpoint(
            LearningSourceSearchRequest(query="Python web security"),
            session=db_session,
        )

    record = db_session.scalar(select(ToolCall).order_by(ToolCall.created_at.desc()))
    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "source_search.unavailable",
        "message": "Online learning source search is unavailable.",
    }
    assert record.response_summary == {"result_count": 0, "source_level": "web"}
    assert "brave-secret" not in str(record.response_summary)
    assert "upstream leaked detail" not in str(record.response_summary)


def test_endpoint_returns_stable_unavailable_error_for_malformed_upstream_payload(db_session, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setattr("backend.app.services.learning_sources.httpx.Client", lambda **_kwargs: client)

    with pytest.raises(HTTPException) as error:
        search_learning_sources_endpoint(
            LearningSourceSearchRequest(query="Python web security"),
            session=db_session,
        )

    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "source_search.unavailable",
        "message": "Online learning source search is unavailable.",
    }


@pytest.mark.parametrize("query", ["   ", "x" * 513])
def test_request_rejects_query_outside_trimmed_length_bounds(query: str) -> None:
    with pytest.raises(ValidationError):
        LearningSourceSearchRequest(query=query)


def test_tutor_tool_and_evidence_mapper_accept_only_safe_live_web_results(monkeypatch) -> None:
    def fake_search(*, query: str) -> list[dict]:
        assert query == "Python web security"
        return [
            {
                "title": "Safe guide",
                "url": "https://public.example.test/guide",
                "snippet": "Use validated learning material.",
                "retrieved_at": "2026-08-19T00:00:00+00:00",
                "source_level": "web",
                "retrieval_mode": "brave_search",
                "is_live_search": True,
                "trust_label": "external_unverified",
            }
        ]

    monkeypatch.setattr("backend.app.services.tutor_tools.search_learning_sources_raw", fake_search)
    router = build_tutor_tool_router()
    result = router.execute_agent(
        run_id="run-general-search",
        user_id="user-1",
        tool_name="search_learning_sources",
        arguments={"query": "Python web security"},
    )
    unsafe_or_not_live = [
        result.value[0],
        {**result.value[0], "url": "https://localhost/guide"},
        {**result.value[0], "is_live_search": False},
        {**result.value[0], "source_level": "official"},
        {**result.value[0], "trust_label": "trusted"},
    ]

    mapped = map_learning_source_search_evidence(unsafe_or_not_live, "fingerprint")

    assert router.registry["search_learning_sources"].spec.safety_class == "read_only"
    assert router.registry["search_learning_sources"].spec.input_schema["required"] == ["query"]
    assert set(router.registry["search_learning_sources"].spec.input_schema["properties"]) == {"query"}
    assert router.registry["search_learning_sources"].spec.input_schema["additionalProperties"] is False
    assert len(mapped) == 1
    assert mapped[0].tool_name == "search_learning_sources"
    assert mapped[0].trusted_level == 1
    assert mapped[0].metadata["trust_label"] == "external_unverified"


def test_evidence_mapper_rejects_results_outside_controlled_shape() -> None:
    valid = {
        "title": "Safe guide",
        "url": "https://public.example.test/guide",
        "snippet": "Use validated learning material.",
        "retrieved_at": "2026-08-19T00:00:00+00:00",
        "source_level": "web",
        "retrieval_mode": "brave_search",
        "is_live_search": True,
        "trust_label": "external_unverified",
    }

    mapped = map_learning_source_search_evidence(
        [
            valid,
            {key: value for key, value in valid.items() if key != "retrieved_at"},
            {**valid, "retrieved_at": "not-a-timestamp"},
            {**valid, "title": "   "},
            {**valid, "title": "T" * 257},
            {**valid, "snippet": "S" * 1001},
            {**valid, "url": "https://public.example.test/" + "a" * 2049},
            {**valid, "title": {"unexpected": "mapping"}},
        ],
        "fingerprint",
    )

    assert len(mapped) == 1
    assert mapped[0].source_url == valid["url"]
