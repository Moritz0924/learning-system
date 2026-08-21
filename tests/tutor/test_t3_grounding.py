from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_tutor.phase2.schemas import RetrievedChunk, TutorRunResult
from adaptive_tutor.tutor.grounding import (
    EvidenceGroundingPipeline,
    GroundingPipeline,
    StructuredAnswerParser,
    build_retrieval_snapshot,
)
from adaptive_tutor.tutor.evidence import (
    EvidenceItem,
    build_evidence_snapshot,
    evidence_from_retrieved_chunk,
    tool_evidence_id,
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


def test_grounding_deduplicates_public_citations() -> None:
    chunk = _chunk()
    snapshot = build_retrieval_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", chunks=[chunk])
    result = GroundingPipeline().evaluate(
        raw='{"answer":"RAG retrieves evidence.","citations":[{"chunk_id":"chunk-1","document_id":"doc-1"},{"chunk_id":"chunk-1","document_id":"doc-1"}]}',
        question="q",
        chunks=[chunk],
        snapshot=snapshot,
    )
    assert [citation.citation_id for citation in result.public_citations] == ["c1"]


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


def _tool_evidence() -> EvidenceItem:
    content = "Official tool evidence."
    return EvidenceItem(
        evidence_id=tool_evidence_id(
            tool_name="search_official_learning_sources",
            source_url="https://docs.langchain.com/checkpoint",
            content_hash=content_hash(content),
        ),
        source_type="tool",
        content=content,
        content_hash=content_hash(content),
        citation_label="LangGraph checkpoint docs",
        source_title="LangGraph checkpoint docs",
        source_url="https://docs.langchain.com/checkpoint",
        trusted_level=4,
        tool_name="search_official_learning_sources",
        tool_call_fingerprint="fingerprint",
    )


def test_evidence_grounding_accepts_valid_rag_evidence_without_semantic_judge() -> None:
    evidence = [evidence_from_retrieved_chunk(_chunk())]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    result = EvidenceGroundingPipeline().evaluate(
        raw=(
            '{"answer":"RAG retrieves evidence.","claims":[{"claim_id":"c1",'
            '"text":"RAG retrieves evidence.","citation_refs":[{"evidence_id":"'
            f'{evidence[0].evidence_id}'
            '"}]}],"citations":[{"evidence_id":"'
            f'{evidence[0].evidence_id}'
            '"}]}'
        ),
        question="q",
        evidence=evidence,
        snapshot=snapshot,
    )
    assert result.status is GroundingStatus.SEMANTIC_UNVERIFIED
    assert result.referenced_evidence == evidence


def test_evidence_grounding_accepts_tool_only_evidence_as_context() -> None:
    evidence = [_tool_evidence()]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    result = EvidenceGroundingPipeline().evaluate(
        raw=(
            '{"answer":"Tool-grounded answer.","claims":[{"claim_id":"c1",'
            '"text":"Tool-grounded answer.","citation_refs":[{"evidence_id":"'
            f'{evidence[0].evidence_id}'
            '"}]}],"citations":[{"evidence_id":"'
            f'{evidence[0].evidence_id}'
            '"}],"insufficient_evidence":false}'
        ),
        question="q",
        evidence=evidence,
        snapshot=snapshot,
    )
    assert result.status is GroundingStatus.SEMANTIC_UNVERIFIED
    assert result.draft is not None and result.draft.insufficient_evidence is False
    assert result.public_citations[0].source_type == "tool"
    assert result.public_citations[0].source_url == evidence[0].source_url


def test_evidence_grounding_rejects_fake_id_after_one_repair() -> None:
    evidence = [_tool_evidence()]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    result = EvidenceGroundingPipeline().evaluate(
        raw='{"answer":"x","citations":[{"evidence_id":"fake"}]}',
        question="q",
        evidence=evidence,
        snapshot=snapshot,
        repair=lambda _prompt: '{"answer":"still invalid","citations":[{"evidence_id":"fake"}]}',
    )
    assert result.status is GroundingStatus.SAFE_REFUSAL
    assert result.repair_count == 1


def test_evidence_grounding_rejects_snapshot_hash_mutation() -> None:
    evidence = [_tool_evidence()]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    mutated = [evidence[0].model_copy(update={"content": "mutated content"})]
    result = EvidenceGroundingPipeline().evaluate(
        raw=f'{{"answer":"x","citations":[{{"evidence_id":"{evidence[0].evidence_id}"}}]}}',
        question="q",
        evidence=mutated,
        snapshot=snapshot,
    )
    assert result.status is GroundingStatus.SAFE_REFUSAL


def test_evidence_grounding_requires_claim_refs_in_top_level_citations() -> None:
    evidence = [_tool_evidence()]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    result = EvidenceGroundingPipeline().evaluate(
        raw=(
            f'{{"answer":"x","claims":[{{"claim_id":"c1","text":"x",'
            f'"citation_refs":[{{"evidence_id":"{evidence[0].evidence_id}"}}]}}],"citations":[]}}'
        ),
        question="q",
        evidence=evidence,
        snapshot=snapshot,
    )
    assert result.status is GroundingStatus.SAFE_REFUSAL


def test_evidence_grounding_deduplicates_public_citations_and_supports_mixed_sources() -> None:
    rag = evidence_from_retrieved_chunk(_chunk())
    tool = _tool_evidence()
    evidence = [rag, tool]
    snapshot = build_evidence_snapshot(run_id="run-1", retrieval_run_id="retrieval-1", evidence=evidence)
    raw = (
        f'{{"answer":"mixed","citations":[{{"evidence_id":"{rag.evidence_id}"}},'
        f'{{"evidence_id":"{rag.evidence_id}"}},{{"evidence_id":"{tool.evidence_id}"}}],'
        f'"claims":[{{"claim_id":"c1","text":"mixed","citation_refs":['
        f'{{"evidence_id":"{rag.evidence_id}"}},{{"evidence_id":"{tool.evidence_id}"}}]}}]}}'
    )
    result = EvidenceGroundingPipeline().evaluate(
        raw=raw,
        question="q",
        evidence=evidence,
        snapshot=snapshot,
    )
    assert result.status is GroundingStatus.SEMANTIC_UNVERIFIED
    assert [citation.citation_id for citation in result.public_citations] == ["c1", "c2"]
    assert {item.source_type for item in result.referenced_evidence} == {"rag", "tool"}
