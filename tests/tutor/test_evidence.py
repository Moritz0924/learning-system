from __future__ import annotations

import pytest
from types import MappingProxyType

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.tutor.evidence import (
    EvidenceInvariantError,
    EvidenceItem,
    EvidenceSelectionPolicy,
    build_evidence_snapshot,
    evidence_from_retrieved_chunk,
    evidence_to_llm_context,
    merge_evidence_items,
    rag_evidence_id,
    select_evidence_items,
    tool_evidence_id,
)
from adaptive_tutor.tutor.t3_contracts import content_hash


def _chunk(*, content: str = "RAG evidence", chunk_id: str = "chunk-1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        content=content,
        citation_label="course chunk 1",
        source_title="course.md",
        source_url="https://example.test/course",
        trusted_level=3,
        metadata={"source_type": "markdown", "mutable": {"value": 1}},
    )


def _tool_evidence(*, content: str = "Tool evidence", evidence_id: str | None = None) -> EvidenceItem:
    digest_id = evidence_id or tool_evidence_id(
        tool_name="search",
        source_url="https://docs.example.test/page",
        content_hash=content_hash(content),
    )
    return EvidenceItem(
        evidence_id=digest_id,
        source_type="tool",
        content=content,
        content_hash=content_hash(content),
        citation_label="official page",
        source_title="Official page",
        source_url="https://docs.example.test/page",
        trusted_level=4,
        tool_name="search",
        tool_call_fingerprint="fingerprint",
    )


def test_rag_conversion_has_deterministic_identity_and_copied_provenance() -> None:
    first = evidence_from_retrieved_chunk(_chunk())
    second = evidence_from_retrieved_chunk(_chunk())

    assert first.evidence_id == rag_evidence_id(document_id="doc-1", chunk_id="chunk-1")
    assert first.evidence_id == second.evidence_id
    assert first.content_hash == second.content_hash == content_hash("RAG evidence")
    assert first.document_id == "doc-1"
    assert first.chunk_id == "chunk-1"
    assert first.source_title == "course.md"
    assert first.source_url == "https://example.test/course"
    assert first.trusted_level == 3
    first.metadata["mutable"]["value"] = 2
    assert second.metadata["mutable"]["value"] == 1


def test_rag_conversion_copies_nested_immutable_mapping_metadata() -> None:
    chunk = _chunk()
    object.__setattr__(
        chunk,
        "metadata",
        MappingProxyType({"nested": MappingProxyType({"value": 1})}),
    )

    evidence = evidence_from_retrieved_chunk(chunk)

    assert evidence.metadata == {"nested": {"value": 1}}


def test_tool_identity_is_deterministic_for_same_url_and_content() -> None:
    digest = content_hash("Tool evidence")
    assert tool_evidence_id(
        tool_name="search",
        source_url="https://docs.example.test/page",
        content_hash=digest,
    ) == tool_evidence_id(
        tool_name="search",
        source_url="https://docs.example.test/page",
        content_hash=digest,
    )


def test_evidence_rejects_conflicting_provenance() -> None:
    with pytest.raises(ValueError):
        EvidenceItem(
            evidence_id="rag:doc-1:chunk-1",
            source_type="rag",
            content="content",
            content_hash=content_hash("content"),
            citation_label="label",
            trusted_level=3,
            document_id="doc-1",
            chunk_id="chunk-1",
            tool_name="search",
        )

    with pytest.raises(ValueError):
        EvidenceItem(
            evidence_id="tool:search:digest",
            source_type="tool",
            content="content",
            content_hash=content_hash("content"),
            citation_label="label",
            trusted_level=4,
        )


def test_merge_keeps_first_seen_duplicate_and_rejects_hash_conflict() -> None:
    item = _tool_evidence()
    assert merge_evidence_items([item], [item]) == [item]

    conflict = item.model_copy(
        update={"content": "changed", "content_hash": content_hash("changed")}
    )
    with pytest.raises(EvidenceInvariantError):
        merge_evidence_items([item], [conflict])


def test_selection_is_deterministic_and_skips_over_budget_items_without_truncating() -> None:
    items = [
        _tool_evidence(content="o" * 400),
        _tool_evidence(content="t" * 700, evidence_id="tool:search:two"),
        _tool_evidence(content="h" * 400, evidence_id="tool:search:three"),
    ]
    policy = EvidenceSelectionPolicy(max_items=2, max_total_chars=1000)

    first = select_evidence_items(items, policy=policy)
    second = select_evidence_items(items, policy=policy)

    assert first == second
    assert [len(item.content) for item in first.items] == [400, 400]
    assert first.skipped_by_item_budget == 0
    assert first.skipped_by_char_budget == 1


def test_snapshot_and_llm_projection_hide_internal_provenance() -> None:
    evidence = [evidence_from_retrieved_chunk(_chunk()), _tool_evidence()]
    snapshot = build_evidence_snapshot(
        run_id="run-1",
        retrieval_run_id="retrieval-1",
        evidence=evidence,
    )
    projected = evidence_to_llm_context(evidence)

    assert snapshot.run_id == "run-1"
    assert [item.evidence_id for item in snapshot.selected_context] == [item.evidence_id for item in evidence]
    assert projected[0]["evidence_id"] == evidence[0].evidence_id
    assert projected[1]["source_type"] == "tool"
    assert "content_hash" not in projected[0]
    assert "tool_call_fingerprint" not in projected[1]
    assert "document_id" not in projected[0]
