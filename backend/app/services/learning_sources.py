from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from adaptive_tutor.tutor.tool_router import sanitize_untrusted_tool_text
from backend.app.models import ToolCall


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_METADATA_HOSTS = {
    "instance-data",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google",
    "metadata.google.internal",
}
_METADATA_ADDRESSES = {ipaddress.ip_address("100.100.100.200")}
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
    unavailable = False
    try:
        results = search_learning_sources_raw(query=query, http_client=http_client)
    except (LearningSourceSearchUnavailable, httpx.HTTPError, KeyError, TypeError, ValueError):
        unavailable = True
    if unavailable:
        record_learning_source_tool_call(session, query=query, results=[], status="failed")
        del http_client
        raise LearningSourceSearchUnavailable("learning source search failed") from None
    record_learning_source_tool_call(session, query=query, results=results, status="success")
    return results


def search_learning_sources_raw(*, query: str, http_client: httpx.Client | None = None) -> list[dict]:
    query = _validated_query(query)
    results = _request_learning_sources(query=query, http_client=http_client)
    if results is None:
        del query, http_client, results
        raise LearningSourceSearchUnavailable("learning source search failed") from None
    return results


def _request_learning_sources(*, query: str, http_client: httpx.Client | None) -> list[dict] | None:
    api_key = _env_value("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return None

    owned_client = http_client is None
    client = None
    failed = False
    results: list[dict] | None = None
    try:
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
        if isinstance(items, list):
            results = _controlled_learning_source_results(items, api_key)
        else:
            failed = True
    except Exception:
        failed = True
    finally:
        if owned_client and client is not None:
            try:
                client.close()
            except Exception:
                failed = True
    return None if failed else results


def _controlled_learning_source_results(items: list[object], api_key: str) -> list[dict]:
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
        if sanitize_untrusted_tool_text(url) != url:
            continue
        title = sanitize_untrusted_tool_text(title)[:256].strip()
        snippet = sanitize_untrusted_tool_text(snippet)[:1000].strip()
        safe_url = url[:2048]
        if not title or not snippet or not is_safe_learning_source_url(safe_url):
            continue
        results.append(
            {
                "title": title,
                "url": safe_url,
                "snippet": snippet,
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
        _ = parsed.port
        host = _canonical_host(parsed.hostname or "")
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or "@" in parsed.netloc:
        return False
    if not host or host == "localhost" or host.endswith(".localhost") or host in _METADATA_HOSTS:
        return False
    try:
        address = _browser_ipv4_address(host) or ipaddress.ip_address(host)
    except ValueError:
        return True
    return address not in _METADATA_ADDRESSES and not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _canonical_host(host: str) -> str:
    decoded = unquote(host).rstrip(".").lower()
    try:
        canonical = decoded.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError as exc:
        raise ValueError("invalid host") from exc
    if not canonical or any(character.isspace() or character in "%/\\@#?[]" for character in canonical):
        raise ValueError("invalid host")
    return canonical


def _browser_ipv4_address(host: str) -> ipaddress.IPv4Address | None:
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        radix = 10
        digits = part
        if part.lower().startswith("0x"):
            radix = 16
            digits = part[2:]
        elif len(part) > 1 and part.startswith("0"):
            radix = 8
            digits = part[1:]
        if not digits:
            numbers.append(0)
            continue
        try:
            numbers.append(int(digits, radix))
        except ValueError:
            return None
    if any(number > 255 for number in numbers[:-1]) or numbers[-1] >= 256 ** (5 - len(numbers)):
        raise ValueError("invalid IPv4 address")
    value = numbers[-1]
    for index, number in enumerate(numbers[:-1]):
        value += number << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _validated_query(query: str) -> str:
    value = (query or "").strip()
    if not 1 <= len(value) <= 512:
        raise ValueError("query must be between 1 and 512 characters")
    return value


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def record_learning_source_tool_call(
    session: Session,
    *,
    query: str,
    results: list[dict],
    status: str,
) -> None:
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
