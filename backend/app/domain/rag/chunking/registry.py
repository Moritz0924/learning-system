from __future__ import annotations

from typing import Any, Mapping

from .chunkers import CodeChunker, MarkdownChunker, TableChunker, TextChunker
from .domain import DEFAULT_CHUNK_POLICY, ChunkDraft, ChunkPolicy, ChunkType


class ChunkerRegistry:
    def __init__(self, policy: ChunkPolicy = DEFAULT_CHUNK_POLICY) -> None:
        self.policy = policy
        self._chunkers = {
            ChunkType.TEXT: TextChunker(policy),
            ChunkType.MARKDOWN: MarkdownChunker(policy),
            ChunkType.CODE: CodeChunker(policy),
            ChunkType.TABLE: TableChunker(policy),
            ChunkType.SLIDE: TextChunker(policy, chunk_type=ChunkType.SLIDE),
            ChunkType.IMAGE_DESCRIPTION: TextChunker(
                policy,
                chunk_type=ChunkType.IMAGE_DESCRIPTION,
            ),
        }

    @classmethod
    def default(cls) -> "ChunkerRegistry":
        return cls(DEFAULT_CHUNK_POLICY)

    @property
    def registered_types(self) -> tuple[ChunkType, ...]:
        return tuple(self._chunkers)

    def chunk(
        self,
        chunk_type: ChunkType | str,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]:
        try:
            normalized_type = ChunkType(chunk_type)
        except ValueError as exc:
            raise LookupError(f"no chunker registered for {chunk_type!r}") from exc
        return self._chunkers[normalized_type].chunk(
            content,
            heading_path=heading_path,
            metadata=metadata,
        )
