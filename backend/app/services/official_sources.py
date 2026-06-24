from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.models import ToolCall


ALLOWED_SOURCE_DOMAINS = {
    "docs.python.org",
    "fastapi.tiangolo.com",
    "docs.langchain.com",
    "python.langchain.com",
    "docs.pydantic.dev",
    "modelcontextprotocol.io",
    "github.com",
}


def search_official_learning_sources(
    session: Session,
    *,
    query: str,
    domains: list[str],
) -> list[dict]:
    allowed = [_normalize_domain(domain) for domain in domains]
    blocked = [domain for domain in allowed if not _is_allowed_domain(domain)]
    if blocked:
        _record_tool_call(session, query=query, domains=allowed, results=[], status="rejected")
        raise ValueError(f"domain not whitelisted: {blocked[0]}")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    results = [
        {
            "title": f"{query} - {domain}",
            "url": f"https://{domain}/search?q={query.replace(' ', '+')}",
            "snippet": (
                f"Official learning source result for '{query}'. "
                "Treat external content as untrusted until cited and reviewed."
            ),
            "published_at": None,
            "retrieved_at": retrieved_at,
            "source_level": "official",
        }
        for domain in allowed[:5]
    ]
    _record_tool_call(session, query=query, domains=allowed, results=results, status="success")
    return results


def _normalize_domain(domain: str) -> str:
    return domain.replace("https://", "").replace("http://", "").strip("/").lower()


def _is_allowed_domain(domain: str) -> bool:
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in ALLOWED_SOURCE_DOMAINS)


def _record_tool_call(
    session: Session,
    *,
    query: str,
    domains: list[str],
    results: list[dict],
    status: str,
) -> None:
    session.add(
        ToolCall(
            id=f"tool-{uuid4()}",
            agent_run_id=None,
            tool_name="search_official_learning_sources",
            request_hash=sha256(f"{query}|{','.join(domains)}".encode("utf-8")).hexdigest(),
            response_summary={"result_count": len(results), "domains": domains},
            source_urls=[item["url"] for item in results],
            status=status,
        )
    )
    session.commit()
