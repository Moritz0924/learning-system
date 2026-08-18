from __future__ import annotations

import os
from hashlib import sha256
import httpx

from backend.app.services.provider_urls import (
    build_provider_url,
    provider_url_identity,
    should_trust_http_environment,
)


class EmbeddingUnavailable(RuntimeError):
    pass


class DeterministicEmbeddingClient:
    mode = "deterministic_test"
    provider_identity = "deterministic:sha256-v1"
    model = "deterministic-sha256-v1"
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

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OpenAICompatibleEmbeddingClient:
    mode = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            (_config_value(base_url) or "")
            if base_url is not None
            else (
                _config_value(os.getenv("EMBEDDING_BASE_URL"))
                or "https://api.openai.com/v1"
            )
        )
        self.provider_identity = _openai_compatible_provider_identity(self.base_url)
        self.api_key = (
            _config_value(api_key)
            if api_key is not None
            else _config_value(os.getenv("EMBEDDING_API_KEY"))
            or (
                _config_value(os.getenv("LLM_API_KEY"))
                if _same_provider_endpoint(self.base_url, _config_value(os.getenv("LLM_BASE_URL")))
                else None
            )
        )
        self.model = (
            (_config_value(model) or "")
            if model is not None
            else _config_value(os.getenv("EMBEDDING_MODEL")) or "text-embedding-3-small"
        )
        configured_dimensions = dimensions
        if configured_dimensions is None:
            try:
                configured_dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
            except ValueError:
                configured_dimensions = 1536
        self.dimensions = configured_dimensions if configured_dimensions > 0 else 1536
        self.http_client = http_client or httpx.Client(
            timeout=15,
            trust_env=should_trust_http_environment(),
        )

    def embed(self, text: str) -> list[float]:
        return self._request(text)[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._request(texts)

    def _request(self, inputs: str | list[str]) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingUnavailable("EMBEDDING_API_KEY is required for remote embeddings")
        try:
            response = self.http_client.post(
                build_provider_url(self.base_url, "embeddings"),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": inputs},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload["data"]
            if not isinstance(data, list):
                raise TypeError("embedding response data must be a list")
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            return [
                [float(value) for value in item["embedding"]]
                for item in ordered
            ]
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


def _openai_compatible_provider_identity(base_url: str) -> str:
    return f"openai-compatible:{provider_url_identity(base_url)}"


def _same_provider_endpoint(first: str, second: str | None) -> bool:
    if not second:
        return False
    return _openai_compatible_provider_identity(first) == _openai_compatible_provider_identity(second)
