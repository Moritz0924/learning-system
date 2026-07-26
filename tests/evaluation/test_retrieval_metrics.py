from __future__ import annotations

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.phase2.telemetry import RetrievalScore, TimedRetrievalResult
from evals.models import GoldChunkMapCase, GoldEvidenceGroup, LearningQaEvaluationCase


def _case(*, answerable: bool = True) -> LearningQaEvaluationCase:
    return LearningQaEvaluationCase.model_validate({
        "case_id": "case-1",
        "dataset_version": "v1",
        "split": "development",
        "category": "multi_evidence" if answerable else "unanswerable",
        "difficulty": "hard",
        "question": "q",
        "conversation_history": [],
        "gold_answer_points": ["a"] if answerable else [],
        "gold_document_ids": ["doc-a", "doc-b"] if answerable else [],
        "gold_evidence_spans": ([
            {"evidence_id": "g1", "document_id": "doc-a", "text": "a"},
            {"evidence_id": "g2", "document_id": "doc-b", "text": "b"},
        ] if answerable else []),
        "acceptable_alternative_document_ids": [],
        "is_answerable": answerable,
        "expected_behavior": "answer_with_citation" if answerable else "abstain",
        "format_contract": {"type": "strict_json", "require_citations": answerable},
        "tags": [],
    })


def _trace(ids: list[tuple[str, str]]) -> TimedRetrievalResult:
    chunks = [
        RetrievedChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            content="evidence",
            citation_label="source",
            trusted_level=3,
        )
        for chunk_id, document_id in ids
    ]
    return TimedRetrievalResult(
        chunks=chunks,
        scores=[RetrievalScore(raw_value=1 - index / 10, score_kind="cosine_similarity", higher_is_better=True) for index in range(len(chunks))],
        embedding_latency_ms=1,
        vector_search_latency_ms=2,
        postprocess_latency_ms=1,
        total_latency_ms=4,
        backend="local_json_embedding",
        top_k=max(1, len(chunks)),
        status="grounded" if chunks else "no_context",
    )


def test_evidence_group_metrics_match_specification() -> None:
    from evals.graders.retrieval_grader import grade_retrieval

    gold = GoldChunkMapCase(evidence_groups=[
        GoldEvidenceGroup(evidence_id="g1", document_id="doc-a", acceptable_chunk_ids={"A"}),
        GoldEvidenceGroup(evidence_id="g2", document_id="doc-b", acceptable_chunk_ids={"B", "C"}),
    ])

    result = grade_retrieval(
        _case(),
        gold,
        _trace([("A", "doc-a"), ("D", "other"), ("B", "doc-b")]),
        cutoffs=[1, 3],
    )

    assert result.chunk_hit_at == {1: 1.0, 3: 1.0}
    assert result.evidence_recall_at == {1: 0.5, 3: 1.0}
    assert result.all_evidence_hit_at == {1: 0.0, 3: 1.0}
    assert result.document_hit_at == {1: 1.0, 3: 1.0}
    assert result.document_recall_at == {1: 0.5, 3: 1.0}


def test_duplicate_retrievals_do_not_satisfy_multiple_evidence_groups() -> None:
    from evals.graders.retrieval_grader import grade_retrieval

    gold = GoldChunkMapCase(evidence_groups=[
        GoldEvidenceGroup(evidence_id="g1", document_id="doc-a", acceptable_chunk_ids={"A"}),
        GoldEvidenceGroup(evidence_id="g2", document_id="doc-b", acceptable_chunk_ids={"B"}),
    ])

    result = grade_retrieval(
        _case(), gold, _trace([("A", "doc-a"), ("A", "doc-a"), ("A", "doc-a")]), cutoffs=[3]
    )
    assert result.evidence_recall_at[3] == 0.5
    assert result.all_evidence_hit_at[3] == 0.0


def test_unanswerable_case_has_no_recall_denominator() -> None:
    from evals.graders.retrieval_grader import grade_retrieval

    result = grade_retrieval(_case(answerable=False), None, _trace([]), cutoffs=[1, 3, 5])

    assert result.document_recall_at == {}
    assert result.evidence_recall_at == {}
    assert result.all_evidence_hit_at == {}
