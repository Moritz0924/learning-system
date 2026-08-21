from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest
from sqlalchemy import select, text

from adaptive_tutor.phase2.schemas import RetrievedChunk

from backend.app.infrastructure.persistence.repositories.rag_repository import (
    SQLAlchemyRagRepository,
)
from backend.app.infrastructure.persistence.repositories.rag_retrievers import (
    SQLAlchemyKeywordRetriever,
    SQLAlchemyMetadataRetriever,
    SQLAlchemyVectorRetriever,
    build_postgresql_keyword_statement,
    build_postgresql_vector_statement,
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


def test_query_analyzer_preserves_versions_bare_errors_and_http_statuses() -> None:
    analysis = QueryAnalyzer().analyze(
        "Python 3.11.4 raised TypeError E1234 while HTTP 404 was returned"
    )

    assert analysis.exact_terms == ("3.11.4", "TypeError", "E1234", "HTTP 404")


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


def test_result_deeply_freezes_candidate_lists_metadata_and_trace_provenance() -> None:
    original_metadata = {
        "source_type": "markdown",
        "location": {"pages": [1], "labels": {"primary", "reviewed"}},
    }
    candidate = RetrievalCandidate(
        chunk_id="chunk-immutable",
        document_id="doc-immutable",
        index_version_id="index-immutable",
        content="immutable content",
        citation_label="immutable citation",
        trusted_level=3,
        metadata=original_metadata,
        retriever="vector",
        query="immutable query",
        rank=1,
        raw_score=0.9,
        score_kind="cosine_similarity",
        higher_is_better=True,
    )

    class FixedVectorRetriever:
        def retrieve(self, request, *, query, analysis):
            return (candidate,)

    result = RetrievalOrchestrator(
        vector_retriever=FixedVectorRetriever(),
        keyword_retriever=EmptySourceStub(),
        metadata_retriever=EmptySourceStub(),
    ).retrieve(RetrievalRequest(query="immutable query"))
    original_metadata["location"]["pages"].append(2)
    original_metadata["location"]["labels"].add("mutated")

    assert isinstance(result.candidates_by_source, MappingProxyType)
    assert isinstance(result.raw_candidate_lists, MappingProxyType)
    assert isinstance(candidate.metadata, MappingProxyType)
    assert isinstance(candidate.metadata["location"], MappingProxyType)
    assert candidate.metadata["location"]["pages"] == (1,)
    assert candidate.metadata["location"]["labels"] == frozenset(
        {"primary", "reviewed"}
    )
    assert result.trace.source_attempts[0].candidate_ids == ("chunk-immutable",)

    with pytest.raises(TypeError):
        result.candidates_by_source["vector"] = ()
    with pytest.raises(TypeError):
        result.raw_candidate_lists["vector"] = ()
    with pytest.raises(TypeError):
        candidate.metadata["new"] = "value"
    with pytest.raises(TypeError):
        candidate.metadata["location"]["new"] = "value"

    dumped = result.model_dump()
    dumped["candidates_by_source"]["vector"][0]["metadata"]["location"]["pages"].append(9)
    assert candidate.metadata["location"]["pages"] == (1,)
    serialized = result.model_dump_json()
    assert '"chunk_id":"chunk-immutable"' in serialized
    assert '"pages":[1]' in serialized


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


def test_bare_version_error_and_http_terms_reach_sqlite_and_postgresql_exact_fallback(
    db_session,
) -> None:
    query = "Python 3.11.4 raised TypeError E1234 and returned HTTP 404"
    analysis = QueryAnalyzer().analyze(query)
    _seed_chunk(
        db_session,
        document_id="doc-bare-exact",
        chunk_id="chunk-bare-exact",
        owner_user_id="user-a",
        content="Python 3.11.4 raised TypeError with E1234 after HTTP 404.",
    )
    request = RetrievalRequest(query=query, user_id="user-a")

    sqlite_candidates = SQLAlchemyKeywordRetriever(db_session).retrieve(
        request,
        query=query,
        analysis=analysis,
    )

    assert sqlite_candidates[0].score_kind == "keyword_exact_term"

    class EmptyMappings:
        def mappings(self):
            return []

    class RecordingPostgreSQLSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute(self, statement, parameters):
            self.calls.append((str(statement), dict(parameters)))
            return EmptyMappings()

        def begin_nested(self):
            return nullcontext()

    recording_session = RecordingPostgreSQLSession()
    SQLAlchemyKeywordRetriever(recording_session)._retrieve_postgresql(
        request,
        query=query,
        analysis=analysis,
    )
    exact_calls = [
        (sql, parameters)
        for sql, parameters in recording_session.calls
        if ":exact_pattern_0" in sql
    ]

    assert len(exact_calls) == 1
    assert [exact_calls[0][1][f"exact_pattern_{index}"] for index in range(4)] == [
        "%3.11.4%",
        "%TypeError%",
        "%E1234%",
        "%HTTP 404%",
    ]


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


@pytest.mark.parametrize(
    ("node_key", "node_value"),
    [
        ("knowledge_node_id", "node-rag"),
        ("knowledge_node_ids", ["node-rag", "node-other"]),
        ("node_ids", "node-rag"),
        ("node_ids", ["node-other", "node-rag"]),
    ],
)
def test_sqlite_filters_accept_alternate_source_and_scalar_or_list_metadata_forms(
    db_session,
    node_key,
    node_value,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-metadata-forms",
        chunk_id="chunk-metadata-forms",
        owner_user_id="user-a",
        content="metadata form parity",
        metadata={
            "source_type": "not-the-requested-source",
            "processing_source_type": "slide",
            node_key: node_value,
            "page_numbers": [1, 2],
            "slide_numbers": [7, 8],
        },
    )
    request = RetrievalRequest(
        query="metadata parity",
        user_id="user-a",
        filters=RetrievalFilters(
            node_ids=("node-rag",),
            source_types=("slide",),
            page_numbers=(2,),
            slide_numbers=(7,),
        ),
    )

    candidates = SQLAlchemyMetadataRetriever(db_session).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-metadata-forms"]


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "node_id": "node-rag",
            "source_type": "slide",
            "page_number": 2,
            "slide_number": 7,
        },
        {
            "node_id": ["node-other", "node-rag"],
            "source_type": ["text", "slide"],
            "page_number": [1, 2],
            "slide_number": [6, 7],
        },
        {
            "node_ids": "node-rag",
            "processing_source_type": "slide",
            "page_numbers": 2,
            "slide_numbers": 7,
        },
        {
            "node_ids": ["node-other", "node-rag"],
            "processing_source_type": ["text", "slide"],
            "page_numbers": [1, 2],
            "slide_numbers": [6, 7],
        },
    ],
)
def test_sqlite_accepts_scalar_or_list_values_for_every_documented_filter_alias(
    db_session,
    metadata,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-explicit-aliases",
        chunk_id="chunk-explicit-aliases",
        owner_user_id="user-a",
        content="explicit alias parity",
        metadata=metadata,
    )
    request = RetrievalRequest(
        query="alias parity",
        user_id="user-a",
        filters=RetrievalFilters(
            node_ids=("node-rag",),
            source_types=("slide",),
            page_numbers=(2,),
            slide_numbers=(7,),
        ),
    )

    candidates = SQLAlchemyMetadataRetriever(db_session).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-explicit-aliases"]


def test_postgresql_vector_and_keyword_scope_support_all_sqlite_metadata_forms() -> None:
    vector_sql = str(build_postgresql_vector_statement())
    keyword_sql = str(build_postgresql_keyword_statement(strategy="fts"))

    for sql in (vector_sql, keyword_sql):
        assert "CAST(:user_id AS text) IS NOT NULL" in sql
        assert "document_chunks.metadata ->> 'source_type'" in sql
        assert "document_chunks.metadata ->> 'processing_source_type'" in sql
        assert "document_chunks.metadata ->> 'knowledge_node_id'" in sql
        assert "document_chunks.metadata ->> 'node_id'" in sql
        assert "document_chunks.metadata ->> 'knowledge_node_ids'" in sql
        assert "document_chunks.metadata ->> 'node_ids'" in sql
        assert "document_chunks.metadata ->> 'page_number'" in sql
        assert "document_chunks.metadata ->> 'slide_number'" in sql
        assert "document_chunks.metadata -> 'page_numbers'" in sql
        assert "document_chunks.metadata -> 'slide_numbers'" in sql
        assert "jsonb_array_elements_text" in sql
        assert "ANY(CAST(:node_ids AS text[]))" in sql
        assert "ANY(CAST(:source_types AS text[]))" in sql
        assert "ANY(CAST(:page_numbers AS text[]))" in sql
        assert "ANY(CAST(:slide_numbers AS text[]))" in sql
        for key in (
            "node_id",
            "node_ids",
            "source_type",
            "processing_source_type",
            "page_number",
            "page_numbers",
            "slide_number",
            "slide_numbers",
        ):
            assert f"document_chunks.metadata ->> '{key}'" in sql
            assert f"document_chunks.metadata -> '{key}'" in sql


def test_legacy_repository_signature_returns_tutor_retrieved_chunks_via_adapter(
    db_session,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-legacy",
        chunk_id="chunk-legacy",
        owner_user_id="user-a",
        content="legacy compatibility content",
        metadata={"source_type": "markdown", "location": {"pages": [1]}},
    )
    repository = SQLAlchemyRagRepository(db_session, QueryEmbeddingClient())

    chunks = repository.retrieve("compatibility", top_k=1, user_id="user-a")
    timed = repository.retrieve_timed("compatibility", top_k=1, user_id="user-a")

    assert [chunk.chunk_id for chunk in chunks] == ["chunk-legacy"]
    assert [chunk.chunk_id for chunk in timed.chunks] == ["chunk-legacy"]
    assert isinstance(chunks[0], RetrievedChunk)
    assert chunks[0].metadata["corpus_type"] == "user_uploaded"
    assert '"pages":[1]' in chunks[0].model_dump_json()
    assert repository.last_retrieval_trace is not None
    assert repository.last_retrieval_trace.source_attempts[0].source == "vector"

    v2 = repository.retrieve_v2(
        RetrievalRequest(query="compatibility", top_k=1, user_id="user-a")
    )
    assert v2.candidates_by_source["vector"][0].chunk_id == "chunk-legacy"
    assert v2.candidates_by_source["keyword"][0].chunk_id == "chunk-legacy"


def test_legacy_repository_returns_orchestrator_selected_chunks_without_changing_schema(
    db_session,
    monkeypatch,
) -> None:
    vector_candidate = _candidate("vector", "compatibility", "vector-first")
    selected_candidate = _candidate("keyword", "compatibility", "selected-context")

    class OrchestratorStub:
        def retrieve(self, request):
            attempt = type(
                "Attempt",
                (),
                {
                    "source": "vector",
                    "query": request.query,
                    "status": "succeeded",
                    "error_code": None,
                },
            )()
            trace = type("Trace", (), {"source_attempts": (attempt,)})()
            return type(
                "Result",
                (),
                {
                    "status": "grounded",
                    "error_code": None,
                    "queries": (request.query,),
                    "candidates_by_source": {"vector": (vector_candidate,)},
                    "selected_candidates": (selected_candidate,),
                    "trace": trace,
                },
            )()

    monkeypatch.setattr(
        SQLAlchemyRagRepository,
        "_orchestrator",
        lambda self: OrchestratorStub(),
    )
    repository = SQLAlchemyRagRepository(db_session, QueryEmbeddingClient())

    chunks = repository.retrieve("compatibility", top_k=1, user_id="user-a")

    assert [chunk.chunk_id for chunk in chunks] == ["selected-context"]
    assert isinstance(chunks[0], RetrievedChunk)
    assert set(chunks[0].model_dump()) == {
        "chunk_id",
        "document_id",
        "content",
        "citation_label",
        "source_title",
        "source_url",
        "trusted_level",
        "metadata",
    }
    assert repository.last_retrieval_result.selected_candidates == (selected_candidate,)
    assert repository.last_retrieval_trace is repository.last_retrieval_result.trace


def test_repository_source_savepoints_isolate_database_failure_and_keep_caller_transaction_usable(
    db_session,
    monkeypatch,
) -> None:
    _seed_chunk(
        db_session,
        document_id="doc-savepoint",
        chunk_id="chunk-savepoint",
        owner_user_id="user-a",
        content="searchable keyword survives a vector database failure",
    )
    original_begin_nested = db_session.begin_nested
    nested_calls: list[str] = []

    def recording_begin_nested():
        nested_calls.append("savepoint")
        return original_begin_nested()

    monkeypatch.setattr(db_session, "begin_nested", recording_begin_nested)

    class DatabaseFailingEmbedding:
        def embed(self, query: str) -> list[float]:
            db_session.execute(text("SELECT * FROM missing_retrieval_table"))
            raise AssertionError("unreachable")

    repository = SQLAlchemyRagRepository(db_session, DatabaseFailingEmbedding())

    chunks = repository.retrieve("searchable keyword", top_k=5, user_id="user-a")

    assert chunks == []
    assert repository.last_retrieval_status == "failed"
    assert repository.degraded_reason == "retrieval_database_error"
    assert repository.last_retrieval_result is not None
    assert [
        candidate.chunk_id
        for candidate in repository.last_retrieval_result.candidates_by_source["keyword"]
    ] == ["chunk-savepoint"]
    assert [attempt.status for attempt in repository.last_retrieval_trace.source_attempts] == [
        "failed",
        "succeeded",
        "succeeded",
    ]
    assert len(nested_calls) == 3

    db_session.add(
        User(
            id="user-after-retrieval-failure",
            email="after-failure@example.test",
            display_name="After Failure",
            status="active",
        )
    )
    db_session.flush()
    assert db_session.scalar(
        select(User.id).where(User.id == "user-after-retrieval-failure")
    ) == "user-after-retrieval-failure"


def test_empty_visible_corpus_returns_no_context_without_calling_embedding_provider(
    db_session,
) -> None:
    class RecordingEmbedding:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed(self, query: str) -> list[float]:
            self.calls.append(query)
            return [1.0, 0.0, 0.0]

    embedding = RecordingEmbedding()
    repository = SQLAlchemyRagRepository(db_session, embedding)

    chunks = repository.retrieve("empty corpus query", top_k=5, user_id="user-a")

    assert chunks == []
    assert embedding.calls == []
    assert repository.last_retrieval_status == "no_context"
    assert repository.degraded_reason is None

    result = repository.retrieve_v2(
        RetrievalRequest(query="empty corpus query", top_k=5, user_id="user-a")
    )
    assert result.status == "no_context"
    assert result.error_code is None
    assert embedding.calls == []


def test_pgvector_empty_preflight_uses_filtered_exists_without_embedding_or_row_transfer(
    monkeypatch,
) -> None:
    class PostgreSQLBind:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    class ExistsOnlySession:
        def __init__(self) -> None:
            self.scalar_calls: list[tuple[str, dict]] = []
            self.execute_calls: list[tuple[object, dict]] = []

        def get_bind(self):
            return PostgreSQLBind()

        def begin_nested(self):
            return nullcontext()

        def scalar(self, statement, parameters):
            self.scalar_calls.append((str(statement), dict(parameters)))
            return False

        def execute(self, statement, parameters=None):
            self.execute_calls.append((statement, dict(parameters or {})))
            raise AssertionError("pgvector preflight must not materialize visible rows")

    class RecordingEmbedding:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed(self, query: str) -> list[float]:
            self.calls.append(query)
            return [1.0, 0.0, 0.0]

    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "pgvector")
    session = ExistsOnlySession()
    embedding = RecordingEmbedding()
    request = RetrievalRequest(
        query="empty pgvector query",
        user_id="user-a",
        filters=RetrievalFilters(
            document_ids=("doc-empty",),
            node_ids=("node-rag",),
            source_types=("slide",),
            page_numbers=(2,),
            slide_numbers=(7,),
            min_trusted_level=2,
            index_version_ids=("index-empty",),
        ),
    )

    candidates = SQLAlchemyVectorRetriever(session, embedding).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert candidates == ()
    assert embedding.calls == []
    assert session.execute_calls == []
    assert len(session.scalar_calls) == 1
    exists_sql, parameters = session.scalar_calls[0]
    assert "SELECT EXISTS" in exists_sql
    assert "SELECT 1" in exists_sql
    assert "index_version.status = 'active'" in exists_sql
    assert "documents.owner_user_id = :user_id" in exists_sql
    assert "document_chunks.content AS content" not in exists_sql
    assert "embedding_vector <=>" not in exists_sql
    assert parameters["user_id"] == "user-a"
    assert parameters["document_ids"] == ["doc-empty"]
    assert parameters["node_ids"] == ["node-rag"]


def test_pgvector_nonempty_preflight_then_uses_indexed_top_k_query(monkeypatch) -> None:
    class PostgreSQLBind:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    class MappingRows:
        def mappings(self):
            return [
                {
                    "chunk_id": "chunk-pgvector",
                    "document_id": "doc-pgvector",
                    "index_version_id": "index-pgvector",
                    "content": "indexed vector result",
                    "citation_label": "pgvector citation",
                    "metadata_json": {"source_type": "markdown"},
                    "source_title": "pgvector.md",
                    "source_url": None,
                    "trusted_level": 3,
                    "corpus_type": "curated",
                    "distance": 0.1,
                }
            ]

    class PostgreSQLSession:
        def __init__(self) -> None:
            self.scalar_sql: list[str] = []
            self.execute_sql: list[str] = []

        def get_bind(self):
            return PostgreSQLBind()

        def begin_nested(self):
            return nullcontext()

        def scalar(self, statement, parameters):
            self.scalar_sql.append(str(statement))
            return True

        def execute(self, statement, parameters):
            self.execute_sql.append(str(statement))
            return MappingRows()

    class RecordingEmbedding:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed(self, query: str) -> list[float]:
            self.calls.append(query)
            return [1.0, 0.0, 0.0]

    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "pgvector")
    session = PostgreSQLSession()
    embedding = RecordingEmbedding()
    request = RetrievalRequest(query="indexed query", top_k=1)

    candidates = SQLAlchemyVectorRetriever(session, embedding).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-pgvector"]
    assert embedding.calls == ["indexed query"]
    assert len(session.scalar_sql) == 1
    assert len(session.execute_sql) == 1
    assert "SELECT EXISTS" in session.scalar_sql[0]
    assert "embedding_vector <=> CAST(:query_vector AS halfvec)" in session.execute_sql[0]
    assert "ORDER BY document_chunks.embedding_vector <=>" in session.execute_sql[0]
    assert "LIMIT :top_k" in session.execute_sql[0]
