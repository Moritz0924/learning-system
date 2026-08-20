from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from backend.app.models import ToolCall


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_METADATA_HOSTS = {
    "instance-data",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google",
    "metadata.google.internal",
}
_CONTROLLED_RESULT_KEYS = {
    "title",
    "url",
    "snippet",
    "retrieved_at",
    "source_level",
    "retrieval_mode",
    "is_live_search",
    "trust_label",
}


class LearningSourceSearchUnavailable(RuntimeError):
    pass


def search_learning_sources(
    session: Session,
    *,
    query: str,
    http_client: httpx.Client | None = None,
) -> list[dict]:
    query = _validated_query(query)
    try:
        results = search_learning_sources_raw(query=query, http_client=http_client)
    except LearningSourceSearchUnavailable:
        _record_tool_call(session, query=query, results=[], status="failed")
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        _record_tool_call(session, query=query, results=[], status="failed")
        raise LearningSourceSearchUnavailable("learning source search failed") from exc
    _record_tool_call(session, query=query, results=results, status="success")
    return results


def search_learning_sources_raw(*, query: str, http_client: httpx.Client | None = None) -> list[dict]:
    query = _validated_query(query)
    api_key = _env_value("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise LearningSourceSearchUnavailable("BRAVE_SEARCH_API_KEY is required")

    client = http_client or httpx.Client(timeout=15)
    response = client.get(
        BRAVE_SEARCH_URL,
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        params={"q": query, "count": 10},
    )
    response.raise_for_status()
    payload = response.json()
    web = payload.get("web", {}) if isinstance(payload, Mapping) else None
    items = web.get("results", []) if isinstance(web, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("invalid Brave search response")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url")
        raw_title = item.get("title")
        raw_snippet = item.get("description")
        if (
            not isinstance(raw_url, str)
            or (raw_title is not None and not isinstance(raw_title, str))
            or not isinstance(raw_snippet, str)
        ):
            continue
        url = raw_url.strip()
        title = (raw_title or url).strip()
        snippet = raw_snippet.strip()
        if any(api_key in field for field in (title, url, snippet)):
            continue
        safe_url = url[:2048]
        if not title or not snippet or not is_safe_learning_source_url(safe_url):
            continue
        results.append(
            {
                "title": title[:256],
                "url": safe_url,
                "snippet": snippet[:1000],
                "retrieved_at": retrieved_at,
                "source_level": "web",
                "retrieval_mode": "brave_search",
                "is_live_search": True,
                "trust_label": "external_unverified",
            }
        )
        if len(results) == 5:
            break
    return results


def is_valid_learning_source_result(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _CONTROLLED_RESULT_KEYS:
        return False
    title = value.get("title")
    url = value.get("url")
    snippet = value.get("snippet")
    retrieved_at = value.get("retrieved_at")
    if not all(isinstance(field, str) for field in (title, url, snippet, retrieved_at)):
        return False
    if (
        title != title.strip()
        or not 1 <= len(title) <= 256
        or url != url.strip()
        or not 1 <= len(url) <= 2048
        or snippet != snippet.strip()
        or not 1 <= len(snippet) <= 1000
    ):
        return False
    try:
        timestamp = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return False
    return (
        timestamp.tzinfo is not None
        and value.get("source_level") == "web"
        and value.get("retrieval_mode") == "brave_search"
        and value.get("is_live_search") is True
        and value.get("trust_label") == "external_unverified"
        and is_safe_learning_source_url(url)
    )


def is_safe_learning_source_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if not host or host == "localhost" or host.endswith(".localhost") or host in _METADATA_HOSTS:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _validated_query(query: str) -> str:
    value = (query or "").strip()
    if not 1 <= len(value) <= 512:
        raise ValueError("query must be between 1 and 512 characters")
    return value


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _record_tool_call(session: Session, *, query: str, results: list[dict], status: str) -> None:
    session.add(
        ToolCall(
            id=f"tool-{uuid4()}",
            agent_run_id=None,
            tool_name="search_learning_sources",
            request_hash=sha256(query.encode("utf-8")).hexdigest(),
            response_summary={"result_count": len(results), "source_level": "web"},
            source_urls=[item["url"] for item in results],
            status=status,
        )
    )
    session.commit()
