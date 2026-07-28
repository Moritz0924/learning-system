"""Cross-platform hashes for versioned UTF-8 evaluation text assets."""
from __future__ import annotations

import hashlib
from pathlib import Path


def canonical_text_bytes(path: Path) -> bytes:
    """Return UTF-8 bytes with every supported newline form normalized to LF."""
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def canonical_text_sha256(path: Path) -> str:
    return hashlib.sha256(canonical_text_bytes(path)).hexdigest()
