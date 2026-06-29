from __future__ import annotations

import os
from hashlib import sha256

import httpx


class EmbeddingUnavailable(RuntimeError):
    pass


class DeterministicEmbeddingClient:
    mode = "deterministic_test"

    def embed(self, text: str) -> list[float]:
        digest = sha256(text.lower().encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:16]]


class OpenAICompatibleEmbeddingClient:
    mode = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("EMBEDDING_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"
        self.http_client = http_client or httpx.Client(timeout=15)

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise EmbeddingUnavailable("EMBEDDING_API_KEY or LLM_API_KEY is required for remote embeddings")
        response = self.http_client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
        )
        response.raise_for_status()
        payload = response.json()
        embedding = payload["data"][0]["embedding"]
        return [float(value) for value in embedding]


def build_embedding_client():
    backend = os.getenv("EMBEDDING_BACKEND", "openai").lower()
    if backend == "openai":
        return OpenAICompatibleEmbeddingClient()
    if backend == "deterministic":
        return DeterministicEmbeddingClient()
    raise EmbeddingUnavailable(f"unsupported embedding backend: {backend}")
