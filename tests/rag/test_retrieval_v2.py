from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adaptive_tutor.phase2.schemas import RetrievedChunk

from backend.app.infrastructure.persistence.repositories.rag_repository import (
    SQLAlchemyRagRepository,
)
from backend.app.infrastructure.persistence.repositories.rag_retrievers import (
    SQLAlchemyKeywordRetriever,
    SQLAlchemyMetadataRetriever,
    SQLAlchemyVectorRetriever,
    build_postgresql_keyword_statement,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion, User

from backend.app.domain.rag.retrieval import (
    QueryAnalyzer,
    RetrievalCandidate,
    RetrievalFilters,
    RetrievalOrchestrator,
    RetrievalRequest,
)


def test_query_analyzer_preserves_function_and_error_code_exact_terms() -> None:
    analysis = QueryAnalyzer().analyze(
        "Why does calculate_mastery_update() call retrieve() and return ERR_AUTH_401 or HTTP-404?"
    )

    assert analysis.normalized_query == (
        "Why does calculate_mastery_update() call retrieve() and return ERR_AUTH_401 or HTTP-404?"
    )
    assert analysis.exact_terms == (
        "calculate_mastery_update",
        "retrieve",
        "ERR_AUTH_401",
        "HTTP-404",
    )


def test_retrieval_request_normalizes_filters_without_losing_typed_values() -> None:
    created_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = RetrievalRequest(
        query="  vector databases  ",
        user_id=" user-a ",
        top_k=7,
        filters=RetrievalFilters(
            document_ids=("doc-b", "doc-a", "doc-a"),
            node_ids=("node-rag",),
            source_types=("markdown",),
            page_numbers=(2,),
            slide_numbers=(7,),
            min_trusted_level=2,
            created_from=created_from,
            index_version_ids=("index-active",),
        ),
    )

    assert request.query == "vector databases"
    assert request.user_id == "user-a"
    assert request.filters.document_ids == ("doc-a", "doc-b")
    assert request.filters.created_from == created_from


def _candidate(source: str, query: str, chunk_id: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        index_version_id=f"index-{chunk_id}",
        content=f"content for {chunk_id}",
        citation_label=f"citation {chunk_id}",
        source_title=f"{chunk_id}.md",
        source_url=None,
        trusted_level=3,
        metadata={"source_type": "markdown"},
        retriever=source,
        query=query,
        rank=1,
        raw_score=0.75,
        score_kind="test_score",
        higher_is_better=True,
    )


class StubRetriever:
    def __init__(self, source: str) -> None:
        self.source = source
        self.queries: list[str] = []

    def retrieve(self, request, *, query, analysis):
        self.queries.append(query)
        return (_candidate(self.source, query, f"{self.source}-{len(self.queries)}"),)


class DeterministicRewriteStub:
    def rewrite(self, analysis):
        return ("rewritten retrieval query",)


class FailingRewriteStub:
    def rewrite(self, analysis):
        raise RuntimeError("offline rewrite unavailable")


class FailingSourceStub:
    def retrieve(self, request, *, query, analysis):
        raise RuntimeError("source unavailable")


class EmptySourceStub:
    def retrieve(self, request, *, query, analysis):
        return ()


def test_orchestrator_retains_unfused_source_lists_and_trace_provenance() -> None:
    vector = StubRetriever("vector")
    keyword = StubRetriever("keyword")
    metadata = StubRetriever("metadata")
    orchestrator = RetrievalOrchestrator(
        vector_retriever=vector,
        keyword_retriever=keyword,
        metadata_retriever=metadata,
        query_rewriter=DeterministicRewriteStub(),
    )

    result = orchestrator.retrieve(RetrievalRequest(query="original query", user_id="user-a"))

    assert result.queries == ("original query", "rewritten retrieval query")
    assert [candidate.chunk_id for candidate in result.candidates_by_source["vector"]] == [
        "vector-1",
        "vector-2",
    ]
    assert [candidate.chunk_id for candidate in result.candidates_by_source["keyword"]] == [
        "keyword-1",
        "keyword-2",
    ]
    assert [candidate.chunk_id for candidate in result.candidates_by_source["metadata"]] == [
        "metadata-1"
    ]
    assert [attempt.source for attempt in result.trace.source_attempts] == [
        "vector",
        "vector",
        "keyword",
        "keyword",
        "metadata",
    ]
    assert result.trace.source_attempts[0].candidate_ids == ("vector-1",)
    assert result.trace.rewrite.status == "succeeded"


def test_rewrite_failure_falls_back_to_original_query_and_records_error_code() -> None:
    vector = StubRetriever("vector")
    orchestrator = RetrievalOrchestrator(
        vector_retriever=vector,
        keyword_retriever=StubRetriever("keyword"),
        metadata_retriever=StubRetriever("metadata"),
        query_rewriter=FailingRewriteStub(),
    )

    result = orchestrator.retrieve(RetrievalRequest(query="original query"))

    assert result.queries == ("original query",)
    assert vector.queries == ["original query"]
    assert result.trace.rewrite.status == "failed"
    assert result.trace.rewrite.error_code == "query_rewrite_failed"
    assert result.status == "grounded"


def test_unfiltered_metadata_noop_does_not_hide_failed_query_sources() -> None:
    result = RetrievalOrchestrator(
        vector_retriever=FailingSourceStub(),
        keyword_retriever=FailingSourceStub(),
        metadata_retriever=EmptySourceStub(),
    ).retrieve(RetrievalRequest(query="original query"))

    assert result.status == "failed"
    assert result.error_code == "retrieval_source_error"


class QueryEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _seed_chunk(
    session,
    *,
    document_id: str,
    chunk_id: str,
    content: str,
    owner_user_id: str | None,
    corpus_type: str = "user_uploaded",
    status: str = "active",
    parse_status: str = "success",
    trusted_level: int = 3,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    created_at: datetime | None = None,
) -> DocumentChunk:
    if owner_user_id is not None and session.get(User, owner_user_id) is None:
        session.add(
            User(
                id=owner_user_id,
                email=f"{owner_user_id}@example.test",
                display_name=owner_user_id,
                status="active",
            )
        )
        session.flush()
    document = Document(
        id=document_id,
        owner_user_id=owner_user_id,
        corpus_type=corpus_type,
        filename=f"{document_id}.md",
        object_key=f"documents/{document_id}.md",
        mime_type="text/markdown",
        parse_status=parse_status,
        sha256=(document_id * 64)[:64],
        source_url=f"https://example.test/{document_id}",
        trusted_level=trusted_level,
        created_at=created_at or datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    version = DocumentIndexVersion(
        id=f"index-{document_id}-{status}",
        document_id=document_id,
        build_key=f"build-{status}",
        status=status,
        chunk_schema_version="v2",
        chunker_version="chunking-v2",
        embedding_model="test-model",
        embedding_dimensions=3,
        chunk_count=1,
    )
    session.add(document)
    session.flush()
    session.add(version)
    session.flush()
    chunk = DocumentChunk(
        id=chunk_id,
        document_id=document_id,
        index_version_id=version.id,
        chunk_index=1,
        content=content,
        token_count=len(content.split()),
        embedding=embedding or [1.0, 0.0, 0.0],
        embedding_vector=None,
        metadata_json=metadata or {"source_type": "markdown"},
        citation_label=f"{document.filename} chunk 1",
    )
    session.add(chunk)
    session.flush()
    return chunk


def test_sqlite_keyword_retriever_prioritizes_function_and_error_code_exact_hit(
    db_session,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-exact",
        chunk_id="chunk-exact",
        owner_user_id="user-a",
        content="calculate_mastery_update returns ERR_AUTH_401 for an invalid principal.",
    )
    _seed_chunk(
        db_session,
        document_id="doc-general",
        chunk_id="chunk-general",
        owner_user_id="user-a",
        content="General advice about calculation and authentication failures.",
    )
    request = RetrievalRequest(
        query="Why did calculate_mastery_update() return ERR_AUTH_401?",
        user_id="user-a",
        top_k=5,
    )

    candidates = SQLAlchemyKeywordRetriever(db_session).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert candidates[0].chunk_id == "chunk-exact"
    assert candidates[0].score_kind == "keyword_exact_term"
    assert candidates[0].raw_score > candidates[-1].raw_score


def test_sqlite_vector_retriever_returns_semantic_hit(db_session) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-semantic",
        chunk_id="chunk-semantic",
        owner_user_id="user-a",
        content="A semantically relevant passage with no lexical overlap.",
        embedding=[1.0, 0.0, 0.0],
    )
    _seed_chunk(
        db_session,
        document_id="doc-other",
        chunk_id="chunk-other",
        owner_user_id="user-a",
        content="Unrelated content.",
        embedding=[0.0, 1.0, 0.0],
    )
    request = RetrievalRequest(query="vector query", user_id="user-a", top_k=2)

    candidates = SQLAlchemyVectorRetriever(
        db_session,
        QueryEmbeddingClient(),
    ).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.chunk_id for candidate in candidates] == [
        "chunk-semantic",
        "chunk-other",
    ]
    assert candidates[0].score_kind == "cosine_similarity"


def test_all_retrievers_isolate_other_users_and_retired_indexes_but_keep_curated(
    db_session,
) -> None:
    for document_id, owner, corpus, status in (
        ("doc-own", "user-a", "user_uploaded", "active"),
        ("doc-curated", None, "curated", "active"),
        ("doc-other", "user-b", "user_uploaded", "active"),
        ("doc-retired", "user-a", "user_uploaded", "retired"),
    ):
        _seed_chunk(
            db_session,
            document_id=document_id,
            chunk_id=f"chunk-{document_id}",
            owner_user_id=owner,
            corpus_type=corpus,
            status=status,
            content="shared searchable retrieval phrase",
            metadata={"source_type": "markdown", "node_id": "node-rag"},
        )
    request = RetrievalRequest(
        query="shared searchable retrieval phrase",
        user_id="user-a",
        top_k=10,
        filters=RetrievalFilters(source_types=("markdown",)),
    )
    analysis = QueryAnalyzer().analyze(request.query)
    retrievers = (
        SQLAlchemyVectorRetriever(db_session, QueryEmbeddingClient()),
        SQLAlchemyKeywordRetriever(db_session),
        SQLAlchemyMetadataRetriever(db_session),
    )

    for retriever in retrievers:
        candidates = retriever.retrieve(
            request,
            query=request.query,
            analysis=analysis,
        )
        assert {candidate.document_id for candidate in candidates} == {
            "doc-own",
            "doc-curated",
        }


def test_metadata_retriever_applies_document_node_source_page_slide_trust_date_and_index_filters(
    db_session,
) -> None:
    created_at = datetime(2026, 2, 10, tzinfo=timezone.utc)
    matching = _seed_chunk(
        db_session,
        document_id="doc-filtered",
        chunk_id="chunk-filtered",
        owner_user_id="user-a",
        content="metadata selected content",
        trusted_level=4,
        created_at=created_at,
        metadata={
            "node_id": "node-rag",
            "source_type": "slide",
            "page_number": 2,
            "slide_number": 7,
        },
    )
    _seed_chunk(
        db_session,
        document_id="doc-wrong-page",
        chunk_id="chunk-wrong-page",
        owner_user_id="user-a",
        content="metadata selected content",
        trusted_level=4,
        created_at=created_at,
        metadata={
            "node_id": "node-rag",
            "source_type": "slide",
            "page_number": 3,
            "slide_number": 7,
        },
    )
    request = RetrievalRequest(
        query="metadata query",
        user_id="user-a",
        top_k=10,
        filters=RetrievalFilters(
            document_ids=("doc-filtered", "doc-wrong-page"),
            node_ids=("node-rag",),
            source_types=("slide",),
            page_numbers=(2,),
            slide_numbers=(7,),
            min_trusted_level=3,
            max_trusted_level=5,
            created_from=created_at - timedelta(days=1),
            created_to=created_at + timedelta(days=1),
            index_version_ids=(matching.index_version_id,),
        ),
    )

    candidates = SQLAlchemyMetadataRetriever(db_session).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-filtered"]
    assert candidates[0].score_kind == "metadata_filter_match"


def test_postgresql_keyword_statements_use_simple_fts_and_bound_safe_fallbacks() -> None:
    query = "ERR_AUTH_401' OR true --"

    fts_sql = str(build_postgresql_keyword_statement(strategy="fts"))
    exact_sql = str(
        build_postgresql_keyword_statement(strategy="exact", exact_term_count=1)
    )
    trigram_sql = str(build_postgresql_keyword_statement(strategy="trigram"))
    ilike_sql = str(build_postgresql_keyword_statement(strategy="ilike"))

    assert "to_tsvector('simple'" in fts_sql
    assert "websearch_to_tsquery('simple', :keyword_query)" in fts_sql
    assert ":exact_pattern_0" in exact_sql
    assert "ESCAPE '!'" in exact_sql
    assert "similarity(" in trigram_sql
    assert ":ilike_pattern" in ilike_sql
    assert "ESCAPE '!'" in ilike_sql
    assert "index_version.status = 'active'" in fts_sql
    assert "documents.owner_user_id = :user_id" in fts_sql
    assert query not in "\n".join((fts_sql, exact_sql, trigram_sql, ilike_sql))


def test_legacy_repository_signature_returns_tutor_retrieved_chunks_via_adapter(
    db_session,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-legacy",
        chunk_id="chunk-legacy",
        owner_user_id="user-a",
        content="legacy compatibility content",
    )
    repository = SQLAlchemyRagRepository(db_session, QueryEmbeddingClient())

    chunks = repository.retrieve("compatibility", top_k=1, user_id="user-a")
    timed = repository.retrieve_timed("compatibility", top_k=1, user_id="user-a")

    assert [chunk.chunk_id for chunk in chunks] == ["chunk-legacy"]
    assert [chunk.chunk_id for chunk in timed.chunks] == ["chunk-legacy"]
    assert isinstance(chunks[0], RetrievedChunk)
    assert chunks[0].metadata["corpus_type"] == "user_uploaded"
    assert repository.last_retrieval_trace is not None
    assert repository.last_retrieval_trace.source_attempts[0].source == "vector"

    v2 = repository.retrieve_v2(
        RetrievalRequest(query="compatibility", top_k=1, user_id="user-a")
    )
    assert v2.candidates_by_source["vector"][0].chunk_id == "chunk-legacy"
    assert v2.candidates_by_source["keyword"][0].chunk_id == "chunk-legacy"
