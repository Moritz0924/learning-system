from __future__ import annotations

from hashlib import sha256

from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkDraft,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
    normalize_chunk_text,
)


def test_normalizer_canonicalizes_unicode_newlines_and_trailing_space() -> None:
    raw = "  Cafe\u0301  \r\nSecond line\t\rThird line  \n\n"

    assert normalize_chunk_text(raw) == "Caf\u00e9\nSecond line\nThird line"


def test_chunk_policy_uses_the_production_character_limits() -> None:
    assert (
        DEFAULT_CHUNK_POLICY.target_chars,
        DEFAULT_CHUNK_POLICY.max_chars,
        DEFAULT_CHUNK_POLICY.overlap_chars,
        DEFAULT_CHUNK_POLICY.min_chars,
    ) == (500, 700, 80, 100)


def test_text_chunker_splits_no_space_chinese_with_exact_overlap() -> None:
    text = "".join(f"\u7b2c{index}\u8282\u8bb2\u89e3\u68c0\u7d22\u589e\u5f3a\u751f\u6210\u3002" for index in range(160))

    chunks = ChunkerRegistry.default().chunk(ChunkType.TEXT, text)

    assert len(chunks) > 1
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)
    for left, right in zip(chunks, chunks[1:]):
        assert right.content.startswith(left.content[-DEFAULT_CHUNK_POLICY.overlap_chars :])


def test_text_chunker_handles_mixed_language_without_empty_or_oversized_chunks() -> None:
    text = ("\u4e2d\u6587\u8bed\u4e49RAG-v2\u6df7\u5408content\u3002" * 90) + " final sentence"

    chunks = ChunkerRegistry.default().chunk(ChunkType.TEXT, text)

    assert len(chunks) > 1
    assert all(chunk.content for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)
    assert any("RAG-v2" in chunk.content for chunk in chunks)


def test_text_chunker_preserves_configured_overlap_at_whitespace_boundaries() -> None:
    text = " ".join(f"retrieval-token-{index:03d}" for index in range(100))

    chunks = ChunkerRegistry.default().chunk(ChunkType.TEXT, text)

    assert len(chunks) > 1
    for left, right in zip(chunks, chunks[1:]):
        assert right.content.startswith(left.content[-DEFAULT_CHUNK_POLICY.overlap_chars :])


def test_markdown_chunker_tracks_heading_paths_and_routes_structured_blocks() -> None:
    markdown = """# RAG Guide
Overview of grounded answers.

## Code
```python
def retrieve(query):
    return query
```

## Metrics
| metric | value |
| --- | --- |
| recall | 0.90 |
| precision | 0.80 |
"""

    chunks = ChunkerRegistry.default().chunk(ChunkType.MARKDOWN, markdown)

    assert [(chunk.chunk_type, chunk.heading_path) for chunk in chunks] == [
        (ChunkType.MARKDOWN, ("RAG Guide",)),
        (ChunkType.MARKDOWN, ("RAG Guide", "Code")),
        (ChunkType.CODE, ("RAG Guide", "Code")),
        (ChunkType.MARKDOWN, ("RAG Guide", "Metrics")),
        (ChunkType.TABLE, ("RAG Guide", "Metrics")),
    ]
    assert chunks[0].content == "# RAG Guide\nOverview of grounded answers."
    assert chunks[1].content == "## Code"
    assert chunks[3].content == "## Metrics"


def test_markdown_heading_only_document_produces_a_searchable_chunk() -> None:
    chunks = ChunkerRegistry.default().chunk(ChunkType.MARKDOWN, "# Searchable heading")

    assert len(chunks) == 1
    assert chunks[0].content == "# Searchable heading"
    assert chunks[0].heading_path == ("Searchable heading",)


def test_markdown_four_backtick_fence_ignores_shorter_inner_fences() -> None:
    markdown = """# Fence Guide
````markdown
```python
inside = True
```
````
## After
Following prose.
"""

    chunks = ChunkerRegistry.default().chunk(ChunkType.MARKDOWN, markdown)

    assert [chunk.chunk_type for chunk in chunks] == [
        ChunkType.MARKDOWN,
        ChunkType.CODE,
        ChunkType.MARKDOWN,
    ]
    assert chunks[1].content == "````markdown\n```python\ninside = True\n```\n````"
    assert chunks[1].heading_path == ("Fence Guide",)
    assert chunks[2].content == "## After\nFollowing prose."
    assert chunks[2].heading_path == ("Fence Guide", "After")


def test_markdown_tilde_fence_preserves_delimiter_and_resumes_structure() -> None:
    markdown = """# Shell Guide
~~~bash
echo grounded
~~~
## After
Following prose.
"""

    chunks = ChunkerRegistry.default().chunk(ChunkType.MARKDOWN, markdown)

    assert [chunk.chunk_type for chunk in chunks] == [
        ChunkType.MARKDOWN,
        ChunkType.CODE,
        ChunkType.MARKDOWN,
    ]
    assert chunks[1].content == "~~~bash\necho grounded\n~~~"
    assert chunks[1].heading_path == ("Shell Guide",)
    assert chunks[2].content == "## After\nFollowing prose."
    assert chunks[2].heading_path == ("Shell Guide", "After")


def test_code_chunker_repeats_fences_while_respecting_maximum() -> None:
    code = "```python\n" + "\n".join(
        f"value_{index} = retrieve('query-{index}')" for index in range(80)
    ) + "\n```"

    chunks = ChunkerRegistry.default().chunk(ChunkType.CODE, code)

    assert len(chunks) > 1
    assert all(chunk.content.startswith("```python\n") for chunk in chunks)
    assert all(chunk.content.endswith("\n```") for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)
    assert "value_0 = retrieve('query-0')" in chunks[0].content
    assert "value_79 = retrieve('query-79')" in chunks[-1].content


def test_code_chunker_hard_splits_an_oversized_fence_label() -> None:
    code = f"```{'language' * 120}\nprint('bounded')\n```"

    chunks = ChunkerRegistry.default().chunk(ChunkType.CODE, code)

    assert len(chunks) > 1
    assert all(chunk.chunk_type is ChunkType.CODE for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)


def test_table_chunker_repeats_header_and_splits_rows_at_boundaries() -> None:
    table = "\n".join(
        ["| item | description |", "| --- | --- |"]
        + [f"| row-{index} | deterministic table value {index} |" for index in range(40)]
    )

    chunks = ChunkerRegistry.default().chunk(ChunkType.TABLE, table)

    assert len(chunks) > 1
    assert all(chunk.content.startswith("| item | description |\n| --- | --- |") for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)
    assert "| row-0 |" in chunks[0].content
    assert "| row-39 |" in chunks[-1].content


def test_table_chunker_hard_splits_an_oversized_row() -> None:
    table = "\n".join(
        [
            "| item | description |",
            "| --- | --- |",
            f"| oversized | {'x' * 1500} |",
        ]
    )

    chunks = ChunkerRegistry.default().chunk(ChunkType.TABLE, table)

    assert len(chunks) > 1
    assert all(chunk.content.startswith("| item | description |\n| --- | --- |") for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)


def test_table_chunker_hard_splits_an_oversized_header() -> None:
    table = "\n".join(
        [
            f"| {'heading' * 120} | description |",
            "| --- | --- |",
            "| value | bounded |",
        ]
    )

    chunks = ChunkerRegistry.default().chunk(ChunkType.TABLE, table)

    assert len(chunks) > 1
    assert all(chunk.chunk_type is ChunkType.TABLE for chunk in chunks)
    assert all(len(chunk.content) <= DEFAULT_CHUNK_POLICY.max_chars for chunk in chunks)


def test_metadata_builder_adds_deterministic_hashes_and_neighbor_links() -> None:
    drafts = [
        ChunkDraft("Alpha\r\nBeta", ChunkType.TEXT),
        ChunkDraft("Gamma", ChunkType.CODE, heading_path=("Guide", "Example")),
        ChunkDraft("Delta", ChunkType.TABLE),
    ]
    builder = ChunkMetadataBuilder(DEFAULT_CHUNK_POLICY)

    first = builder.build(drafts, document_id="doc-1", base_metadata={"source_type": "test"})
    second = builder.build(drafts, document_id="doc-1", base_metadata={"source_type": "test"})

    assert first == second
    assert first[0].content == "Alpha\nBeta"
    assert first[0].content_hash == sha256(b"Alpha\nBeta").hexdigest()
    assert first[0].previous_chunk_id is None
    assert first[0].next_chunk_id == first[1].chunk_id
    assert first[1].previous_chunk_id == first[0].chunk_id
    assert first[1].next_chunk_id == first[2].chunk_id
    assert first[2].previous_chunk_id == first[1].chunk_id
    assert first[2].next_chunk_id is None
    assert first[1].metadata["heading_path"] == ["Guide", "Example"]
    assert first[1].metadata["chunk_schema_version"] == "v2"


def test_registry_exposes_all_production_chunk_types() -> None:
    registry = ChunkerRegistry.default()

    assert set(registry.registered_types) == {
        ChunkType.TEXT,
        ChunkType.MARKDOWN,
        ChunkType.CODE,
        ChunkType.TABLE,
        ChunkType.SLIDE,
        ChunkType.IMAGE_DESCRIPTION,
    }
