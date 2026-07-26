"""Deterministic document and evidence-group retrieval metrics."""
from __future__ import annotations

from adaptive_tutor.phase2.telemetry import TimedRetrievalResult
from evals.models import GoldChunkMapCase, LearningQaEvaluationCase, RetrievalEvaluationResult


def grade_retrieval(
    case: LearningQaEvaluationCase,
    gold: GoldChunkMapCase | None,
    trace: TimedRetrievalResult,
    *,
    cutoffs: list[int],
) -> RetrievalEvaluationResult:
    normalized_cutoffs = sorted(set(cutoffs))
    if not normalized_cutoffs or normalized_cutoffs[0] < 1:
        raise ValueError("metric cutoffs must be positive")
    chunk_ids = [chunk.chunk_id for chunk in trace.chunks]
    document_ids = [chunk.document_id for chunk in trace.chunks]

    document_hit: dict[int, float] = {}
    document_recall: dict[int, float] = {}
    chunk_hit: dict[int, float] = {}
    evidence_recall: dict[int, float] = {}
    all_evidence_hit: dict[int, float] = {}

    acceptable_documents = set(case.gold_document_ids) | set(case.acceptable_alternative_document_ids)
    groups = gold.evidence_groups if gold is not None else []
    for cutoff in normalized_cutoffs:
        retrieved_chunks = set(chunk_ids[:cutoff])
        retrieved_documents = set(document_ids[:cutoff])
        document_hit[cutoff] = float(bool(retrieved_documents & acceptable_documents))
        satisfied = sum(
            1
            for group in groups
            if retrieved_chunks & group.acceptable_chunk_ids
        )
        chunk_hit[cutoff] = float(satisfied > 0)
        if case.is_answerable:
            gold_documents = set(case.gold_document_ids)
            matched_documents = len(retrieved_documents & gold_documents)
            if retrieved_documents & set(case.acceptable_alternative_document_ids):
                matched_documents = min(len(gold_documents), max(1, matched_documents))
            document_recall[cutoff] = matched_documents / len(gold_documents) if gold_documents else 0.0
            evidence_recall[cutoff] = satisfied / len(groups) if groups else 0.0
            all_evidence_hit[cutoff] = float(bool(groups) and satisfied == len(groups))

    return RetrievalEvaluationResult(
        retrieved_chunk_ids=chunk_ids,
        retrieved_document_ids=document_ids,
        retrieval_scores=trace.scores,
        document_hit_at=document_hit,
        document_recall_at=document_recall,
        chunk_hit_at=chunk_hit,
        evidence_recall_at=evidence_recall,
        all_evidence_hit_at=all_evidence_hit,
        retrieval_latency_ms=trace.total_latency_ms,
        embedding_latency_ms=trace.embedding_latency_ms,
        vector_search_latency_ms=trace.vector_search_latency_ms,
        retrieval_postprocess_latency_ms=trace.postprocess_latency_ms,
    )
