from __future__ import annotations

import os
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import quote_plus, urlparse
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from backend.app.models import ToolCall


ALLOWED_SOURCE_DOMAINS = {
    "docs.python.org",
    "fastapi.tiangolo.com",
    "docs.langchain.com",
    "python.langchain.com",
    "docs.pydantic.dev",
    "modelcontextprotocol.io",
    "platform.openai.com",
    "docs.openai.com",
    "github.com",
}


class OfficialSourceSearchUnavailable(RuntimeError):
    pass


def search_official_learning_sources(
    session: Session,
    *,
    query: str,
    domains: list[str],
    http_client: httpx.Client | None = None,
) -> list[dict]:
    query = query.strip()
    allowed = [_normalize_domain(domain) for domain in domains]
    if not query:
        _record_tool_call(session, query=query, domains=allowed, results=[], status="rejected")
        raise ValueError("query is required")
    blocked = [domain for domain in allowed if not _is_allowed_domain(domain)]
    if blocked:
        _record_tool_call(session, query=query, domains=allowed, results=[], status="rejected")
        raise ValueError(f"domain not whitelisted: {blocked[0]}")

    try:
        results = search_official_learning_sources_raw(
            query=query,
            domains=allowed,
            http_client=http_client,
        )
    except OfficialSourceSearchUnavailable as exc:
        _record_tool_call(
            session,
            query=query,
            domains=allowed,
            results=[],
            status="failed",
            response_summary={
                "provider": (_env_value("OFFICIAL_SEARCH_PROVIDER") or "url_template").lower(),
                "error_type": type(exc).__name__,
            },
        )
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        _record_tool_call(
            session,
            query=query,
            domains=allowed,
            results=[],
            status="failed",
            response_summary={
                "provider": (_env_value("OFFICIAL_SEARCH_PROVIDER") or "url_template").lower(),
                "error_type": type(exc).__name__,
            },
        )
        raise OfficialSourceSearchUnavailable("official source search failed") from exc
    _record_tool_call(session, query=query, domains=allowed, results=results, status="success")
    return results


def search_official_learning_sources_raw(
    *,
    query: str,
    domains: list[str],
    http_client: httpx.Client | None = None,
) -> list[dict]:
    """Search official sources without owning a database session or transaction."""

    query = query.strip()
    allowed = [_normalize_domain(domain) for domain in domains]
    if not query:
        raise ValueError("query is required")
    blocked = [domain for domain in allowed if not _is_allowed_domain(domain)]
    if blocked:
        raise ValueError(f"domain not whitelisted: {blocked[0]}")

    provider = (_env_value("OFFICIAL_SEARCH_PROVIDER") or "url_template").lower()
    if provider == "brave":
        return _search_with_brave(query=query, domains=allowed, http_client=http_client)
    if provider != "url_template":
        raise OfficialSourceSearchUnavailable(f"unsupported official search provider: {provider}")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    results = [
        {
            "title": f"{query} - {domain}",
            "url": f"https://{domain}/search?q={quote_plus(query)}",
            "snippet": (
                f"Official learning source result for '{query}'. "
                "Treat external content as untrusted until cited and reviewed."
            ),
            "published_at": None,
            "retrieved_at": retrieved_at,
            "source_level": "official",
            "retrieval_mode": "url_template",
            "is_live_search": False,
        }
        for domain in allowed[:5]
    ]
    return results


def _search_with_brave(
    *,
    query: str,
    domains: list[str],
    http_client: httpx.Client | None,
) -> list[dict]:
    api_key = _env_value("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise OfficialSourceSearchUnavailable("BRAVE_SEARCH_API_KEY is required for live official search")
    if not domains:
        return []
    client = http_client or httpx.Client(timeout=15)
    site_query = " OR ".join(f"site:{domain}" for domain in domains)
    search_query = f"{site_query} {query}" if len(domains) == 1 else f"({site_query}) {query}"
    response = client.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        params={"q": search_query, "count": 10},
    )
    response.raise_for_status()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for item in response.json().get("web", {}).get("results", []):
        url = item.get("url") or ""
        if not _is_domain_in_list(_domain_from_url(url), domains):
            continue
        results.append(
            {
                "title": item.get("title") or url,
                "url": url,
                "snippet": item.get("description") or "",
                "published_at": item.get("age"),
                "retrieved_at": retrieved_at,
                "source_level": "official",
                "retrieval_mode": "brave_search",
                "is_live_search": True,
            }
        )
        if len(results) >= 5:
            break
    return results


def _normalize_domain(domain: str) -> str:
    return domain.replace("https://", "").replace("http://", "").strip("/").lower()


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _is_allowed_domain(domain: str) -> bool:
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in ALLOWED_SOURCE_DOMAINS)


def is_allowed_official_source_url(url: str) -> bool:
    value = (url or "").strip()
    return bool(value) and _is_allowed_domain(_domain_from_url(value))


def _domain_from_url(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _is_domain_in_list(domain: str, allowed_domains: list[str]) -> bool:
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains)


def _record_tool_call(
    session: Session,
    *,
    query: str,
    domains: list[str],
    results: list[dict],
    status: str,
    response_summary: dict | None = None,
) -> None:
    summary = {"result_count": len(results), "domains": domains}
    if response_summary:
        summary.update(response_summary)
    session.add(
        ToolCall(
            id=f"tool-{uuid4()}",
            agent_run_id=None,
            tool_name="search_official_learning_sources",
            request_hash=sha256(f"{query}|{','.join(domains)}".encode("utf-8")).hexdigest(),
            response_summary=summary,
            source_urls=[item["url"] for item in results],
            status=status,
        )
    )
    session.commit()
