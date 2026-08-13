"""Phase 2 Hybrid Chunking V3 evaluation through the real retrieval orchestrator.

This is an evaluation runner, not a second retrieval implementation.  Candidate
index versions are activated only inside the isolated evaluation database so
``SQLAlchemyRagRepository.retrieve_v2`` reaches the production
``RetrievalOrchestrator`` unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.application.document_index_service import DocumentIndexService
from backend.app.domain.rag.retrieval import RetrievalRequest
from backend.app.infrastructure.persistence.repositories.rag_repository import (
    SQLAlchemyRagRepository,
)
from backend.app.models import DocumentIndexVersion
from backend.app.services.token_counting import TiktokenTokenCounter
from evals.chunking_v3 import (
    ChunkingQuery,
    EvidenceAnchor,
    RetrievedChunk,
    map_chunk_to_anchors,
    score_ranked_chunks,
)
from evals.runner.chunking_v3_provider import PHASE1_TOP_N, ProviderVariantIndex


class ProductionAblationError(RuntimeError):
    """The active-index A-vs-Best protocol cannot be run safely."""


def run_production_a_vs_best(
    session: Session,
    *,
    indexes: Mapping[str, ProviderVariantIndex],
    baseline: str,
    best: str,
    queries: Sequence[ChunkingQuery],
    anchors: Sequence[EvidenceAnchor],
    embedding_client: object,
    top_n: int = PHASE1_TOP_N,
) -> dict[str, object]:
    """Sequentially run A then Best with the production active-index path.

    ``baseline`` must be A and ``best`` may be B/C/D/E only.  Every source
    document must have an active version before the run; those versions are
    reactivated in ``finally`` even if retrieval or report construction fails.
    """
    if baseline != "A":
        raise ProductionAblationError("production baseline is frozen at A")
    if best not in {"B", "C", "D", "E"}:
        raise ProductionAblationError("production best must be one of B/C/D/E; P is attribution-only")
    if top_n != PHASE1_TOP_N:
        raise ProductionAblationError(f"production top_n is frozen at {PHASE1_TOP_N}")
    if baseline not in indexes or best not in indexes:
        raise ProductionAblationError("both A and Best index cohorts are required")
    if not queries:
        raise ProductionAblationError("production evaluation requires frozen Test queries")

    selected = {baseline: indexes[baseline], best: indexes[best]}
    _validate_parallel_cohorts(session, selected)
    original_active = _active_versions_for_cohorts(session, selected.values())
    evaluation_document_ids = {
        version.document_id
        for version in _versions_by_id(
            session,
            [version_id for index in selected.values() for version_id in index.index_version_ids],
        ).values()
    }
    activation_sequence: list[dict[str, object]] = []
    per_query: dict[str, dict[str, object]] = {}
    traces: dict[str, dict[str, object]] = {}
    try:
        for variant, index in selected.items():
            _activate_cohort(session, index, embedding_client=embedding_client)
            activation_sequence.append(
                {"variant": variant, "index_version_ids": list(index.index_version_ids)}
            )
            repository = SQLAlchemyRagRepository(
                session,
                embedding_client,
                allowed_document_ids=evaluation_document_ids,
            )
            orchestrator = repository._orchestrator()
            for query in queries:
                result = repository.retrieve_v2(
                    RetrievalRequest(query=query.query, top_k=top_n)
                )
                if result.status == "failed":
                    raise ProductionAblationError(
                        f"production retrieval failed for {variant}/{query.query_id}: {result.error_code}"
                    )
                per_query.setdefault(query.query_id, {})[variant] = _score_result(
                    result=result,
                    query=query,
                    anchors=anchors,
                    tokenizer_id=index.tokenizer_id,
                )
                traces.setdefault(query.query_id, {})[variant] = _trace_payload(
                    result=result,
                    repository=repository,
                    orchestrator=orchestrator,
                )
    finally:
        _restore_active_versions(
            session,
            original_active,
            embedding_client,
            evaluation_document_ids=evaluation_document_ids,
        )

    return {
        "production_orchestrator": "RetrievalOrchestrator",
        "variants": [baseline, best],
        "top_n": top_n,
        "activation_sequence": activation_sequence,
        "per_query": per_query,
        "traces": traces,
    }


def _validate_parallel_cohorts(
    session: Session,
    indexes: Mapping[str, ProviderVariantIndex],
) -> None:
    document_sets: set[frozenset[str]] = set()
    for index in indexes.values():
        versions = _versions_by_id(session, index.index_version_ids)
        if len(versions) != len(index.index_version_ids):
            raise ProductionAblationError("candidate index cohort has missing versions")
        if any(version.status not in {"ready", "active", "retired"} or version.completed_at is None for version in versions.values()):
            raise ProductionAblationError("candidate index cohort contains a noncompleted version")
        document_sets.add(frozenset(version.document_id for version in versions.values()))
    if len(document_sets) != 1:
        raise ProductionAblationError("A and Best must cover the same source documents")


def _active_versions_for_cohorts(
    session: Session,
    cohorts: Sequence[ProviderVariantIndex],
) -> dict[str, str]:
    target_ids = [version_id for cohort in cohorts for version_id in cohort.index_version_ids]
    target_versions = _versions_by_id(session, target_ids)
    document_ids = {version.document_id for version in target_versions.values()}
    active = {
        version.document_id: version.id
        for version in session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.document_id.in_(document_ids),
                DocumentIndexVersion.status == "active",
            )
        )
    }
    if active and set(active) != document_ids:
        raise ProductionAblationError(
            "pre-existing active indexes must cover every evaluation source document"
        )
    return active


def _activate_cohort(
    session: Session,
    cohort: ProviderVariantIndex,
    *,
    embedding_client: object,
) -> None:
    service = DocumentIndexService(session, embedding_client=embedding_client)
    for version in _versions_by_id(session, cohort.index_version_ids).values():
        service.activate_index(
            user_id=None,
            document_id=version.document_id,
            index_version_id=version.id,
        )
    session.commit()


def _restore_active_versions(
    session: Session,
    original_active: Mapping[str, str],
    embedding_client: object,
    *,
    evaluation_document_ids: set[str],
) -> None:
    """Restore the previous active state even after a failed retrieval/query."""
    service = DocumentIndexService(session, embedding_client=embedding_client)
    try:
        if not original_active:
            document_ids = {
                version.document_id
                for version in session.scalars(
                    select(DocumentIndexVersion).where(
                        DocumentIndexVersion.document_id.in_(evaluation_document_ids),
                        DocumentIndexVersion.status == "active"
                    )
                )
            }
            if document_ids:
                session.execute(
                    update(DocumentIndexVersion)
                    .where(
                        DocumentIndexVersion.document_id.in_(evaluation_document_ids),
                        DocumentIndexVersion.status == "active",
                    )
                    .values(
                        status="retired",
                        retired_at=datetime.now(timezone.utc),
                    )
                )
                session.commit()
            return
        for document_id, version_id in original_active.items():
            service.activate_index(
                user_id=None,
                document_id=document_id,
                index_version_id=version_id,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise


def _versions_by_id(
    session: Session,
    index_version_ids: Sequence[str],
) -> dict[str, DocumentIndexVersion]:
    return {
        version.id: version
        for version in session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.id.in_(index_version_ids)
            )
        )
    }


def _score_result(
    *,
    result: object,
    query: ChunkingQuery,
    anchors: Sequence[EvidenceAnchor],
    tokenizer_id: str,
) -> dict[str, object]:
    ranked = []
    ordered = sorted(
        result.reranked_candidates,
        key=lambda candidate: (candidate.reranked_rank or candidate.fused_rank, candidate.chunk_id),
    )[:PHASE1_TOP_N]
    for candidate in ordered:
        document_id = candidate.document_id.removeprefix("chunking-v3-source-")
        ranked.append(
            RetrievedChunk(
                chunk_id=candidate.chunk_id,
                document_id=document_id,
                content=candidate.content,
                token_count=int(candidate.metadata.get("token_count", 0)),
                covered_anchor_ids=map_chunk_to_anchors(
                    document_id=document_id,
                    content=candidate.content,
                    metadata=candidate.metadata,
                    anchors=anchors,
                ),
            )
        )
    return score_ranked_chunks(
        query=query,
        ranked=ranked,
        anchors_by_id={anchor.anchor_id: anchor for anchor in anchors},
        token_counter=TiktokenTokenCounter(tokenizer_id),
    )


def _trace_payload(*, result: object, repository: object, orchestrator: object) -> dict[str, object]:
    trace = result.trace
    return {
        "status": result.status,
        "error_code": result.error_code,
        "query_rewriter": (
            "not_configured"
            if repository.query_rewriter is None
            else f"{type(repository.query_rewriter).__module__}.{type(repository.query_rewriter).__name__}"
        ),
        "reranker": f"{type(orchestrator.reranker).__module__}.{type(orchestrator.reranker).__name__}",
        "source_attempts": [
            {
                "source": attempt.source,
                "status": attempt.status,
                "error_code": attempt.error_code,
                "candidate_ids": list(attempt.candidate_ids),
            }
            for attempt in trace.source_attempts
        ],
        "fallback_paths": list(trace.fallback_reasons),
        "rerank_status": trace.rerank_status,
        "selected_chunk_ids": [candidate.chunk_id for candidate in result.selected_candidates],
    }


__all__ = ["ProductionAblationError", "run_production_a_vs_best"]
