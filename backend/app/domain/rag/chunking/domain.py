from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .normalization import normalize_chunk_text


class ChunkType(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    CODE = "code"
    TABLE = "table"
    SLIDE = "slide"
    IMAGE_DESCRIPTION = "image_description"


@dataclass(frozen=True)
class ChunkPolicy:
    target_chars: int
    max_chars: int
    overlap_chars: int
    min_chars: int

    def __post_init__(self) -> None:
        if self.min_chars <= 0:
            raise ValueError("min_chars must be positive")
        if self.target_chars < self.min_chars:
            raise ValueError("target_chars must be greater than or equal to min_chars")
        if self.max_chars < self.target_chars:
            raise ValueError("max_chars must be greater than or equal to target_chars")
        if self.overlap_chars < 0 or self.overlap_chars >= self.target_chars:
            raise ValueError("overlap_chars must be non-negative and smaller than target_chars")


DEFAULT_CHUNK_POLICY = ChunkPolicy(
    target_chars=500,
    max_chars=700,
    overlap_chars=80,
    min_chars=100,
)


@dataclass(frozen=True)
class ChunkDraft:
    content: str
    chunk_type: ChunkType
    heading_path: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = normalize_chunk_text(self.content)
        if not normalized:
            raise ValueError("chunk content is required")
        object.__setattr__(self, "content", normalized)
        object.__setattr__(
            self,
            "heading_path",
            tuple(part.strip() for part in self.heading_path if part.strip()),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    content_hash: str
    chunk_type: ChunkType
    previous_chunk_id: str | None
    next_chunk_id: str | None
    metadata: Mapping[str, Any]
