from __future__ import annotations

import pytest

from backend.app.domain.rag.chunking.v3.config import SizeGuardPolicy
from backend.app.domain.rag.chunking.v3.domain import SemanticBoundary, SemanticSegment, SemanticUnit
from backend.app.domain.rag.chunking.v3.size_guard import SizeGuard
from backend.app.services.document_parsing.models import DocumentBlockType


class FakeTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split()) if " " in text else len(text)


class CharacterTokenCounter:
    def count(self, text: str) -> int:
        return len(text)


def _unit(text: str, *, kind: DocumentBlockType = DocumentBlockType.PARAGRAPH, order: int = 1) -> SemanticUnit:
    return SemanticUnit(
        unit_id=f"u{order}", text=text, source_unit_ids=(f"u{order}",),
        page_start=1, page_end=1, heading_path=("RAG",),
        block_type=kind, order=order,
    )


def _boundary(*, score: float, left: str, right: str) -> SemanticBoundary:
    return SemanticBoundary(
        left_unit_id=left,
        right_unit_id=right,
        local_similarity=0.0,
        adjacent_similarity=0.0,
        continuation_score=0.0,
        continuation_reasons=(),
        boundary_score=score,
        adaptive_threshold=None,
        selected=True,
    )


def test_heading_rendering_is_counted_before_accepting_chunk() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=5, max_tokens=5),
    )

    chunks = guard.apply([SemanticSegment(units=(_unit("one two three"),))])

    assert len(chunks) == 1
    assert chunks[0].metadata["size_guard"]["token_count"] <= 5
    assert "RAG" in chunks[0].content


def test_oversize_text_is_split_and_final_invariant_holds() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=4, max_tokens=5),
    )

    chunks = guard.apply([SemanticSegment(units=(_unit("one two three four five six seven"),))])

    assert len(chunks) > 1
    assert all(chunk.metadata["size_guard"]["token_count"] <= 5 for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 5 for chunk in chunks)
    assert any(chunk.metadata["size_guard"]["action"] == "hard_fallback" for chunk in chunks)


def test_fenced_code_rebuilds_fence_for_each_final_chunk() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=4, max_tokens=5),
    )
    code = "```python\n" + "\n".join(f"line {index}" for index in range(8)) + "\n```"

    chunks = guard.apply([SemanticSegment(units=(_unit(code, kind=DocumentBlockType.CODE),))])

    assert len(chunks) > 1
    assert all(chunk.content.startswith("```python") and chunk.content.endswith("```") for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 5 for chunk in chunks)


def test_table_split_repeats_header_and_counts_header_tokens() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=15, max_tokens=20),
    )
    table = "| metric | score |\n| --- | --- |\n" + "\n".join(
        f"| row{index} | value{index} |" for index in range(6)
    )

    chunks = guard.apply([SemanticSegment(units=(_unit(table, kind=DocumentBlockType.TABLE),))])

    assert len(chunks) > 1
    assert all("| metric | score |" in chunk.content for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 20 for chunk in chunks)


def test_oversize_single_line_uses_token_safe_fallback() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=3, max_tokens=4),
    )

    chunks = guard.apply([SemanticSegment(units=(_unit("中" * 20),))])

    assert len(chunks) > 1
    assert all(guard.token_counter.count(chunk.content) <= 4 for chunk in chunks)


@pytest.mark.parametrize(
    ("left_score", "right_score", "expected"),
    [(0.12, 0.41, ("alpha", "tiny")), (0.50, 0.10, ("tiny", "charlie"))],
)
def test_tiny_middle_segment_merges_across_weaker_boundary(
    left_score: float,
    right_score: float,
    expected: tuple[str, str],
) -> None:
    guard = SizeGuard(
        token_counter=CharacterTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=10, target_tokens=30, max_tokens=100),
    )
    left = _unit("alpha beta gamma", order=1)
    middle = _unit("tiny", order=2)
    right = _unit("charlie delta echo", order=3)
    left_boundary = _boundary(score=left_score, left=left.unit_id, right=middle.unit_id)
    right_boundary = _boundary(score=right_score, left=middle.unit_id, right=right.unit_id)

    chunks = guard.apply([
        SemanticSegment(units=(left,), boundary_after=left_boundary),
        SemanticSegment(units=(middle,), boundary_before=left_boundary, boundary_after=right_boundary),
        SemanticSegment(units=(right,), boundary_before=right_boundary),
    ])

    assert len(chunks) == 2
    assert any(all(text in chunk.content for text in expected) for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 100 for chunk in chunks)


def test_tiny_merge_cannot_cross_a_hard_structure_region() -> None:
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy, SemanticChunkPolicy
    from backend.app.domain.rag.chunking.v3.pipeline import HybridChunkingPipeline
    from backend.app.domain.rag.chunking.v3.semantic import SemanticChunker
    from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
    from backend.app.services.document_parsing.models import (
        DocumentBlock,
        DocumentFileType,
        ProcessingMode,
        SourceElementType,
    )

    class Counter:
        def count(self, text: str) -> int:
            return len(text.split())

    class Encoder:
        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    def block(text: str, *, index: int, kind: DocumentBlockType, level: int | None = None) -> DocumentBlock:
        return DocumentBlock(
            filename="regions.md",
            file_type=DocumentFileType.TEXT,
            page_number=1,
            block_index=index,
            text=text,
            processing_mode=ProcessingMode.TEXT_NATIVE,
            source_element=SourceElementType.TEXT_FILE,
            block_type=kind,
            heading_level=level,
            reading_order=index,
            structure_confidence=1.0,
            source_format="markdown",
        )

    policy = HybridChunkPolicy(
        semantic=SemanticChunkPolicy(min_boundary_samples=2),
        size=SizeGuardPolicy(min_tokens=10, target_tokens=20, max_tokens=50),
    )
    pipeline = HybridChunkingPipeline(
        structure_chunker=StructureAwareChunker(),
        semantic_chunker=SemanticChunker(encoder=Encoder(), policy=policy.semantic),
        size_guard=SizeGuard(token_counter=Counter(), policy=policy.size),
        policy=policy,
    )

    result = pipeline.chunk([
        block("First", index=1, kind=DocumentBlockType.HEADING, level=1),
        block("left", index=2, kind=DocumentBlockType.PARAGRAPH),
        block("Second", index=3, kind=DocumentBlockType.HEADING, level=1),
        block("right", index=4, kind=DocumentBlockType.PARAGRAPH),
    ], document_id="hard-region")

    assert len(result.chunks) == 2
    assert {tuple(chunk.source_unit_ids) for chunk in result.chunks} == {("unit-1-1",), ("unit-2-1",)}
    assert all("tiny_merge" != chunk.metadata["size_guard"]["action"] for chunk in result.chunks)


@pytest.mark.parametrize(
    "table",
    [
        "| metric | score |\n| --- | --- |\n| " + ("row " * 30) + "| value |",
        "| metric | score |\n| --- | --- |\n| row | " + ("value" * 80) + " |",
    ],
)
def test_oversized_table_rows_and_cells_remain_valid_tables(table: str) -> None:
    guard = SizeGuard(
        token_counter=CharacterTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=45, max_tokens=50),
    )

    chunks = guard.apply([SemanticSegment(units=(_unit(table, kind=DocumentBlockType.TABLE),))])

    assert len(chunks) > 1
    assert all(chunk.chunk_type == DocumentBlockType.TABLE.value for chunk in chunks)
    assert all(chunk.content.startswith("| metric | score |\n| --- | --- |\n|") for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 50 for chunk in chunks)


def test_giant_fenced_code_line_retains_a_complete_fence() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=13, max_tokens=16),
    )
    code = "```python\n" + ("x" * 80) + "\n```"

    chunks = guard.apply([SemanticSegment(units=(_unit(code, kind=DocumentBlockType.CODE),))])

    assert len(chunks) > 1
    assert all(chunk.chunk_type == DocumentBlockType.CODE.value for chunk in chunks)
    assert all(chunk.content.startswith("```python\n") and chunk.content.endswith("\n```") for chunk in chunks)
    assert all(guard.token_counter.count(chunk.content) <= 16 for chunk in chunks)


def test_code_prefers_function_and_blank_line_boundaries_before_line_fragments() -> None:
    guard = SizeGuard(
        token_counter=FakeTokenCounter(),
        policy=SizeGuardPolicy(min_tokens=1, target_tokens=6, max_tokens=7),
    )
    code = """```python
def first():
    return 1

def second():
    return 2
```"""

    chunks = guard.apply([SemanticSegment(units=(_unit(code, kind=DocumentBlockType.CODE),))])

    assert len(chunks) == 2
    assert "def first" in chunks[0].content
    assert "def second" in chunks[1].content
    assert all(chunk.content.startswith("```python\n") and chunk.content.endswith("\n```") for chunk in chunks)
