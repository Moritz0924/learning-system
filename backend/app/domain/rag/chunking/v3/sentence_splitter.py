from __future__ import annotations

import re


class SentenceSplitter:
    """Deterministically split natural-language sentences while preserving punctuation."""

    _boundary = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)")

    def split(self, text: str) -> list[str]:
        return [piece.strip() for piece in self._boundary.split(text) if piece.strip()]
