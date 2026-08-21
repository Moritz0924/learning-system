from __future__ import annotations


class TiktokenTokenCounter:
    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        try:
            import tiktoken
        except ImportError as exc:  # pragma: no cover - dependency is project-managed
            raise RuntimeError("tiktoken is required for V3 token counting") from exc
        self.encoding_name = encoding_name
        self.encoder = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self.encoder.encode(text))


__all__ = ["TiktokenTokenCounter"]
