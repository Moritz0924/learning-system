import httpx
import pytest

from backend.app.services.llm_gateway import LLMGatewayClient
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
