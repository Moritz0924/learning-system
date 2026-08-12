from __future__ import annotations

from evals.chunking_v3 import (
    EvidenceAnchor,
    RetrievedChunk,
    canonical_gold_hash,
    map_chunk_to_anchors,
    paired_bootstrap,
    score_ranked_chunks,
    validate_document_split,
    ChunkingDocument,
    ChunkingQuery,
)
from evals.chunking_v3_dataset import build_fixture_bundle


def test_gold_mapping_does_not_require_v3_unit_ids() -> None:
    anchor = EvidenceAnchor.create(
        anchor_id="anchor-1", document_id="doc-1", text="canonical evidence",
        source_locator="doc-1:source:1",
    )

    covered = map_chunk_to_anchors(
        document_id="doc-1",
        content="A rendered chunk contains canonical evidence.",
        metadata={"source_unit_ids": ["generated-by-v3"]},
        anchors=[anchor],
    )

    assert covered == ("anchor-1",)


def test_fixed_k_and_fixed_budget_metrics_are_reported() -> None:
    anchor = EvidenceAnchor.create(
        anchor_id="anchor-1", document_id="doc-1", text="gold evidence",
        source_locator="doc-1:source:1",
    )
    query = ChunkingQuery("q-1", "doc-1", "test", "question", ("anchor-1",))
    ranked = [RetrievedChunk("c1", "doc-1", "gold evidence", 3, ("anchor-1",))]

    result = score_ranked_chunks(query=query, ranked=ranked, anchors_by_id={"anchor-1": anchor})

    assert result["fixed_k"]["1"]["evidence_recall"] == 1.0
    assert result["fixed_token_budget"]["512"]["context_density"] > 0


def test_document_level_split_rejects_query_leakage() -> None:
    documents = [ChunkingDocument("doc-1", "one.md", "One", "markdown", "a" * 64)]
    queries = [ChunkingQuery("q-1", "doc-1", "test", "question", ())]

    errors = validate_document_split(documents, queries)

    assert errors == ["query q-1 crosses document split"]


def test_paired_bootstrap_is_seeded_and_reports_confidence_interval() -> None:
    result = paired_bootstrap([0.1, 0.2, 0.3], resamples=1000, seed=42)

    assert result["resamples"] == 1000
    assert result["ci95_low"] <= result["mean_delta"] <= result["ci95_high"]


def test_gold_hash_is_stable() -> None:
    anchor = EvidenceAnchor.create(
        anchor_id="anchor-1", document_id="doc-1", text="evidence",
        source_locator="doc-1:source:1",
    )
    assert canonical_gold_hash([anchor], {"doc-1": (("u1", "u2"),)}) == canonical_gold_hash(
        [anchor], {"doc-1": (("u1", "u2"),)}
    )


def test_fixture_uses_document_level_dev_test_split_and_fixed_source_distribution() -> None:
    bundle = build_fixture_bundle()
    documents = bundle.dataset.documents
    assert len(documents) == 30
    assert sum(document.split == "development" for document in documents) == 20
    assert sum(document.split == "test" for document in documents) == 10
    assert {source_type: sum(d.source_type == source_type for d in documents) for source_type in ("markdown", "pdf", "pptx", "text")} == {
        "markdown": 10, "pdf": 10, "pptx": 5, "text": 5,
    }
