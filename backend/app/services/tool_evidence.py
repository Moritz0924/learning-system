from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaptive_tutor.tutor.evidence import EvidenceItem, tool_evidence_id
from adaptive_tutor.tutor.t3_contracts import content_hash

from .official_sources import is_allowed_official_source_url
from .learning_sources import is_valid_learning_source_result


OFFICIAL_SEARCH_TRUSTED_LEVEL = 4
LEARNING_SOURCE_SEARCH_TRUSTED_LEVEL = 1


def map_official_search_evidence(value: Any, fingerprint: str) -> tuple[EvidenceItem, ...]:
    if not isinstance(value, list):
        return ()
    mapped: list[EvidenceItem] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("is_live_search") is not True:
            continue
        source_url = str(item.get("url") or "").strip()
        content = str(item.get("snippet") or "").strip()
        if not source_url or not content or not is_allowed_official_source_url(source_url):
            continue
        source_title = str(item.get("title") or source_url).strip()
        item_hash = content_hash(content)
        mapped.append(
            EvidenceItem(
                evidence_id=tool_evidence_id(
                    tool_name="search_official_learning_sources",
                    source_url=source_url,
                    content_hash=item_hash,
                ),
                source_type="tool",
                content=content,
                content_hash=item_hash,
                citation_label=source_title,
                source_title=source_title,
                source_url=source_url,
                trusted_level=OFFICIAL_SEARCH_TRUSTED_LEVEL,
                tool_name="search_official_learning_sources",
                tool_call_fingerprint=fingerprint,
                metadata={
                    key: item[key]
                    for key in ("retrieval_mode", "retrieved_at", "published_at", "is_live_search")
                    if key in item
                },
            )
        )
    return tuple(mapped)


def map_learning_source_search_evidence(value: Any, fingerprint: str) -> tuple[EvidenceItem, ...]:
    if not isinstance(value, list):
        return ()
    mapped: list[EvidenceItem] = []
    for item in value:
        if not is_valid_learning_source_result(item):
            continue
        source_url = item["url"]
        content = item["snippet"]
        source_title = item["title"]
        item_hash = content_hash(content)
        mapped.append(
            EvidenceItem(
                evidence_id=tool_evidence_id(
                    tool_name="search_learning_sources", source_url=source_url, content_hash=item_hash
                ),
                source_type="tool",
                content=content,
                content_hash=item_hash,
                citation_label=source_title,
                source_title=source_title,
                source_url=source_url,
                trusted_level=LEARNING_SOURCE_SEARCH_TRUSTED_LEVEL,
                tool_name="search_learning_sources",
                tool_call_fingerprint=fingerprint,
                metadata={
                    key: item[key]
                    for key in ("retrieval_mode", "retrieved_at", "is_live_search", "trust_label")
                    if key in item
                },
            )
        )
        if len(mapped) == 5:
            break
    return tuple(mapped)
