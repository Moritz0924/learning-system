from __future__ import annotations

from backend.app.services.token_counting import TiktokenTokenCounter
from evals.chunking_v3 import EvidenceAnchor, ChunkingQuery, RetrievedChunk, score_ranked_chunks


def _anchor(anchor_id: str, text: str) -> EvidenceAnchor:
    return EvidenceAnchor.create(
        anchor_id=anchor_id,
        document_id="doc-1",
        text=text,
        source_locator=f"doc-1:{anchor_id}",
    )


def _query(*anchor_ids: str) -> ChunkingQuery:
    return ChunkingQuery("query-1", "doc-1", "test", "question", tuple(anchor_ids))


def test_context_density_uses_the_production_token_counter_for_chinese_evidence() -> None:
    anchor = _anchor("anchor-1", "中文检索证据必须使用真实分词器计数")
    counter = TiktokenTokenCounter()
    result = score_ranked_chunks(
        query=_query("anchor-1"),
        ranked=[RetrievedChunk("chunk-1", "doc-1", anchor.normalized_text, 20, ("anchor-1",))],
        anchors_by_id={anchor.anchor_id: anchor},
        token_counter=counter,
    )

    assert counter.count(anchor.normalized_text) > len(anchor.normalized_text.split())
    assert result["fixed_k"]["1"]["context_density"] == counter.count(anchor.normalized_text) / 20


def test_repeated_evidence_only_counts_once_for_recall_density_and_ndcg() -> None:
    anchor = _anchor("anchor-1", "one gold evidence")
    result = score_ranked_chunks(
        query=_query("anchor-1"),
        ranked=[
            RetrievedChunk("chunk-1", "doc-1", "one gold evidence", 10, ("anchor-1",)),
            RetrievedChunk("chunk-2", "doc-1", "one gold evidence repeated", 10, ("anchor-1",)),
        ],
        anchors_by_id={anchor.anchor_id: anchor},
        cutoffs=(2,),
        token_budgets=(20,),
        token_counter=TiktokenTokenCounter(),
    )

    metrics = result["fixed_k"]["2"]
    assert metrics["evidence_recall"] == 1.0
    assert metrics["evidence_ndcg"] == 1.0
    assert metrics["context_density"] == TiktokenTokenCounter().count(anchor.normalized_text) / 20


def test_hit_at_k_is_binary_for_any_gold_evidence() -> None:
    anchor = _anchor("anchor-1", "one gold evidence")
    missed = score_ranked_chunks(
        query=_query(anchor.anchor_id),
        ranked=[RetrievedChunk("chunk-1", "doc-1", "miss", 10, ())],
        anchors_by_id={anchor.anchor_id: anchor},
    )
    hit = score_ranked_chunks(
        query=_query(anchor.anchor_id),
        ranked=[RetrievedChunk("chunk-1", "doc-1", "hit", 10, (anchor.anchor_id,))],
        anchors_by_id={anchor.anchor_id: anchor},
    )

    assert missed["fixed_k"]["1"]["hit"] == 0.0
    assert hit["fixed_k"]["1"]["hit"] == 1.0


def test_evidence_ndcg_rewards_each_anchor_at_its_first_hit_only_and_is_bounded() -> None:
    first = _anchor("anchor-1", "first evidence")
    second = _anchor("anchor-2", "second evidence")
    result = score_ranked_chunks(
        query=_query(first.anchor_id, second.anchor_id),
        ranked=[
            RetrievedChunk("chunk-1", "doc-1", "first", 5, (first.anchor_id,)),
            RetrievedChunk("chunk-2", "doc-1", "first again", 5, (first.anchor_id,)),
            RetrievedChunk("chunk-3", "doc-1", "second", 5, (second.anchor_id,)),
        ],
        anchors_by_id={first.anchor_id: first, second.anchor_id: second},
        cutoffs=(3,),
        token_budgets=(15,),
    )

    expected = (1.0 + 1.0 / __import__("math").log2(4)) / (1.0 + 1.0 / __import__("math").log2(3))
    assert result["fixed_k"]["3"]["evidence_ndcg"] == expected
    assert 0.0 <= result["fixed_k"]["3"]["evidence_ndcg"] <= 1.0


def test_fixed_token_budget_never_accepts_an_oversized_first_chunk() -> None:
    anchor = _anchor("anchor-1", "gold evidence")
    result = score_ranked_chunks(
        query=_query(anchor.anchor_id),
        ranked=[RetrievedChunk("chunk-1", "doc-1", "gold evidence", 513, (anchor.anchor_id,))],
        anchors_by_id={anchor.anchor_id: anchor},
        token_budgets=(512,),
    )

    assert result["fixed_token_budget"]["512"]["retrieved_tokens"] == 0.0
    assert result["fixed_token_budget"]["512"]["evidence_recall"] == 0.0
