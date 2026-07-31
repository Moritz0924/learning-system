from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_tutor.phase2.schemas import RetrievedChunk, TutorRunResult
from adaptive_tutor.tutor.grounding import (
    GroundingPipeline,
    StructuredAnswerParser,
    build_retrieval_snapshot,
)
from adaptive_tutor.tutor.t3_contracts import GroundingStatus, content_hash


def _chunk(chunk_id: str = "chunk-1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        content="RAG retrieves evidence before generation.",
        citation_label="course chunk 1",
        source_title="course.md",
        metadata={"source_type": "markdown", "index_version_id": "index-1"},
        trusted_level=3,
    )


def test_structured_answer_parser_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StructuredAnswerParser().parse('{"answer":"ok","unknown":true}')


def test_pipeline_repairs_once_then_marks_judge_unavailable() -> None:
    snapshot = build_retrieval_snapshot(
        run_id="run-1",
        retrieval_run_id="retrieval-1",
        chunks=[_chunk()],
    )
    calls = []
    result = GroundingPipeline().evaluate(
        raw='{"answer":"broken","citations":[{"chunk_id":"external","document_id":"doc-1"}]}',
        question="How does RAG work?",
        chunks=[_chunk()],
        snapshot=snapshot,
        repair=lambda prompt: calls.append(prompt) or '{"answer":"RAG retrieves evidence.","claims":[{"claim_id":"c1","text":"RAG retrieves evidence.","citation_refs":[{"chunk_id":"chunk-1","document_id":"doc-1"}]}],"citations":[{"chunk_id":"chunk-1","document_id":"doc-1"}]}' ,
    )
    assert result.status is GroundingStatus.SEMANTIC_UNVERIFIED
    assert result.repair_count == 1
    assert len(calls) == 1
    assert [citation.citation_id for citation in result.public_citations] == ["c1"]


def test_pipeline_rejects_unrepairable_citation_and_no_context_is_insufficient() -> None:
    snapshot = build_retrieval_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", chunks=[_chunk()])
    refused = GroundingPipeline().evaluate(
        raw='{"answer":"x","claims":[{"claim_id":"c1","text":"x","citation_refs":[{"chunk_id":"nope","document_id":"doc-1"}]}],"citations":[]}',
        question="q",
        chunks=[_chunk()],
        snapshot=snapshot,
        repair=lambda _prompt: '{"answer":"still invalid","claims":[{"claim_id":"c1","text":"x","citation_refs":[{"chunk_id":"nope","document_id":"doc-1"}]}],"citations":[]}',
    )
    assert refused.status is GroundingStatus.SAFE_REFUSAL
    empty = GroundingPipeline().evaluate(
        raw='{"answer":"not enough","insufficient_evidence":true,"missing_information":["source"]}',
        question="q",
        chunks=[],
        snapshot=build_retrieval_snapshot(run_id="run-2", retrieval_run_id="retrieval-2", chunks=[]),
    )
    assert empty.status is GroundingStatus.INSUFFICIENT_EVIDENCE
    assert empty.public_citations == []


def test_snapshot_uses_normalized_content_hash_and_public_citation_hides_internal_ids() -> None:
    chunk = _chunk()
    snapshot = build_retrieval_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", chunks=[chunk])
    assert snapshot.selected_context[0].content_hash == content_hash(chunk.content)
    result = GroundingPipeline().evaluate(
        raw='{"answer":"RAG retrieves evidence.","citations":[{"chunk_id":"chunk-1","document_id":"doc-1"}]}',
        question="q",
        chunks=[chunk],
        snapshot=snapshot,
    )
    assert "chunk_id" not in result.public_citations[0].model_dump()


def test_tutor_run_result_carries_grounding_contract_without_removing_legacy_fields() -> None:
    result = TutorRunResult(
        route="teaching",
        final_answer="answer",
        grounding_status=GroundingStatus.SEMANTIC_UNVERIFIED.value,
        insufficient_evidence=False,
        missing_information=[],
        public_citations=[],
    )
    assert result.final_answer == "answer"
    assert result.grounding_status == GroundingStatus.SEMANTIC_UNVERIFIED.value
