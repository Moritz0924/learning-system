from __future__ import annotations

from backend.app.services.embeddings import (
    DeterministicEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
)
from backend.app.infrastructure.persistence.repositories.rag_repository import _vector_literal


def test_deterministic_embedding_client_exposes_stable_batch_identity() -> None:
    client = DeterministicEmbeddingClient()

    batch = client.embed_batch(["first", "second"])

    assert client.model == "deterministic-sha256-v1"
    assert batch == [client.embed("first"), client.embed("second")]
    assert all(len(vector) == client.dimensions for vector in batch)


def test_openai_compatible_embedding_client_sends_one_batch_request() -> None:
    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            }

    class HTTPClient:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def post(self, url: str, **kwargs):
            self.requests.append({"url": url, **kwargs})
            return Response()

    http_client = HTTPClient()
    client = OpenAICompatibleEmbeddingClient(
        base_url="https://embedding.example/v1",
        api_key="test-key",
        model="batch-model",
        dimensions=3,
        http_client=http_client,
    )

    embeddings = client.embed_batch(["first", "second"])

    assert embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert len(http_client.requests) == 1
    assert http_client.requests[0]["json"] == {
        "model": "batch-model",
        "input": ["first", "second"],
        "dimensions": 3,
    }


def test_embedding_client_defaults_to_zhipu_embedding_3(monkeypatch) -> None:
    for name in ("EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(name, raising=False)

    client = OpenAICompatibleEmbeddingClient(api_key="test-key")
    try:
        assert client.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert client.model == "embedding-3"
        assert client.dimensions == 2048
    finally:
        client.http_client.close()


def test_pgvector_literals_pad_supported_smaller_embedding_dimensions() -> None:
    literal = _vector_literal([0.25, 0.5])

    assert literal.startswith("[0.25000000,0.50000000,")
    assert literal.count(",") == 2047
    assert literal.endswith(",0.00000000]")
