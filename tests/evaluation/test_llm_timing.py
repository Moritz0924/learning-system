from __future__ import annotations

import json

import httpx
import pytest

from backend.app.services.llm_gateway import LLMGatewayClient


def test_complete_timed_preserves_safety_order_and_records_usage() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"ok","citations":[]}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    gateway = LLMGatewayClient(
        base_url="https://llm.example.test/v1",
        api_key="secret",
        model="model-1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )

    result = gateway.complete_timed(
        role="teacher",
        prompt="question",
        context=[],
        instruction_prompt="candidate instructions",
        response_envelope="strict response envelope",
        temperature=0,
        max_output_tokens=256,
        seed=42,
        strict_remote=True,
    )

    assert [message["content"] for message in seen["messages"]][1:3] == [
        "candidate instructions",
        "strict response envelope",
    ]
    assert "adaptive AI application development tutor" in seen["messages"][0]["content"]
    assert seen["messages"][-1] == {"role": "user", "content": "question"}
    assert seen["temperature"] == 0
    assert seen["top_p"] == 1
    assert seen["max_tokens"] == 256
    assert seen["seed"] == 42
    assert result.input_token_count == 12
    assert result.output_token_count == 7
    assert result.mode == "remote"
    assert result.total_latency_ms >= result.request_latency_ms >= 0


def test_strict_remote_rejects_missing_provider_configuration(monkeypatch) -> None:
    from backend.app.services.llm_gateway import EvaluationProviderError

    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    gateway = LLMGatewayClient(base_url="", api_key="")

    with pytest.raises(EvaluationProviderError) as caught:
        gateway.complete_timed(
            role="teacher",
            prompt="question",
            strict_remote=True,
        )

    assert caught.value.error_code == "provider_configuration_missing"
    assert caught.value.retry_count == 0
    assert caught.value.total_latency_ms >= 0


def test_strict_remote_never_uses_offline_fallback_on_http_failure() -> None:
    from backend.app.services.llm_gateway import EvaluationProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    gateway = LLMGatewayClient(
        base_url="https://llm.example.test/v1",
        api_key="secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
    )

    with pytest.raises(EvaluationProviderError) as caught:
        gateway.complete_timed(role="teacher", prompt="question", strict_remote=True)

    assert caught.value.error_code == "provider_request_failed"
    assert caught.value.retry_count == 1
    assert gateway.last_completion_metadata["mode"] == "failed"


def test_production_complete_retains_offline_fallback(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    gateway = LLMGatewayClient(base_url="", api_key="")

    answer = gateway.complete(role="teacher", prompt="question", context=[])

    assert answer == "teacher: question"
    assert gateway.last_completion_metadata["mode"] == "offline"
