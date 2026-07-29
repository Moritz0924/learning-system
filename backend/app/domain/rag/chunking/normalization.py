from __future__ import annotations

from hashlib import sha256
from unicodedata import normalize as unicode_normalize


def normalize_chunk_text(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("chunk content must be text")
    canonical = unicode_normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in canonical.split("\n")]
    return "\n".join(lines).strip()


def chunk_content_hash(content: str) -> str:
    normalized = normalize_chunk_text(content)
    return sha256(normalized.encode("utf-8")).hexdigest()
