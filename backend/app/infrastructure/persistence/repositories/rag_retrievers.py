from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Iterable, Literal

from sqlalchemy import and_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.runtime_config import runtime_mode
from backend.app.domain.rag.retrieval import (
    QueryAnalysis,
    QueryAnalyzer,
    RetrievalCandidate,
    RetrievalRequest,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import pgvector_storage_values


@dataclass(slots=True)
class SQLAlchemyVectorRetriever:
    session: Session
    embedding_client: object
    allowed_document_ids: set[str] | None = None

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        with self.session.begin_nested():
            return self._retrieve_unisolated(request, query=query, analysis=analysis)

    def _retrieve_unisolated(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        if _uses_pgvector(self.session):
            has_visible_rows = self.session.scalar(
                build_postgresql_visible_exists_statement(),
                _postgresql_filter_parameters(
                    request,
                    allowed_document_ids=self.allowed_document_ids,
                ),
            )
            if not has_visible_rows:
                return ()
            return self._retrieve_postgresql(request, query=query)
        visible_rows = _visible_rows(
            self.session,
            request,
            allowed_document_ids=self.allowed_document_ids,
        )
        if not visible_rows:
            return ()
        query_embedding = self.embedding_client.embed(query)
        ranked = sorted(
            (
                (_cosine_similarity(query_embedding, chunk.embedding or []), chunk, document)
                for chunk, document, _ in visible_rows
            ),
            key=lambda item: (-item[0], item[1].id),
        )[: request.top_k]
        return tuple(
            _candidate_from_models(
                chunk=chunk,
                document=document,
                retriever="vector",
                query=query,
                rank=rank,
                raw_score=score,
                score_kind="cosine_similarity",
                higher_is_better=True,
            )
            for rank, (score, chunk, document) in enumerate(ranked, start=1)
        )

    def _retrieve_postgresql(
        self,
        request: RetrievalRequest,
        *,
        query: str,
    ) -> tuple[RetrievalCandidate, ...]:
        rows = self.session.execute(
            build_postgresql_vector_statement(),
            {
                **_postgresql_filter_parameters(
                    request,
                    allowed_document_ids=self.allowed_document_ids,
                ),
                "query_vector": _vector_literal(self.embedding_client.embed(query)),
                "top_k": request.top_k,
            },
        ).mappings()
        return tuple(
            _candidate_from_mapping(
                row,
                retriever="vector",
                query=query,
                rank=rank,
                raw_score=float(row["distance"]),
                score_kind="cosine_distance",
                higher_is_better=False,
            )
            for rank, row in enumerate(rows, start=1)
        )


@dataclass(slots=True)
class SQLAlchemyKeywordRetriever:
    session: Session
    allowed_document_ids: set[str] | None = None

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        with self.session.begin_nested():
            return self._retrieve_unisolated(request, query=query, analysis=analysis)

    def _retrieve_unisolated(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        query_analysis = QueryAnalyzer().analyze(query)
        if self.session.get_bind().dialect.name == "postgresql":
            return self._retrieve_postgresql(
                request,
                query=query,
                analysis=query_analysis,
            )
        ranked: list[tuple[float, str, DocumentChunk, Document]] = []
        for chunk, document, _ in _visible_rows(
            self.session,
            request,
            allowed_document_ids=self.allowed_document_ids,
        ):
            score, score_kind = _sqlite_keyword_score(chunk.content, query_analysis)
            if score > 0:
                ranked.append((score, score_kind, chunk, document))
        ranked.sort(key=lambda item: (-item[0], item[2].id))
        return tuple(
            _candidate_from_models(
                chunk=chunk,
                document=document,
                retriever="keyword",
                query=query,
                rank=rank,
                raw_score=score,
                score_kind=score_kind,
                higher_is_better=True,
            )
            for rank, (score, score_kind, chunk, document) in enumerate(
                ranked[: request.top_k],
                start=1,
            )
        )

    def _retrieve_postgresql(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        parameters = {
            **_postgresql_filter_parameters(
                request,
                allowed_document_ids=self.allowed_document_ids,
            ),
            "keyword_query": query,
            "top_k": request.top_k,
            "trigram_threshold": 0.12,
            "ilike_pattern": f"%{_escape_like(query)}%",
            **{
                f"exact_pattern_{index}": f"%{_escape_like(term)}%"
                for index, term in enumerate(analysis.exact_terms)
            },
        }
        rows: dict[str, tuple[dict[str, Any], str]] = {}
        self._collect_postgresql_rows(
            rows,
            strategy="fts",
            parameters=parameters,
            exact_term_count=0,
        )
        if analysis.exact_terms:
            self._collect_postgresql_rows(
                rows,
                strategy="exact",
                parameters=parameters,
                exact_term_count=len(analysis.exact_terms),
            )
        if len(rows) < request.top_k:
            try:
                with self.session.begin_nested():
                    self._collect_postgresql_rows(
                        rows,
                        strategy="trigram",
                        parameters=parameters,
                        exact_term_count=0,
                    )
            except SQLAlchemyError:
                pass
        if len(rows) < request.top_k:
            self._collect_postgresql_rows(
                rows,
                strategy="ilike",
                parameters=parameters,
                exact_term_count=0,
            )
        ranked = sorted(
            rows.values(),
            key=lambda item: (-float(item[0]["keyword_score"]), item[0]["chunk_id"]),
        )[: request.top_k]
        return tuple(
            _candidate_from_mapping(
                row,
                retriever="keyword",
                query=query,
                rank=rank,
                raw_score=float(row["keyword_score"]),
                score_kind=score_kind,
                higher_is_better=True,
            )
            for rank, (row, score_kind) in enumerate(ranked, start=1)
        )

    def _collect_postgresql_rows(
        self,
        rows: dict[str, tuple[dict[str, Any], str]],
        *,
        strategy: Literal["fts", "exact", "trigram", "ilike"],
        parameters: dict[str, Any],
        exact_term_count: int,
    ) -> None:
        score_kind = {
            "fts": "keyword_fts_simple",
            "exact": "keyword_exact_term",
            "trigram": "keyword_trigram_similarity",
            "ilike": "keyword_ilike",
        }[strategy]
        result = self.session.execute(
            build_postgresql_keyword_statement(
                strategy=strategy,
                exact_term_count=exact_term_count,
            ),
            parameters,
        )
        for row in result.mappings():
            mapped = dict(row)
            existing = rows.get(mapped["chunk_id"])
            if existing is None or float(mapped["keyword_score"]) > float(
                existing[0]["keyword_score"]
            ):
                rows[mapped["chunk_id"]] = (mapped, score_kind)


@dataclass(slots=True)
class SQLAlchemyMetadataRetriever:
    session: Session
    allowed_document_ids: set[str] | None = None

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        with self.session.begin_nested():
            return self._retrieve_unisolated(request, query=query, analysis=analysis)

    def _retrieve_unisolated(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        if not request.filters.has_metadata_constraints:
            return ()
        rows = _visible_rows(
            self.session,
            request,
            allowed_document_ids=self.allowed_document_ids,
        )[: request.top_k]
        return tuple(
            _candidate_from_models(
                chunk=chunk,
                document=document,
                retriever="metadata",
                query=query,
                rank=rank,
                raw_score=1.0,
                score_kind="metadata_filter_match",
                higher_is_better=True,
            )
            for rank, (chunk, document, _) in enumerate(rows, start=1)
        )


def _visible_rows(
    session: Session,
    request: RetrievalRequest,
    *,
    allowed_document_ids: set[str] | None,
) -> list[tuple[DocumentChunk, Document, DocumentIndexVersion]]:
    filters = request.filters
    statement = (
        select(DocumentChunk, Document, DocumentIndexVersion)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(
            DocumentIndexVersion,
            and_(
                DocumentIndexVersion.id == DocumentChunk.index_version_id,
                DocumentIndexVersion.document_id == DocumentChunk.document_id,
            ),
        )
        .where(Document.parse_status == "success")
        .where(DocumentIndexVersion.status == "active")
        .where(_visibility_condition(request.user_id))
        .order_by(DocumentChunk.id)
    )
    document_ids = _effective_document_ids(filters.document_ids, allowed_document_ids)
    if document_ids is not None:
        if not document_ids:
            return []
        statement = statement.where(Document.id.in_(document_ids))
    if filters.index_version_ids:
        statement = statement.where(DocumentIndexVersion.id.in_(filters.index_version_ids))
    if filters.min_trusted_level is not None:
        statement = statement.where(Document.trusted_level >= filters.min_trusted_level)
    if filters.max_trusted_level is not None:
        statement = statement.where(Document.trusted_level <= filters.max_trusted_level)
    if filters.created_from is not None:
        statement = statement.where(Document.created_at >= filters.created_from)
    if filters.created_to is not None:
        statement = statement.where(Document.created_at <= filters.created_to)
    rows = list(session.execute(statement).all())
    return [
        row
        for row in rows
        if _matches_json_metadata(row[0].metadata_json or {}, request)
    ]


def _visibility_condition(user_id: str | None):
    if user_id is None:
        return Document.corpus_type == "curated"
    return (Document.corpus_type == "curated") | (Document.owner_user_id == user_id)


def _effective_document_ids(
    requested: tuple[str, ...],
    allowed: set[str] | None,
) -> set[str] | None:
    requested_set = set(requested)
    if allowed is None:
        return requested_set if requested else None
    return set(allowed) & requested_set if requested else set(allowed)


def _matches_json_metadata(metadata: dict[str, Any], request: RetrievalRequest) -> bool:
    filters = request.filters
    if filters.node_ids and not _metadata_intersects(
        metadata,
        filters.node_ids,
        "node_id",
        "knowledge_node_id",
        "node_ids",
        "knowledge_node_ids",
    ):
        return False
    if filters.source_types and not _metadata_intersects(
        metadata,
        filters.source_types,
        "source_type",
        "processing_source_type",
    ):
        return False
    if filters.page_numbers and not _metadata_intersects(
        metadata,
        filters.page_numbers,
        "page_number",
        "page_numbers",
    ):
        return False
    if filters.slide_numbers and not _metadata_intersects(
        metadata,
        filters.slide_numbers,
        "slide_number",
        "slide_numbers",
    ):
        return False
    return True


def _metadata_intersects(
    metadata: dict[str, Any],
    expected: Iterable[Any],
    *keys: str,
) -> bool:
    expected_set = set(expected)
    actual: set[Any] = set()
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            actual.update(value)
        elif value is not None:
            actual.add(value)
    return bool(actual & expected_set)


def _sqlite_keyword_score(content: str, analysis: QueryAnalysis) -> tuple[float, str]:
    lowered = content.casefold()
    exact_hits = sum(lowered.count(term.casefold()) for term in analysis.exact_terms)
    token_hits = sum(
        lowered.count(token.casefold())
        for token in analysis.tokens
        if len(token) >= 2 and token not in analysis.exact_terms
    )
    trigram_score = _trigram_similarity(analysis.normalized_query, content)
    if exact_hits:
        return 100.0 * exact_hits + token_hits + trigram_score, "keyword_exact_term"
    if token_hits:
        return float(token_hits) + trigram_score, "keyword_token_overlap"
    if trigram_score >= 0.08:
        return trigram_score, "keyword_trigram_similarity"
    return 0.0, "keyword_no_match"


def _trigram_similarity(left: str, right: str) -> float:
    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    union = left_trigrams | right_trigrams
    if not union:
        return 0.0
    return len(left_trigrams & right_trigrams) / len(union)


def _trigrams(value: str) -> set[str]:
    normalized = "  " + " ".join(value.casefold().split()) + "  "
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def _candidate_from_models(
    *,
    chunk: DocumentChunk,
    document: Document,
    retriever: Literal["vector", "keyword", "metadata"],
    query: str,
    rank: int,
    raw_score: float,
    score_kind: str,
    higher_is_better: bool,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk.id,
        document_id=document.id,
        index_version_id=chunk.index_version_id,
        content=chunk.content,
        citation_label=chunk.citation_label,
        source_title=document.filename,
        source_url=document.source_url,
        trusted_level=document.trusted_level,
        metadata=_public_metadata(chunk.metadata_json, corpus_type=document.corpus_type),
        retriever=retriever,
        query=query,
        rank=rank,
        raw_score=raw_score,
        score_kind=score_kind,
        higher_is_better=higher_is_better,
    )


def _candidate_from_mapping(
    row: dict[str, Any],
    *,
    retriever: Literal["vector", "keyword", "metadata"],
    query: str,
    rank: int,
    raw_score: float,
    score_kind: str,
    higher_is_better: bool,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        index_version_id=row["index_version_id"],
        content=row["content"],
        citation_label=row["citation_label"],
        source_title=row["source_title"],
        source_url=row["source_url"],
        trusted_level=row["trusted_level"],
        metadata=_public_metadata(row["metadata_json"], corpus_type=row["corpus_type"]),
        retriever=retriever,
        query=query,
        rank=rank,
        raw_score=raw_score,
        score_kind=score_kind,
        higher_is_better=higher_is_better,
    )


def _public_metadata(metadata: dict[str, Any] | None, *, corpus_type: str) -> dict[str, Any]:
    return {
        **(metadata or {}),
        "untrusted_input": corpus_type != "curated",
        "corpus_type": corpus_type,
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)


def _uses_pgvector(session: Session) -> bool:
    bind = session.get_bind()
    return bool(
        bind
        and bind.dialect.name == "postgresql"
        and runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector") == "pgvector"
    )


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in pgvector_storage_values(values)) + "]"


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _postgresql_metadata_membership(
    keys: tuple[str, ...],
    *,
    parameter: str,
) -> str:
    identifiers = (*keys, parameter)
    if not keys or any(not value.replace("_", "").isalnum() for value in identifiers):
        raise ValueError("PostgreSQL metadata identifiers must be static alphanumeric keys")
    clauses: list[str] = []
    for index, key in enumerate(keys):
        clauses.append(
            f"document_chunks.metadata ->> '{key}' = ANY(CAST(:{parameter} AS text[]))"
        )
        alias = f"metadata_{parameter}_{index}"
        clauses.append(
            f"""EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof((document_chunks.metadata -> '{key}')::jsonb) = 'array'
                            THEN (document_chunks.metadata -> '{key}')::jsonb
                            ELSE '[]'::jsonb
                        END
                    ) AS {alias}(value)
                    WHERE {alias}.value = ANY(CAST(:{parameter} AS text[]))
                )"""
        )
    return "\n                OR ".join(clauses)


_POSTGRESQL_VISIBLE_FROM = f"""
        FROM document_chunks
        JOIN documents ON documents.id = document_chunks.document_id
        JOIN document_index_versions AS index_version
          ON index_version.id = document_chunks.index_version_id
         AND index_version.document_id = document_chunks.document_id
        WHERE documents.parse_status = 'success'
          AND index_version.status = 'active'
          AND (
                documents.corpus_type = 'curated'
                OR (
                    CAST(:user_id AS text) IS NOT NULL
                    AND documents.owner_user_id = CAST(:user_id AS text)
                )
              )
          AND (:restrict_documents = false OR documents.id = ANY(CAST(:document_ids AS text[])))
          AND (:filter_indexes = false OR index_version.id = ANY(CAST(:index_version_ids AS text[])))
          AND (:filter_min_trust = false OR documents.trusted_level >= :min_trusted_level)
          AND (:filter_max_trust = false OR documents.trusted_level <= :max_trusted_level)
          AND (:filter_created_from = false OR documents.created_at >= :created_from)
          AND (:filter_created_to = false OR documents.created_at <= :created_to)
          AND (
                :filter_nodes = false
                OR {_postgresql_metadata_membership(
                    ('node_id', 'node_ids', 'knowledge_node_id', 'knowledge_node_ids'),
                    parameter='node_ids',
                )}
              )
          AND (
                :filter_sources = false
                OR {_postgresql_metadata_membership(
                    ('source_type', 'processing_source_type'),
                    parameter='source_types',
                )}
              )
          AND (
                :filter_pages = false
                OR {_postgresql_metadata_membership(
                    ('page_number', 'page_numbers'),
                    parameter='page_numbers',
                )}
              )
          AND (
                :filter_slides = false
                OR {_postgresql_metadata_membership(
                    ('slide_number', 'slide_numbers'),
                    parameter='slide_numbers',
                )}
              )
"""


def _postgresql_select_columns(score_expression: str, score_alias: str) -> str:
    return f"""
        SELECT
            document_chunks.id AS chunk_id,
            document_chunks.document_id AS document_id,
            document_chunks.index_version_id AS index_version_id,
            document_chunks.content AS content,
            document_chunks.citation_label AS citation_label,
            document_chunks.metadata AS metadata_json,
            documents.filename AS source_title,
            documents.source_url AS source_url,
            documents.trusted_level AS trusted_level,
            documents.corpus_type AS corpus_type,
            {score_expression} AS {score_alias}
    """


def build_postgresql_visible_exists_statement():
    return text(
        """
        SELECT EXISTS (
            SELECT 1
        """
        + _POSTGRESQL_VISIBLE_FROM
        + """
            LIMIT 1
        ) AS has_visible_rows
        """
    )


def build_postgresql_vector_statement():
    return text(
        _postgresql_select_columns(
            "document_chunks.embedding_vector <=> CAST(:query_vector AS halfvec)",
            "distance",
        )
        + _POSTGRESQL_VISIBLE_FROM
        + """
          AND document_chunks.embedding_vector IS NOT NULL
        ORDER BY document_chunks.embedding_vector <=> CAST(:query_vector AS halfvec), document_chunks.id
        LIMIT :top_k
        """
    )


def build_postgresql_keyword_statement(
    *,
    strategy: Literal["fts", "exact", "trigram", "ilike"],
    exact_term_count: int = 0,
):
    if strategy == "fts":
        score = (
            "ts_rank_cd(to_tsvector('simple', COALESCE(document_chunks.content, '')), "
            "websearch_to_tsquery('simple', :keyword_query))"
        )
        predicate = (
            "to_tsvector('simple', COALESCE(document_chunks.content, '')) "
            "@@ websearch_to_tsquery('simple', :keyword_query)"
        )
    elif strategy == "exact":
        if exact_term_count <= 0:
            raise ValueError("exact keyword fallback requires at least one term")
        clauses = [
            f"document_chunks.content ILIKE :exact_pattern_{index} ESCAPE '!'"
            for index in range(exact_term_count)
        ]
        predicate = "(" + " OR ".join(clauses) + ")"
        score = "100.0"
    elif strategy == "trigram":
        score = "similarity(lower(document_chunks.content), lower(:keyword_query))"
        predicate = score + " >= :trigram_threshold"
    elif strategy == "ilike":
        score = "1.0"
        predicate = "document_chunks.content ILIKE :ilike_pattern ESCAPE '!'"
    else:
        raise ValueError(f"unsupported PostgreSQL keyword strategy: {strategy}")
    return text(
        _postgresql_select_columns(score, "keyword_score")
        + _POSTGRESQL_VISIBLE_FROM
        + f"""
          AND {predicate}
        ORDER BY keyword_score DESC, document_chunks.id
        LIMIT :top_k
        """
    )


def _postgresql_filter_parameters(
    request: RetrievalRequest,
    *,
    allowed_document_ids: set[str] | None,
) -> dict[str, Any]:
    filters = request.filters
    document_ids = _effective_document_ids(filters.document_ids, allowed_document_ids)
    return {
        "user_id": request.user_id,
        "restrict_documents": document_ids is not None,
        "document_ids": sorted(document_ids or ()),
        "filter_indexes": bool(filters.index_version_ids),
        "index_version_ids": list(filters.index_version_ids),
        "filter_min_trust": filters.min_trusted_level is not None,
        "min_trusted_level": filters.min_trusted_level or 0,
        "filter_max_trust": filters.max_trusted_level is not None,
        "max_trusted_level": filters.max_trusted_level or 5,
        "filter_created_from": filters.created_from is not None,
        "created_from": filters.created_from,
        "filter_created_to": filters.created_to is not None,
        "created_to": filters.created_to,
        "filter_nodes": bool(filters.node_ids),
        "node_ids": list(filters.node_ids),
        "filter_sources": bool(filters.source_types),
        "source_types": list(filters.source_types),
        "filter_pages": bool(filters.page_numbers),
        "page_numbers": [str(value) for value in filters.page_numbers],
        "filter_slides": bool(filters.slide_numbers),
        "slide_numbers": [str(value) for value in filters.slide_numbers],
    }
