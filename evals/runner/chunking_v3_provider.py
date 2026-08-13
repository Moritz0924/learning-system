"""Provider-backed Phase 1 helpers for the frozen Hybrid Chunking V3 study.

This module is deliberately evaluation-only.  It creates completed ``ready``
indexes in the isolated evaluation database and passes their exact IDs to the
explicit retriever; it never activates a candidate index.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.document_index_service import (
    DocumentIndexService,
    embedding_client_identity,
)
from backend.app.domain.rag.chunking.v3.config import (
    HybridChunkPolicy,
    TokenizerIdentity,
    ChunkingExecutionSnapshot,
)
from backend.app.domain.rag.retrieval import QueryAnalyzer, RetrievalRequest
from backend.app.models import Document, DocumentIndexVersion
from backend.app.services.token_counting import TiktokenTokenCounter
from evals.adapters.explicit_index_retriever import ExplicitIndexVersionVectorRetriever
from evals.chunking_v3 import (
    ChunkingDocument,
    ChunkingQuery,
    EvidenceAnchor,
    RetrievedChunk,
    map_chunk_to_anchors,
    score_ranked_chunks,
)
from evals.chunking_v3_runner import _empty_diagnostics, chunk_document
from evals.runner.evaluation_config import EvaluationConfig


PRODUCTION_FREEZE_SHA = "76ae7800ae75ca9873f3b28ce4be1eb751433c60"
PHASE1_TOP_N = 20


@dataclass(frozen=True)
class ProviderVariantIndex:
    variant: str
    index_version_ids: tuple[str, ...]
    chunk_count: int
    diagnostics: dict[str, int]
    ingestion_latency_seconds: float
    parser_implementation_version: str
    chunking_implementation_version: str
    policy_fingerprint: str
    tokenizer_id: str


def require_provider_backed_isolation(*, allow_remote: bool) -> tuple[EvaluationConfig, str]:
    """Apply the existing isolated-DB and explicit remote-authorization gates."""
    config = EvaluationConfig.from_environment(allow_remote=allow_remote)
    config.require_remote("embedding")
    database_url = config.require_database_url(require_postgres=True)
    return config, database_url


def seed_provider_variant_index(
    session: Session,
    *,
    documents: Sequence[tuple[ChunkingDocument, str]],
    variant: str,
    embedding_client: object,
    fixed_threshold: float | None = None,
) -> ProviderVariantIndex:
    """Build or reuse a non-active, provider-embedded index cohort for one variant."""
    if variant not in {"A", "P", "B", "C", "D", "E"}:
        raise ValueError(f"unsupported chunking variant: {variant}")
    if variant == "D" and fixed_threshold is None:
        raise ValueError("variant D requires its frozen fixed threshold")
    if variant != "D" and fixed_threshold is not None:
        raise ValueError("only variant D accepts a fixed threshold")

    policy = HybridChunkPolicy()
    snapshot = ChunkingExecutionSnapshot.from_v3_policy(
        policy=policy,
        tokenizer=TokenizerIdentity(policy.tokenizer_id),
    )
    provider, model, dimensions = embedding_client_identity(embedding_client)
    diagnostics = _empty_diagnostics()
    service = DocumentIndexService(session, embedding_client)
    index_version_ids: list[str] = []
    chunk_count = 0
    started = perf_counter()
    for source_document, source_text in sorted(documents, key=lambda item: item[0].document_id):
        document = _ensure_evaluation_document(session, source_document)
        chunks = chunk_document(
            source_text,
            filename=source_document.filename,
            variant=variant,
            policy=policy,
            fixed_threshold=fixed_threshold,
            semantic_encoder=embedding_client,
            diagnostics=diagnostics,
        )
        counter = TiktokenTokenCounter(policy.tokenizer_id)
        prepared_chunks = [
            {
                "content": content,
                "metadata": {
                    **metadata,
                    "token_count": counter.count(content),
                    "evaluation_variant": variant,
                    "evaluation_source_document_id": source_document.document_id,
                    "evaluation_phase": "provider-backed-isolation",
                },
            }
            for content, metadata in chunks
        ]
        build_key = _provider_build_key(
            source_document=source_document,
            variant=variant,
            fixed_threshold=fixed_threshold,
            provider=provider,
            model=model,
            dimensions=dimensions,
            policy_fingerprint=snapshot.policy_fingerprint or "",
        )
        version = service.build_index(
            user_id=None,
            document_id=document.id,
            build_key=build_key,
            chunks=prepared_chunks,
            chunker_version=_variant_chunker_version(variant, fixed_threshold),
            chunk_schema_version="v3",
        )
        if version.status not in {"ready", "active", "retired"} or version.completed_at is None:
            raise RuntimeError(
                f"provider index build did not complete for {source_document.document_id}: {version.status}"
            )
        index_version_ids.append(version.id)
        chunk_count += version.chunk_count
    session.commit()
    return ProviderVariantIndex(
        variant=variant,
        index_version_ids=tuple(index_version_ids),
        chunk_count=chunk_count,
        diagnostics=diagnostics,
        ingestion_latency_seconds=perf_counter() - started,
        parser_implementation_version=snapshot.parser_implementation_version,
        chunking_implementation_version=snapshot.chunking_implementation_version,
        policy_fingerprint=snapshot.policy_fingerprint or "",
        tokenizer_id=snapshot.tokenizer_id or policy.tokenizer_id,
    )


def evaluate_provider_query(
    session: Session,
    *,
    index: ProviderVariantIndex,
    query: ChunkingQuery,
    anchors: Sequence[EvidenceAnchor],
    embedding_client: object,
    top_n: int = PHASE1_TOP_N,
) -> dict[str, object]:
    """Run one Phase 1 vector-only query through the explicit index cohort."""
    if top_n != PHASE1_TOP_N:
        raise ValueError(f"Phase 1 top_n is frozen at {PHASE1_TOP_N}")
    request = RetrievalRequest(query=query.query, top_k=top_n)
    candidates = ExplicitIndexVersionVectorRetriever(
        session,
        embedding_client=embedding_client,
        index_version_ids=index.index_version_ids,
    ).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )
    ranked = [
        RetrievedChunk(
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id.removeprefix("chunking-v3-source-"),
            content=candidate.content,
            token_count=int(candidate.metadata.get("token_count", 0)),
            covered_anchor_ids=map_chunk_to_anchors(
                document_id=candidate.document_id.removeprefix("chunking-v3-source-"),
                content=candidate.content,
                metadata=candidate.metadata,
                anchors=anchors,
            ),
        )
        for candidate in candidates
    ]
    return score_ranked_chunks(
        query=query,
        ranked=ranked,
        anchors_by_id={anchor.anchor_id: anchor for anchor in anchors},
        token_counter=TiktokenTokenCounter(index.tokenizer_id),
    )


def assert_no_candidate_is_active(
    session: Session,
    indexes: Iterable[ProviderVariantIndex],
) -> None:
    """Fail closed if a Phase 1 helper ever violates the production invariant."""
    ids = [version_id for index in indexes for version_id in index.index_version_ids]
    active_ids = set(
        session.scalars(
            select(DocumentIndexVersion.id).where(
                DocumentIndexVersion.id.in_(ids),
                DocumentIndexVersion.status == "active",
            )
        )
    )
    if active_ids:
        raise RuntimeError(
            "provider isolation must not activate candidate indexes: "
            + ", ".join(sorted(active_ids))
        )


def _ensure_evaluation_document(session: Session, source_document: ChunkingDocument) -> Document:
    document_id = f"chunking-v3-source-{source_document.document_id}"
    existing = session.get(Document, document_id)
    if existing is not None:
        if existing.sha256 != source_document.source_sha256 or existing.parse_status != "success":
            raise RuntimeError(
                f"provider evaluation source document drift for {source_document.document_id}; use a fresh isolated database"
            )
        return existing
    suffix = source_document.filename.rsplit(".", 1)[-1].lower()
    mime_type = {
        "md": "text/markdown",
        "txt": "text/plain",
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }[suffix]
    document = Document(
        id=document_id,
        owner_user_id=None,
        corpus_type="curated",
        filename=source_document.filename,
        object_key=f"evals/chunking-v3-ablation-v2/{source_document.filename}",
        mime_type=mime_type,
        parse_status="success",
        sha256=source_document.source_sha256,
        source_url=f"eval://chunking-v3-ablation-v2/{source_document.document_id}",
        trusted_level=3,
        parser_version="document-parser-v4.1",
    )
    session.add(document)
    session.flush([document])
    return document


def _provider_build_key(
    *,
    source_document: ChunkingDocument,
    variant: str,
    fixed_threshold: float | None,
    provider: str,
    model: str,
    dimensions: int,
    policy_fingerprint: str,
) -> str:
    payload = (
        f"provider-isolation-v1\0{source_document.source_sha256}\0{variant}\0"
        f"{fixed_threshold}\0{provider}\0{model}\0{dimensions}\0{policy_fingerprint}"
    ).encode("utf-8")
    return f"eval-v3-{sha256(payload).hexdigest()[:56]}"


def _variant_chunker_version(variant: str, fixed_threshold: float | None) -> str:
    suffix = f"-d{fixed_threshold:.2f}" if fixed_threshold is not None else ""
    return f"hybrid-chunking-v3.1-eval-{variant}{suffix}"


__all__ = [
    "PHASE1_TOP_N",
    "PRODUCTION_FREEZE_SHA",
    "ProviderVariantIndex",
    "assert_no_candidate_is_active",
    "evaluate_provider_query",
    "require_provider_backed_isolation",
    "seed_provider_variant_index",
]
