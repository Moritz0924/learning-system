import httpx
import pytest

from backend.app.services.llm_gateway import LLMGatewayClient
from backend.app.services.embeddings import OpenAICompatibleEmbeddingClient
from backend.app.services.official_sources import search_official_learning_sources


def test_llm_gateway_sends_openai_compatible_chat_completion_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = __import__("json").loads(request.content.decode())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Grounded answer"}}]},
        )

    client = LLMGatewayClient(
        base_url="https://llm.example.test/v1",
        api_key="secret",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    answer = client.complete(role="teacher", prompt="Explain RAG.", context=[])

    assert answer == "Grounded answer"
    assert seen["url"] == "https://llm.example.test/v1/chat/completions"
    assert seen["authorization"] == "Bearer secret"
    assert seen["json"]["model"] == "demo-model"
    assert seen["json"]["messages"][0]["role"] == "system"
    assert seen["json"]["messages"][1]["content"] == "Explain RAG."
    assert client.last_completion_metadata["mode"] == "remote"
    assert client.last_completion_metadata["is_remote"] is True


def test_llm_gateway_marks_offline_completion_when_remote_config_missing(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client = LLMGatewayClient()

    answer = client.complete(role="teacher", prompt="Explain RAG.", context=[])

    assert answer == "teacher: Explain RAG."
    assert client.last_completion_metadata == {
        "mode": "offline",
        "is_remote": False,
        "model": "stage3-mock-model",
        "reason": "missing LLM_BASE_URL or LLM_API_KEY",
    }


def test_embedding_client_sends_openai_compatible_embedding_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = __import__("json").loads(request.content.decode())
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    client = OpenAICompatibleEmbeddingClient(
        base_url="https://llm.example.test/v1",
        api_key="secret",
        model="embed-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    embedding = client.embed("ground this document")

    assert embedding == [0.1, 0.2, 0.3]
    assert seen["url"] == "https://llm.example.test/v1/embeddings"
    assert seen["authorization"] == "Bearer secret"
    assert seen["json"] == {"model": "embed-model", "input": "ground this document"}


def test_official_source_search_rejects_non_whitelisted_domains(db_session):
    with pytest.raises(ValueError, match="not whitelisted"):
        search_official_learning_sources(
            db_session,
            query="private forum answer",
            domains=["example.com"],
        )


def test_official_source_search_returns_retrieved_at_and_records_tool_call(db_session):
    results = search_official_learning_sources(
        db_session,
        query="Python pathlib",
        domains=["docs.python.org"],
    )

    assert results[0]["title"]
    assert results[0]["url"].startswith("https://docs.python.org")
    assert results[0]["published_at"] is None
    assert results[0]["retrieved_at"]
    assert results[0]["source_level"] == "official"
    assert results[0]["retrieval_mode"] == "url_template"
    assert results[0]["is_live_search"] is False


def test_official_source_search_allows_frontend_default_openai_domain(db_session):
    results = search_official_learning_sources(
        db_session,
        query="OpenAI API responses",
        domains=["platform.openai.com"],
    )

    assert results[0]["url"].startswith("https://platform.openai.com")
    assert results[0]["retrieval_mode"] == "url_template"


def test_official_source_search_can_use_brave_live_provider(db_session, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "pathlib - Python documentation",
                            "url": "https://docs.python.org/3/library/pathlib.html",
                            "description": "Object-oriented filesystem paths.",
                        },
                        {
                            "title": "Untrusted mirror",
                            "url": "https://example.com/pathlib",
                            "description": "Should be filtered.",
                        },
                    ]
                }
            },
        )

    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")

    results = search_official_learning_sources(
        db_session,
        query="Python pathlib",
        domains=["docs.python.org"],
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert results == [
        {
            "title": "pathlib - Python documentation",
            "url": "https://docs.python.org/3/library/pathlib.html",
            "snippet": "Object-oriented filesystem paths.",
            "published_at": None,
            "retrieved_at": results[0]["retrieved_at"],
            "source_level": "official",
            "retrieval_mode": "brave_search",
            "is_live_search": True,
        }
    ]
    assert "site%3Adocs.python.org+Python+pathlib" in seen["url"]
    assert seen["headers"]["x-subscription-token"] == "brave-secret"
