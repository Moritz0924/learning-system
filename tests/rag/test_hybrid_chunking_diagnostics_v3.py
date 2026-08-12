from __future__ import annotations


class _Counter:
    def count(self, text: str) -> int:
        return len(text.split())


class _Encoder:
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if index == 0 else [0.0, 1.0] for index, _ in enumerate(texts)]


class _FixedThreshold:
    def threshold(self, scores) -> float:
        return 0.2

    def select(self, score: float, threshold: float | None) -> bool:
        return threshold is not None and score > threshold


def test_pipeline_returns_diagnostics_without_full_semantic_trace_in_chunk_metadata() -> None:
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy, SemanticChunkPolicy, SizeGuardPolicy
    from backend.app.domain.rag.chunking.v3.pipeline import HybridChunkingPipeline
    from backend.app.domain.rag.chunking.v3.relations import AdjacentRelationChecker
    from backend.app.domain.rag.chunking.v3.semantic import SemanticChunker
    from backend.app.domain.rag.chunking.v3.size_guard import SizeGuard
    from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
    from backend.app.services.document_parsing.models import (
        DocumentBlock,
        DocumentBlockType,
        DocumentFileType,
        ProcessingMode,
        SourceElementType,
    )

    blocks = [
        DocumentBlock(
            filename="diagnostics.md",
            file_type=DocumentFileType.TEXT,
            page_number=1,
            block_index=1,
            text="First sentence. Second sentence. Third sentence.",
            processing_mode=ProcessingMode.TEXT_NATIVE,
            source_element=SourceElementType.TEXT_FILE,
            block_type=DocumentBlockType.PARAGRAPH,
            reading_order=1,
            structure_confidence=1.0,
            source_format="markdown",
        )
    ]
    policy = HybridChunkPolicy(
        semantic=SemanticChunkPolicy(min_boundary_samples=2),
        size=SizeGuardPolicy(min_tokens=1, target_tokens=100, max_tokens=200),
    )
    pipeline = HybridChunkingPipeline(
        structure_chunker=StructureAwareChunker(),
        semantic_chunker=SemanticChunker(
            encoder=_Encoder(),
            relation_checker=AdjacentRelationChecker(),
            threshold_policy=_FixedThreshold(),
            policy=policy.semantic,
        ),
        size_guard=SizeGuard(token_counter=_Counter(), policy=policy.size),
        policy=policy,
    )

    result = pipeline.chunk(blocks, document_id="doc-diagnostics")

    assert result.diagnostics.semantic_regions == 1
    assert result.diagnostics.candidate_boundaries == 2
    assert result.diagnostics.selected_boundaries >= 1
    assert result.chunks
    assert all("semantic_trace" not in chunk.metadata for chunk in result.chunks)
    assert all(len(chunk.metadata["semantic"]["boundaries"]) <= 1 for chunk in result.chunks)
