from __future__ import annotations

import json

import httpx


def _provider_urls():
    from backend.app.services import provider_urls

    return provider_urls


def test_provider_url_builder_preserves_query_when_appending_paths() -> None:
    """Appending an endpoint after `?query` instead of to the path must fail this test."""
    urls = _provider_urls()

    assert urls.build_provider_url(
        "http://127.0.0.1:11434/v1?api-version=2026-08-14",
        "chat/completions",
    ) == "http://127.0.0.1:11434/v1/chat/completions?api-version=2026-08-14"


def test_provider_url_identity_uses_canonical_query_semantics() -> None:
    """Dropping query configuration from provider/index identity must fail this test."""
    urls = _provider_urls()

    first = urls.provider_url_identity("https://EXAMPLE.test:443/v1/?b=2&a=1")
    equivalent = urls.provider_url_identity("https://example.test/v1?a=1&b=2")
    different = urls.provider_url_identity("https://example.test/v1?a=1&b=3")

    assert first == equivalent
    assert first != different


def test_provider_clients_bypass_a_malformed_no_proxy_environment(monkeypatch) -> None:
    """Letting `[::1]` poison HTTP client construction must fail this test."""
    from backend.app.services.embeddings import OpenAICompatibleEmbeddingClient
    from backend.app.services.llm_gateway import LLMGatewayClient

    monkeypatch.setenv("NO_PROXY", "localhost,[::1]")
    urls = _provider_urls()
    assert getattr(urls, "should_trust_http_environment", lambda: True)() is False

    clients = [
        LLMGatewayClient(base_url="https://provider.example/v1", api_key="key", model="chat"),
        OpenAICompatibleEmbeddingClient(base_url="https://provider.example/v1", api_key="key", model="embed"),
    ]
    try:
        assert all(client.http_client._trust_env is False for client in clients)
    finally:
        for client in clients:
            client.http_client.close()


def test_llm_and_embedding_clients_use_shared_query_safe_builder() -> None:
    """Client-specific string concatenation that corrupts query URLs must fail this test."""
    from backend.app.services.embeddings import OpenAICompatibleEmbeddingClient
    from backend.app.services.llm_gateway import LLMGatewayClient

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"index": 0, "embedding": [0.0] * 1536}]},
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    LLMGatewayClient(
        base_url="https://provider.test/v1?scope=chat/",
        api_key="key",
        model="selected",
        http_client=httpx.Client(transport=transport),
    ).complete(role="test", prompt="hello", strict_remote=True)
    OpenAICompatibleEmbeddingClient(
        base_url="https://provider.test/v1?api-version=embed",
        api_key="key",
        model="selected",
        dimensions=1536,
        http_client=httpx.Client(transport=transport),
    ).embed("hello")

    assert seen == [
        "https://provider.test/v1/chat/completions?scope=chat/",
        "https://provider.test/v1/embeddings?api-version=embed",
    ]
