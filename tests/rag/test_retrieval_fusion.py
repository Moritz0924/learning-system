from __future__ import annotations

from multiprocessing import active_children
from threading import current_thread, enumerate as enumerate_threads
from time import monotonic

import pytest

import backend.app.domain.rag.retrieval as retrieval
import backend.app.domain.rag.retrieval.reranking as reranking_module


def _candidate(
    chunk_id: str,
    *,
    source: str,
    rank: int,
    raw_score: float,
    query: str = "fusion query",
    document_id: str | None = None,
    content: str | None = None,
    metadata: dict | None = None,
) -> retrieval.RetrievalCandidate:
    return retrieval.RetrievalCandidate(
        chunk_id=chunk_id,
        document_id=document_id or f"doc-{chunk_id}",
        index_version_id=f"index-{chunk_id}",
        content=content or f"content for {chunk_id}",
        citation_label=f"citation {chunk_id}",
        source_title=f"{chunk_id}.md",
        trusted_level=3,
        metadata=metadata or {},
        retriever=source,
        query=query,
        rank=rank,
        raw_score=raw_score,
        score_kind=f"{source}_score",
        higher_is_better=True,
    )


def test_rrf_uses_rank_formula_and_preserves_cross_source_provenance() -> None:
    fusion = retrieval.ReciprocalRankFusion()
    vector = _candidate("chunk-a", source="vector", rank=1, raw_score=0.91)
    keyword = _candidate("chunk-a", source="keyword", rank=2, raw_score=12.0)

    fused = fusion.fuse({"vector": (vector,), "keyword": (keyword,), "metadata": ()})

    assert len(fused) == 1
    assert fused[0].rrf_score == pytest.approx((1 / 61) + (1 / 62))
    assert [item.retriever for item in fused[0].provenance] == ["vector", "keyword"]
    assert [item.rank for item in fused[0].provenance] == [1, 2]
    assert [item.raw_score for item in fused[0].provenance] == [0.91, 12.0]
    assert [item.rrf_contribution for item in fused[0].provenance] == pytest.approx(
        [1 / 61, 1 / 62]
    )


def test_rrf_k_is_configurable() -> None:
    candidate = _candidate("chunk-a", source="vector", rank=1, raw_score=0.91)

    fused = retrieval.ReciprocalRankFusion(k=9).fuse({"vector": (candidate,)})

    assert fused[0].rrf_score == pytest.approx(0.1)


def test_rrf_deduplicates_same_source_query_occurrence_without_score_stuffing() -> None:
    vector_best = _candidate("chunk-a", source="vector", rank=1, raw_score=0.91)
    vector_duplicate = _candidate(
        "chunk-a", source="vector", rank=3, raw_score=0.81
    )
    keyword = _candidate("chunk-a", source="keyword", rank=2, raw_score=12.0)

    fused = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (vector_duplicate, vector_best),
            "keyword": (keyword,),
        }
    )

    assert len(fused) == 1
    assert [(item.retriever, item.rank) for item in fused[0].provenance] == [
        ("vector", 1),
        ("keyword", 2),
    ]
    assert fused[0].rrf_score == pytest.approx((1 / 61) + (1 / 62))


def test_rrf_ties_have_deterministic_chunk_id_order_independent_of_mapping_order() -> None:
    fusion = retrieval.ReciprocalRankFusion()
    chunk_b = _candidate("chunk-b", source="keyword", rank=1, raw_score=9.0)
    chunk_a = _candidate("chunk-a", source="vector", rank=1, raw_score=0.8)

    first = fusion.fuse({"keyword": (chunk_b,), "vector": (chunk_a,)})
    second = fusion.fuse({"vector": (chunk_a,), "keyword": (chunk_b,)})

    assert [item.chunk_id for item in first] == ["chunk-a", "chunk-b"]
    assert [item.chunk_id for item in second] == ["chunk-a", "chunk-b"]
    assert [item.fused_rank for item in first] == [1, 2]


class _FixedRetriever:
    def __init__(self, candidates: tuple[retrieval.RetrievalCandidate, ...]) -> None:
        self.candidates = candidates

    def retrieve(self, request, *, query, analysis):
        return self.candidates


class _FailingReranker:
    def rerank(self, request, candidates, *, timeout_ms):
        del request, candidates, timeout_ms
        raise RuntimeError("local reranker unavailable")


class _CooperativeTimeoutReranker:
    def __init__(self) -> None:
        self.called_thread = None
        self.timeout_ms = None

    def rerank(self, request, candidates, *, timeout_ms):
        del request, candidates
        self.called_thread = current_thread()
        self.timeout_ms = timeout_ms
        raise retrieval.RerankerTimeoutError("adapter deadline exceeded")


class _PayloadReplacingReranker:
    def rerank(self, request, candidates, *, timeout_ms):
        del request, timeout_ms
        return tuple(
            candidate.model_copy(
                update={
                    "content": "replaced by reranker",
                    "metadata": {},
                    "provenance": (),
                    "rerank_score": float(rank),
                }
            )
            for rank, candidate in enumerate(reversed(candidates), start=1)
        )


class _NonFiniteReranker:
    def rerank(self, request, candidates, *, timeout_ms):
        del request, timeout_ms
        return tuple(
            candidate.model_copy(update={"rerank_score": float("nan")})
            for candidate in candidates
        )


class _SteppingNanosecondClock:
    def __init__(self, *, step_ns: int = 400_000) -> None:
        self._value = -step_ns
        self._step_ns = step_ns

    def __call__(self) -> int:
        self._value += self._step_ns
        return self._value


def _install_resolution_test_clocks(monkeypatch) -> None:
    monkeypatch.setattr(
        reranking_module, "monotonic_ns", lambda: 0, raising=False
    )
    monkeypatch.setattr(
        reranking_module,
        "perf_counter_ns",
        _SteppingNanosecondClock(),
        raising=False,
    )


def _orchestrator(*, reranker, timeout_ms: int) -> retrieval.RetrievalOrchestrator:
    vector = (
        _candidate("chunk-b", source="vector", rank=1, raw_score=0.9),
        _candidate("chunk-a", source="vector", rank=2, raw_score=0.8),
    )
    return retrieval.RetrievalOrchestrator(
        vector_retriever=_FixedRetriever(vector),
        keyword_retriever=_FixedRetriever(()),
        metadata_retriever=_FixedRetriever(()),
        reranker=reranker,
        rerank_timeout_ms=timeout_ms,
    )


def test_reranker_failure_falls_back_exactly_to_rrf_order() -> None:
    result = _orchestrator(reranker=_FailingReranker(), timeout_ms=100).retrieve(
        retrieval.RetrievalRequest(query="fusion query")
    )

    assert result.reranked_candidates == result.fused_candidates
    assert [item.chunk_id for item in result.reranked_candidates] == [
        "chunk-b",
        "chunk-a",
    ]
    assert result.trace.rerank_status == "failed"
    assert result.trace.fallback_reasons == ("reranker_failed",)
    assert result.trace.rerank_elapsed_ms >= 0


def test_reranker_cooperative_timeout_falls_back_without_creating_a_worker() -> None:
    reranker = _CooperativeTimeoutReranker()
    calling_thread = current_thread()
    child_processes_before = {process.pid for process in active_children()}
    started = monotonic()
    result = _orchestrator(reranker=reranker, timeout_ms=1).retrieve(
        retrieval.RetrievalRequest(query="fusion query")
    )
    elapsed = monotonic() - started

    assert result.reranked_candidates == result.fused_candidates
    assert result.trace.rerank_status == "timed_out"
    assert result.trace.fallback_reasons == ("reranker_timeout",)
    assert reranker.called_thread is calling_thread
    assert reranker.timeout_ms == 1
    assert not any(
        thread.name.startswith("rag-reranker") for thread in enumerate_threads()
    )
    assert {process.pid for process in active_children()} == child_processes_before
    assert elapsed < 0.1


def test_reranker_can_change_order_and_score_without_replacing_provenance() -> None:
    result = _orchestrator(
        reranker=_PayloadReplacingReranker(), timeout_ms=100
    ).retrieve(retrieval.RetrievalRequest(query="fusion query"))
    fused_by_id = {
        candidate.chunk_id: candidate for candidate in result.fused_candidates
    }

    assert [item.chunk_id for item in result.reranked_candidates] == [
        "chunk-a",
        "chunk-b",
    ]
    assert [item.rerank_score for item in result.reranked_candidates] == [1.0, 2.0]
    for candidate in result.reranked_candidates:
        fused = fused_by_id[candidate.chunk_id]
        assert candidate.content == fused.content
        assert candidate.metadata == fused.metadata
        assert candidate.provenance == fused.provenance


def test_reranker_non_finite_score_falls_back_to_rrf_order() -> None:
    result = _orchestrator(reranker=_NonFiniteReranker(), timeout_ms=100).retrieve(
        retrieval.RetrievalRequest(query="fusion query")
    )

    assert result.reranked_candidates == result.fused_candidates
    assert result.trace.rerank_status == "failed"
    assert result.trace.fallback_reasons == ("reranker_failed",)


def test_local_heuristic_reranker_is_deterministic_and_query_aware() -> None:
    candidates = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (
                _candidate(
                    "chunk-a",
                    source="vector",
                    rank=1,
                    raw_score=0.9,
                    content="unrelated material",
                ),
            ),
            "keyword": (
                _candidate(
                    "chunk-b",
                    source="keyword",
                    rank=1,
                    raw_score=8.0,
                    content="alpha evidence",
                ),
            ),
        }
    )
    request = retrieval.RetrievalRequest(query="alpha")
    reranker = retrieval.HeuristicReranker()

    first = reranker.rerank(request, candidates, timeout_ms=100)
    second = reranker.rerank(request, candidates, timeout_ms=100)

    assert first == second
    assert [item.chunk_id for item in first] == ["chunk-b", "chunk-a"]
    assert [item.reranked_rank for item in first] == [1, 2]
    assert first[0].rerank_score > first[1].rerank_score


def test_local_heuristic_reranker_matches_cjk_query_with_character_ngrams() -> None:
    candidates = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (
                _candidate(
                    "unrelated",
                    source="vector",
                    rank=1,
                    raw_score=0.9,
                    content="数据库索引优化指南",
                ),
                _candidate(
                    "relevant",
                    source="vector",
                    rank=2,
                    raw_score=0.8,
                    content="课程介绍机器学习基础算法",
                ),
            )
        }
    )

    reranked = retrieval.HeuristicReranker().rerank(
        retrieval.RetrievalRequest(query="机器学习"),
        candidates,
        timeout_ms=100,
    )

    assert [item.chunk_id for item in reranked] == ["relevant", "unrelated"]
    assert reranked[0].rerank_score > reranked[1].rerank_score


def test_local_heuristic_deadline_falls_back_for_many_long_candidates() -> None:
    long_content = "机器学习基础算法" * 500
    vector = tuple(
        _candidate(
            f"chunk-{rank}",
            source="vector",
            rank=rank,
            raw_score=1.0 / rank,
            document_id=f"doc-{rank}",
            content=f"{long_content}{rank}",
        )
        for rank in range(1, 33)
    )
    result = retrieval.RetrievalOrchestrator(
        vector_retriever=_FixedRetriever(vector),
        keyword_retriever=_FixedRetriever(()),
        metadata_retriever=_FixedRetriever(()),
        reranker=retrieval.HeuristicReranker(),
        rerank_timeout_ms=1,
        context_selector=retrieval.ContextSelector(
            retrieval.ContextSelectionConfig(max_chunks=1)
        ),
    ).retrieve(retrieval.RetrievalRequest(query="机器学习", top_k=100))

    assert result.reranked_candidates == result.fused_candidates
    assert result.trace.rerank_status == "timed_out"
    assert result.trace.fallback_reasons == ("reranker_timeout",)


def test_local_heuristic_short_workload_uses_high_resolution_deadline(
    monkeypatch,
) -> None:
    _install_resolution_test_clocks(monkeypatch)
    candidates = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (
                _candidate(
                    "chunk-a",
                    source="vector",
                    rank=1,
                    raw_score=0.9,
                    content="alpha evidence",
                ),
            )
        }
    )

    with pytest.raises(retrieval.RerankerTimeoutError):
        retrieval.HeuristicReranker().rerank(
            retrieval.RetrievalRequest(query="alpha"),
            candidates,
            timeout_ms=1,
        )


def test_orchestrator_falls_back_on_local_high_resolution_deadline(
    monkeypatch,
) -> None:
    _install_resolution_test_clocks(monkeypatch)
    vector = (
        _candidate(
            "chunk-a",
            source="vector",
            rank=1,
            raw_score=0.9,
            content="alpha evidence",
        ),
    )
    result = retrieval.RetrievalOrchestrator(
        vector_retriever=_FixedRetriever(vector),
        keyword_retriever=_FixedRetriever(()),
        metadata_retriever=_FixedRetriever(()),
        reranker=retrieval.HeuristicReranker(),
        rerank_timeout_ms=1,
    ).retrieve(retrieval.RetrievalRequest(query="alpha"))

    assert result.reranked_candidates == result.fused_candidates
    assert result.trace.rerank_status == "timed_out"
    assert result.trace.fallback_reasons == ("reranker_timeout",)


def test_local_noop_reranker_preserves_rrf_order() -> None:
    candidates = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (
                _candidate("chunk-b", source="vector", rank=1, raw_score=0.9),
                _candidate("chunk-a", source="vector", rank=2, raw_score=0.8),
            )
        }
    )

    reranked = retrieval.NoOpReranker().rerank(
        retrieval.RetrievalRequest(query="fusion query"),
        candidates,
        timeout_ms=100,
    )

    assert [item.chunk_id for item in reranked] == ["chunk-b", "chunk-a"]
    assert [item.reranked_rank for item in reranked] == [1, 2]


def test_local_noop_reranker_checks_deadline_while_copying_candidates() -> None:
    candidate = retrieval.ReciprocalRankFusion().fuse(
        {
            "vector": (
                _candidate("chunk-a", source="vector", rank=1, raw_score=0.9),
            )
        }
    )[0]

    with pytest.raises(retrieval.RerankerTimeoutError):
        retrieval.NoOpReranker().rerank(
            retrieval.RetrievalRequest(query="fusion query"),
            (candidate,) * 10_000,
            timeout_ms=1,
        )


def _fused_candidates(
    specs: tuple[tuple[str, str, str, dict], ...]
) -> tuple[retrieval.FusedCandidate, ...]:
    raw = tuple(
        _candidate(
            chunk_id,
            source="vector",
            rank=rank,
            raw_score=1.0 / rank,
            document_id=document_id,
            content=content,
            metadata=metadata,
        )
        for rank, (chunk_id, document_id, content, metadata) in enumerate(
            specs, start=1
        )
    )
    return retrieval.ReciprocalRankFusion().fuse({"vector": raw})


def test_context_selector_prioritizes_distinct_documents_before_second_passages() -> None:
    candidates = _fused_candidates(
        (
            ("a-1", "doc-a", "first A passage", {"chunk_index": 1}),
            ("a-2", "doc-a", "second A passage", {"chunk_index": 3}),
            ("b-1", "doc-b", "first B passage", {"chunk_index": 1}),
            ("c-1", "doc-c", "first C passage", {"chunk_index": 1}),
        )
    )
    selector = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(max_chunks=4, char_budget=1_000)
    )

    selected = selector.select(candidates)

    assert [item.chunk_id for item in selected] == ["a-1", "b-1", "c-1", "a-2"]


def test_context_selector_rejects_duplicate_and_neighbor_chunks_by_default() -> None:
    candidates = _fused_candidates(
        (
            (
                "chunk-1",
                "doc-a",
                "primary passage",
                {"chunk_index": 1, "next_chunk_id": "chunk-2"},
            ),
            ("duplicate", "doc-b", "primary passage", {"chunk_index": 1}),
            (
                "chunk-2",
                "doc-a",
                "adjacent but distinct passage",
                {"chunk_index": 2, "previous_chunk_id": "chunk-1"},
            ),
            ("independent", "doc-c", "independent passage", {"chunk_index": 1}),
        )
    )

    selected = retrieval.ContextSelector().select(candidates)
    neighbor_enabled = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(allow_neighbor_chunks=True)
    ).select(candidates)

    assert [item.chunk_id for item in selected] == ["chunk-1", "independent"]
    assert [item.chunk_id for item in neighbor_enabled] == [
        "chunk-1",
        "independent",
        "chunk-2",
    ]


def test_context_selector_overlap_control_filters_repetitive_passages() -> None:
    common = "shared boundary text " * 8
    candidates = _fused_candidates(
        (
            ("left", "doc-a", f"unique left {common}", {}),
            ("right", "doc-b", f"{common}unique right", {}),
        )
    )

    filtered = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(max_overlap_ratio=0.6)
    ).select(candidates)
    unfiltered = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(max_overlap_ratio=None)
    ).select(candidates)

    assert [item.chunk_id for item in filtered] == ["left"]
    assert [item.chunk_id for item in unfiltered] == ["left", "right"]


def test_context_selector_zero_overlap_limit_keeps_non_overlapping_passages() -> None:
    candidates = _fused_candidates(
        (
            ("left", "doc-a", "alpha passage", {}),
            ("right", "doc-b", "beta evidence", {}),
        )
    )

    selected = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(max_overlap_ratio=0.0)
    ).select(candidates)

    assert [item.chunk_id for item in selected] == ["left", "right"]


def test_context_selector_counts_separators_and_never_exceeds_budget() -> None:
    candidates = _fused_candidates(
        (
            ("one", "doc-a", "1234", {}),
            ("too-large", "doc-b", "x" * 11, {}),
            ("two", "doc-c", "5678", {}),
            ("no-room", "doc-d", "9", {}),
        )
    )
    selector = retrieval.ContextSelector(
        retrieval.ContextSelectionConfig(
            max_chunks=5,
            char_budget=10,
            separator="\n\n",
            max_overlap_ratio=None,
        )
    )

    selected = selector.select(candidates)

    assert [item.chunk_id for item in selected] == ["one", "two"]
    assert selector.context_char_count(selected) == 10


def test_context_selector_default_budget_is_six_thousand_characters() -> None:
    candidates = _fused_candidates(
        (
            ("almost-full", "doc-a", "a" * 5_999, {}),
            ("no-room", "doc-b", "b", {}),
        )
    )
    selector = retrieval.ContextSelector()

    selected = selector.select(candidates)

    assert [item.chunk_id for item in selected] == ["almost-full"]
    assert selector.context_char_count(selected) == 5_999


def test_orchestrator_selects_default_five_and_traces_every_stage() -> None:
    vector = tuple(
        _candidate(
            f"chunk-{rank}",
            source="vector",
            rank=rank,
            raw_score=1.0 / rank,
            document_id=f"doc-{rank}",
            content=f"evidence {rank}",
        )
        for rank in range(1, 7)
    )
    result = retrieval.RetrievalOrchestrator(
        vector_retriever=_FixedRetriever(vector),
        keyword_retriever=_FixedRetriever(()),
        metadata_retriever=_FixedRetriever(()),
        reranker=retrieval.NoOpReranker(),
    ).retrieve(retrieval.RetrievalRequest(query="evidence", top_k=10))

    assert len(result.selected_candidates) == 5
    assert result.trace.fused_candidates == result.fused_candidates
    assert result.trace.reranked_candidates == result.reranked_candidates
    assert result.trace.selected_candidates == result.selected_candidates
    assert result.trace.selection_elapsed_ms >= 0
    assert result.trace.selected_char_count == sum(
        len(item.content) for item in result.selected_candidates
    ) + 2 * (len(result.selected_candidates) - 1)
    assert result.trace.selected_candidates[0].provenance[0].raw_score == 1.0
