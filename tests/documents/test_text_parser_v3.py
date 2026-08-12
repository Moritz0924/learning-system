from __future__ import annotations


def test_structured_markdown_parser_preserves_structure_offsets_and_text_provenance() -> None:
    from backend.app.services.document_parsing.models import (
        DocumentBlockType,
        DocumentFileType,
        ProcessingMode,
        SourceElementType,
    )
    from backend.app.services.document_parsing.text_parser import StructuredTextParser

    content = (
        b"# Lesson\n\n"
        b"A paragraph explains the learning objective.\n\n"
        b"- First item\n- Second item\n\n"
        b"```python\nprint('study')\n```\n\n"
        b"| Topic | Score |\n| --- | --- |\n| Retrieval | 1 |\n"
    )

    result = StructuredTextParser().parse(
        content=content,
        filename="lesson.md",
        mime_type="text/markdown",
    )

    assert result.file_type is DocumentFileType.TEXT
    assert result.parser_version == "document-parser-v4.1"
    assert [block.block_type for block in result.blocks] == [
        DocumentBlockType.HEADING,
        DocumentBlockType.PARAGRAPH,
        DocumentBlockType.LIST_ITEM,
        DocumentBlockType.LIST_ITEM,
        DocumentBlockType.CODE,
        DocumentBlockType.TABLE,
    ]
    assert all(block.processing_mode is ProcessingMode.TEXT_NATIVE for block in result.blocks)
    assert all(block.source_element is SourceElementType.TEXT_FILE for block in result.blocks)
    assert all(block.source_format == "markdown" for block in result.blocks)
    assert [block.block_index for block in result.blocks] == list(range(1, 7))
    assert [block.reading_order for block in result.blocks] == list(range(1, 7))
    for block in result.blocks:
        assert block.source_char_start is not None
        assert block.source_char_end is not None
        assert content.decode("utf-8")[block.source_char_start:block.source_char_end].strip()


def test_structured_plain_text_parser_uses_blank_lines_without_markdown_syntax() -> None:
    from backend.app.services.document_parsing.models import DocumentBlockType
    from backend.app.services.document_parsing.text_parser import StructuredTextParser

    result = StructuredTextParser().parse(
        content=b"# literal text\ncontinues here\n\nSecond paragraph.",
        filename="notes.txt",
        mime_type="text/plain",
    )

    assert [block.block_type for block in result.blocks] == [
        DocumentBlockType.PARAGRAPH,
        DocumentBlockType.PARAGRAPH,
    ]
    assert result.blocks[0].text == "# literal text\ncontinues here"
    assert all(block.source_format == "plain_text" for block in result.blocks)
