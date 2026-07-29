from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

from .domain import Chunk, ChunkDraft, ChunkPolicy
from .normalization import chunk_content_hash, normalize_chunk_text


class ChunkMetadataBuilder:
    def __init__(self, policy: ChunkPolicy) -> None:
        self.policy = policy

    def build(
        self,
        drafts: Sequence[ChunkDraft],
        *,
        document_id: str,
        base_metadata: Mapping[str, Any] | None = None,
    ) -> list[Chunk]:
        if not document_id.strip():
            raise ValueError("document_id is required")
        normalized = [normalize_chunk_text(draft.content) for draft in drafts]
        hashes = [chunk_content_hash(content) for content in normalized]
        chunk_ids = [
            _stable_chunk_id(document_id=document_id, chunk_index=index, content_hash=content_hash)
            for index, content_hash in enumerate(hashes, start=1)
        ]
        chunks: list[Chunk] = []
        for offset, (draft, content, content_hash, chunk_id) in enumerate(
            zip(drafts, normalized, hashes, chunk_ids)
        ):
            chunk_index = offset + 1
            previous_chunk_id = chunk_ids[offset - 1] if offset else None
            next_chunk_id = chunk_ids[offset + 1] if offset + 1 < len(chunk_ids) else None
            metadata = {
                **dict(base_metadata or {}),
                **dict(draft.metadata),
                "chunk_schema_version": "v2",
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "chunk_type": draft.chunk_type.value,
                "heading_path": list(draft.heading_path),
                "content_hash": content_hash,
                "previous_chunk_id": previous_chunk_id,
                "next_chunk_id": next_chunk_id,
                "chunk_policy": {
                    "target_chars": self.policy.target_chars,
                    "max_chars": self.policy.max_chars,
                    "overlap_chars": self.policy.overlap_chars,
                    "min_chars": self.policy.min_chars,
                },
            }
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=content,
                    content_hash=content_hash,
                    chunk_type=draft.chunk_type,
                    previous_chunk_id=previous_chunk_id,
                    next_chunk_id=next_chunk_id,
                    metadata=metadata,
                )
            )
        return chunks


def _stable_chunk_id(*, document_id: str, chunk_index: int, content_hash: str) -> str:
    identity = f"v2\0{document_id}\0{chunk_index}\0{content_hash}".encode("utf-8")
    return f"chunk-{sha256(identity).hexdigest()[:32]}"
