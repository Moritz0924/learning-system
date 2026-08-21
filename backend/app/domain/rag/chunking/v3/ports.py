from __future__ import annotations

from typing import Protocol, Sequence


class SemanticEncoderPort(Protocol):
    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...


class TokenCounterPort(Protocol):
    def count(self, text: str) -> int: ...

