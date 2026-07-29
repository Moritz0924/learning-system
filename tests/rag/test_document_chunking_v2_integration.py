from __future__ import annotations

from backend.app.application.document_service import _parse_document_content


def test_production_markdown_parser_persists_structured_chunk_metadata() -> None:
    content = b"""# Guide
Grounded answers cite evidence.

## Example
```python
answer = retrieve(query)
```

## Scores
| metric | value |
| --- | --- |
| recall | 0.9 |
"""

    parsed = _parse_document_content(
        content,
        filename="guide.md",
        mime_type="text/markdown",
    )

    assert [chunk["metadata"]["chunk_type"] for chunk in parsed.chunks] == [
        "markdown",
        "markdown",
        "code",
        "markdown",
        "table",
    ]
    assert parsed.chunks[0]["content"] == "# Guide\nGrounded answers cite evidence."
    assert parsed.chunks[1]["content"] == "## Example"
    assert parsed.chunks[2]["metadata"]["heading_path"] == ["Guide", "Example"]
    assert parsed.chunks[3]["content"] == "## Scores"
    assert parsed.chunks[0]["metadata"]["previous_chunk_id"] is None
    assert parsed.chunks[0]["metadata"]["next_chunk_id"] == parsed.chunks[1]["metadata"]["chunk_id"]
    assert parsed.chunks[-1]["metadata"]["next_chunk_id"] is None
    assert all(len(chunk["metadata"]["content_hash"]) == 64 for chunk in parsed.chunks)


def test_production_plain_text_parser_uses_text_chunker_for_no_space_chinese() -> None:
    content = ("\u65e0\u7a7a\u683c\u4e2d\u6587\u68c0\u7d22\u5185\u5bb9\u3002" * 120).encode("utf-8")

    parsed = _parse_document_content(
        content,
        filename="notes.txt",
        mime_type="text/plain",
    )

    assert len(parsed.chunks) > 1
    assert {chunk["metadata"]["chunk_type"] for chunk in parsed.chunks} == {"text"}
    assert all(len(chunk["content"]) <= 700 for chunk in parsed.chunks)
