from __future__ import annotations

import pytest

from backend.app.domain.rag.chunking.v3.config import SizeGuardPolicy
from backend.app.domain.rag.chunking.v3.domain import SemanticSegment, SemanticUnit
from backend.app.domain.rag.chunking.v3.size_guard import SizeGuard
from backend.app.services.document_parsing.models import DocumentBlockType


class FakeTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split()) if " " in text else len(text)


def _unit(text: str, *, kind: DocumentBlockType = DocumentBlockType.PARAGRAPH, order: int = 1) -> SemanticUnit:
    return SemanticUnit(
        unit_id=f"u{order}", text=text, source_unit_ids=(f"u{order}",),
        page_start=1, page_end=1, heading_path=("RAG",),
        block_type=kind, order=order,
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
