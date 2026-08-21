from __future__ import annotations

from backend.app.domain.rag.chunking.v3.config import (
    CHUNKING_ALGORITHM_VERSIONS,
    HybridChunkPolicy,
    policy_fingerprint,
)
from backend.app.domain.rag.chunking.v3.domain import SemanticUnit
from backend.app.domain.rag.chunking.v3.renderer import ChunkRenderer
from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
from backend.app.services.document_parsing.models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    ProcessingMode,
    SourceElementType,
)


def _unit(text: str, *, kind: DocumentBlockType) -> SemanticUnit:
    return SemanticUnit(
        unit_id="unit-1",
        text=text,
        source_unit_ids=("unit-1",),
        page_start=1,
        page_end=1,
        heading_path=("Guide",),
        block_type=kind,
        order=1,
    )


def _block(
    text: str,
    *,
    page: int,
    index: int,
    kind: DocumentBlockType,
) -> DocumentBlock:
    return DocumentBlock(
        filename="lesson.pptx",
        file_type=DocumentFileType.PPTX,
        page_number=page,
        block_index=index,
        text=text,
        processing_mode=ProcessingMode.PPT_NATIVE_TEXT,
        source_element=SourceElementType.PPT_TEXT_SHAPES,
        block_type=kind,
        reading_order=index,
        structure_confidence=1.0,
    )


def test_renderer_applies_heading_context_only_to_text() -> None:
    renderer = ChunkRenderer(include_heading_context=False)

    assert renderer.render((_unit("Body", kind=DocumentBlockType.PARAGRAPH),)) == "Body"
    assert renderer.render((_unit("```python\npass\n```", kind=DocumentBlockType.CODE),)) == "```python\npass\n```"
    assert renderer.render((_unit("| a |\n| --- |\n| 1 |", kind=DocumentBlockType.TABLE),)) == "| a |\n| --- |\n| 1 |"


def test_slide_title_is_context_not_a_text_unit_and_slide_change_is_soft() -> None:
    regions = StructureAwareChunker().build_regions([
        _block("First slide", page=1, index=1, kind=DocumentBlockType.SLIDE_TITLE),
        _block("First body", page=1, index=2, kind=DocumentBlockType.SLIDE_BODY),
        _block("Second slide", page=2, index=3, kind=DocumentBlockType.SLIDE_TITLE),
        _block("Second body", page=2, index=4, kind=DocumentBlockType.SLIDE_BODY),
    ])

    assert [[unit.text for unit in region.units] for region in regions] == [["First body"], ["Second body"]]
    assert [region.heading_path for region in regions] == [("First slide",), ("Second slide",)]
    assert regions[0].boundary_after.value == "soft"
    assert regions[1].boundary_before.value == "soft"
    assert regions[0].units[0].metadata["slide_context"] == "First slide"


def test_ppt_slide_without_title_does_not_inherit_previous_slide_context() -> None:
    regions = StructureAwareChunker().build_regions([
        _block("First slide", page=1, index=1, kind=DocumentBlockType.SLIDE_TITLE),
        _block("First body", page=1, index=2, kind=DocumentBlockType.SLIDE_BODY),
        _block("Second body", page=2, index=3, kind=DocumentBlockType.SLIDE_BODY),
    ])

    assert [region.heading_path for region in regions] == [("First slide",), ()]
    assert regions[0].boundary_after.value == "soft"
    assert regions[1].boundary_before.value == "soft"


def test_algorithm_fingerprint_includes_all_frozen_algorithm_versions(monkeypatch) -> None:
    policy = HybridChunkPolicy()
    baseline = policy_fingerprint(policy, tokenizer_id="cl100k_base")

    assert CHUNKING_ALGORITHM_VERSIONS == {
        "structure": "structure-v3.1",
        "semantic": "semantic-v3.1",
        "sentence_splitter": "sentence-v3.1",
        "relations": "relations-v3.1",
        "renderer": "renderer-v3.1",
        "size_guard": "size-v3.1",
        "table_splitter": "table-v3.1",
        "code_splitter": "code-v3.1",
    }
    monkeypatch.setitem(CHUNKING_ALGORITHM_VERSIONS, "renderer", "renderer-v3.2")

    assert policy_fingerprint(policy, tokenizer_id="cl100k_base") != baseline
