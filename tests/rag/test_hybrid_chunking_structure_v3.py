from __future__ import annotations

from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
from backend.app.services.document_parsing.models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    ProcessingMode,
    SourceElementType,
)


def _block(
    text: str,
    *,
    index: int,
    page: int = 1,
    block_type: DocumentBlockType = DocumentBlockType.PARAGRAPH,
    heading_level: int | None = None,
) -> DocumentBlock:
    return DocumentBlock(
        filename="guide.md",
        file_type=DocumentFileType.PDF,
        page_number=page,
        block_index=index,
        text=text,
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
        block_type=block_type,
        heading_level=heading_level,
        reading_order=index,
        structure_confidence=1.0,
    )


def test_structure_layer_builds_heading_stack_without_heading_only_units() -> None:
    blocks = [
        _block("RAG", index=1, block_type=DocumentBlockType.HEADING, heading_level=1),
        _block("Retrieval", index=2, block_type=DocumentBlockType.HEADING, heading_level=2),
        _block("Vector search retrieves evidence.", index=3),
        _block("Grounding uses the evidence.", index=4),
    ]

    regions = StructureAwareChunker().build_regions(blocks)

    assert len(regions) == 1
    assert [unit.text for unit in regions[0].units] == [
        "Vector search retrieves evidence.",
        "Grounding uses the evidence.",
    ]
    assert regions[0].heading_path == ("RAG", "Retrieval")
    assert regions[0].units[0].heading_path == ("RAG", "Retrieval")


def test_heading_code_and_table_are_hard_region_boundaries() -> None:
    blocks = [
        _block("Before", index=1),
        _block("print(x)", index=2, block_type=DocumentBlockType.CODE),
        _block("After", index=3),
        _block("| a | b |\n| --- | --- |\n| 1 | 2 |", index=4, block_type=DocumentBlockType.TABLE),
        _block("Tail", index=5),
    ]

    regions = StructureAwareChunker().build_regions(blocks)

    assert [region.region_type for region in regions] == ["text", "code", "text", "table", "text"]
    assert regions[1].boundary_before.value == "hard"
    assert regions[3].boundary_before.value == "hard"
    assert all(region.region_type not in {"code", "table"} or len(region.units) == 1 for region in regions)


def test_page_change_is_soft_and_same_section_can_cross_pages() -> None:
    blocks = [
        _block("First half", index=1, page=10),
        _block("Second half", index=2, page=11),
    ]

    regions = StructureAwareChunker().build_regions(blocks)

    assert len(regions) == 1
    assert regions[0].boundary_before.value == "soft"
    assert regions[0].units[0].page_number == 10
    assert regions[0].units[1].page_number == 11


def test_structure_ids_and_order_are_deterministic() -> None:
    blocks = [_block("one", index=1), _block("two", index=2)]
    first = StructureAwareChunker().build_regions(blocks)
    second = StructureAwareChunker().build_regions(blocks)

    assert first == second
    assert first[0].region_id == "region-1"
    assert [unit.unit_id for unit in first[0].units] == ["unit-1-1", "unit-1-2"]
