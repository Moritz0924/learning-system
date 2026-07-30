from __future__ import annotations

from hashlib import sha256
from typing import Iterable


def persisted_chunk_id(
    *,
    index_version_id: str,
    chunk_index: int,
    content_hash: str,
) -> str:
    if not index_version_id.strip():
        raise ValueError("index_version_id is required")
    if chunk_index <= 0:
        raise ValueError("chunk_index must be positive")
    if len(content_hash) != 64:
        raise ValueError("content_hash must be a SHA256 hex digest")
    identity = (
        f"chunk-v2\0{index_version_id}\0{chunk_index}\0{content_hash}"
    ).encode("utf-8")
    return f"chunk-{sha256(identity).hexdigest()[:32]}"


def persisted_chunk_ids(
    *,
    index_version_id: str,
    content_hashes: Iterable[str],
) -> list[str]:
    return [
        persisted_chunk_id(
            index_version_id=index_version_id,
            chunk_index=index,
            content_hash=content_hash,
        )
        for index, content_hash in enumerate(content_hashes, start=1)
    ]
