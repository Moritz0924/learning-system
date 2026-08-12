from __future__ import annotations

from backend.app.domain.rag.chunking.v3.domain import (
    BoundaryStrength,
    StructuralRegion,
    StructuralUnit,
)
from backend.app.domain.rag.chunking.v3.semantic import SemanticChunker
from backend.app.domain.rag.chunking.v3.threshold import AdaptiveThresholdPolicy
from backend.app.domain.rag.chunking.v3.config import SemanticChunkPolicy
from backend.app.services.document_parsing.models import DocumentBlockType


class FakeSemanticEncoder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors[text] for text in texts]


def _region(texts: list[str]) -> StructuralRegion:
    units = tuple(
        StructuralUnit(
            unit_id=f"u{i}", text=text, block_type=DocumentBlockType.PARAGRAPH,
            page_number=1, block_index=i, heading_path=("RAG",), bbox=None,
            reading_order=i, structure_confidence=1.0,
        )
        for i, text in enumerate(texts, start=1)
    )
    return StructuralRegion(
        region_id="region-1", heading_path=("RAG",), units=units,
        region_type="text", boundary_before=BoundaryStrength.SOFT,
        boundary_after=BoundaryStrength.SOFT,
    )


def test_semantic_chunker_batches_units_and_detects_topic_boundary() -> None:
    encoder = FakeSemanticEncoder({
        "a": [1, 0], "b": [1, 0], "c": [0, 1], "d": [0, 1],
        "e": [0, 1], "f": [0, 1],
    })
    chunker = SemanticChunker(
        encoder=encoder,
        policy=SemanticChunkPolicy(min_boundary_samples=2),
    )

    segments = chunker.split(_region(["a", "b", "c", "d", "e", "f"]))

    assert encoder.calls == [["a", "b", "c", "d", "e", "f"]]
    assert len(segments) == 2
    assert [unit.text for unit in segments[0].units] == ["a", "b"]
    assert segments[0].boundaries


def test_short_region_does_not_make_unstable_semantic_split() -> None:
    encoder = FakeSemanticEncoder({"a": [1, 0], "b": [0, 1]})
    chunker = SemanticChunker(
        encoder=encoder,
        policy=SemanticChunkPolicy(min_boundary_samples=5),
    )

    segments = chunker.split(_region(["a", "b"]))

    assert len(segments) == 1
    assert segments[0].boundaries[0].adaptive_threshold is None
    assert segments[0].boundaries[0].selected is False


def test_zero_mad_does_not_mass_split() -> None:
    encoder = FakeSemanticEncoder({text: [1, 0] for text in "abcdef"})
    chunker = SemanticChunker(
        encoder=encoder,
        policy=SemanticChunkPolicy(min_boundary_samples=2),
    )

    segments = chunker.split(_region(list("abcdef")))

    assert len(segments) == 1


def test_semantic_unit_resource_guard_rejects_unbounded_document() -> None:
    encoder = FakeSemanticEncoder({str(i): [1, 0] for i in range(3)})
    chunker = SemanticChunker(
        encoder=encoder,
        policy=SemanticChunkPolicy(max_semantic_units=2),
    )

    import pytest
    with pytest.raises(ValueError, match="maximum semantic units"):
        chunker.split(_region(["0", "1", "2"]))


def test_semantic_embedding_calls_are_bounded_batches() -> None:
    texts = [str(index) for index in range(130)]
    encoder = FakeSemanticEncoder({text: [1, 0] for text in texts})
    chunker = SemanticChunker(
        encoder=encoder,
        policy=SemanticChunkPolicy(min_boundary_samples=200, max_semantic_units=200),
        batch_size=64,
    )

    chunker.split(_region(texts))

    assert [len(call) for call in encoder.calls] == [64, 64, 2]
