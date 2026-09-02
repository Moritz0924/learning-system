from __future__ import annotations

from adaptive_tutor.phase2.schemas import RetrievedChunk, TutorRunResult
from adaptive_tutor.tutor.t3_contracts import PublicCitation
from backend.app.application.serialization import _run_result_to_dict
from backend.app.application.tutor_stream_service import public_stream_result


def _chunk(*, document_id: str, chunk_id: str, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        chunk_id=chunk_id,
        content=f"Evidence from {title}.",
        citation_label=f"{title} chunk",
        source_title=title,
        source_url=None,
        trusted_level=3,
        metadata={"source_type": "markdown"},
    )


def _public_for(chunk: RetrievedChunk, *, citation_id: str) -> PublicCitation:
    return PublicCitation(
        citation_id=citation_id,
        title=chunk.source_title,
        source_type="markdown",
        excerpt=chunk.content,
        citation_label=chunk.citation_label,
        source_title=chunk.source_title,
        source_url=chunk.source_url,
    )


def test_public_and_persisted_citations_only_use_this_runs_retrieved_chunks() -> None:
    retrieved = _chunk(document_id="doc-visible", chunk_id="chunk-visible", title="Visible notes")
    rogue = _chunk(document_id="doc-rogue", chunk_id="chunk-rogue", title="Rogue notes")
    online = PublicCitation(
        citation_id="c-online",
        title="Online recommendation",
        source_type="tool",
        excerpt="Browsing result only.",
        citation_label="Online recommendation",
        source_title="Online recommendation",
        source_url="https://example.test/recommendation",
    )
    result = TutorRunResult(
        route="teaching",
        final_answer="Grounded answer",
        retrieved_context=[retrieved],
        citations=[retrieved, rogue],
        public_citations=[online, _public_for(retrieved, citation_id="c-visible")],
        grounding_status="semantic_unverified",
    )

    serialized = _run_result_to_dict(result)
    assert serialized["citations"] == [_public_for(retrieved, citation_id="c-visible").model_dump()]
    assert serialized["public_citations"] == serialized["citations"]
    persisted = public_stream_result(result)
    assert [citation["citation_id"] for citation in persisted["citations"]] == ["c-visible"]
    assert persisted["citations"][0]["source_type"] == "markdown"


def test_citations_are_empty_without_a_matching_retrieved_chunk() -> None:
    chunk = _chunk(document_id="doc-old", chunk_id="chunk-old", title="Old notes")
    result = TutorRunResult(
        route="teaching",
        final_answer="Unsupported answer",
        citations=[chunk],
        public_citations=[_public_for(chunk, citation_id="c-old")],
        grounding_status="semantic_unverified",
    )

    assert _run_result_to_dict(result)["citations"] == []
    assert public_stream_result(result)["citations"] == []
