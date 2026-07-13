from __future__ import annotations

import os
from hashlib import sha256

import httpx


class EmbeddingUnavailable(RuntimeError):
    pass


class DeterministicEmbeddingClient:
    mode = "deterministic_test"
    dimensions = 1536

    def embed(self, text: str) -> list[float]:
        seed = text.lower().encode("utf-8")
        values: list[float] = []
        counter = 0
        while len(values) < self.dimensions:
            digest = sha256(seed + counter.to_bytes(4, "big")).digest()
            values.extend(byte / 255 for byte in digest)
            counter += 1
        return values[: self.dimensions]


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
            _config_value(base_url)
            or _config_value(os.getenv("EMBEDDING_BASE_URL"))
            or _config_value(os.getenv("LLM_BASE_URL"))
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = (
            _config_value(api_key)
            if api_key is not None
            else _config_value(os.getenv("EMBEDDING_API_KEY")) or _config_value(os.getenv("LLM_API_KEY"))
        )
        self.model = _config_value(model) or _config_value(os.getenv("EMBEDDING_MODEL")) or "text-embedding-3-small"
        self.http_client = http_client or httpx.Client(timeout=15)

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise EmbeddingUnavailable("EMBEDDING_API_KEY or LLM_API_KEY is required for remote embeddings")
        try:
            response = self.http_client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text},
            )
            response.raise_for_status()
            payload = response.json()
            embedding = payload["data"][0]["embedding"]
            return [float(value) for value in embedding]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("remote embedding failed") from exc


def build_embedding_client():
    backend = (_config_value(os.getenv("EMBEDDING_BACKEND")) or "openai").lower()
    if backend == "openai":
        return OpenAICompatibleEmbeddingClient()
    if backend == "deterministic":
        return DeterministicEmbeddingClient()
    raise EmbeddingUnavailable(f"unsupported embedding backend: {backend}")


def _config_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
