from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, ConfigDict

from backend.app.infrastructure.llm.structured_output_client import StructuredOutputClient


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


def test_client_does_not_call_remote_when_credentials_are_blank() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response({"value": 1})

    client = StructuredOutputClient(
        base_url="",
        api_key="",
        model="assessment-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete(
        role="assessment_generator",
        prompt_version="v2",
        system_instructions="Return a schema-valid response.",
        input_payload=_Input(request="generate"),
        output_model=_Output,
    )

    assert result.mode == "offline"
    assert result.value is None
    assert calls == 0
    assert "api_key" not in str(client.last_metadata).lower()


def test_client_retries_transient_failure_and_validates_json_schema() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response({"error": "busy"}, status_code=429)
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["strict"] is True
        return _response({"value": 7})

    client = StructuredOutputClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="assessment-test",
        max_retries=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete(
        role="assessment_generator",
        prompt_version="v2",
        system_instructions="Return a schema-valid response.",
        input_payload=_Input(request="generate"),
        output_model=_Output,
    )

    assert result.value == _Output(value=7)
    assert result.mode == "remote"
    assert result.retry_count == 1
    assert calls == 2


def test_client_repairs_invalid_schema_once_then_returns_validated_value() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response({"value": 4, "unexpected": True} if calls == 1 else {"value": 5})

    client = StructuredOutputClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="assessment-test",
        schema_repair_attempts=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete(
        role="assessment_grader",
        prompt_version="v2",
        system_instructions="Return a schema-valid response.",
        input_payload=_Input(request="grade"),
        output_model=_Output,
    )

    assert result.value == _Output(value=5)
    assert result.repair_count == 1
    assert calls == 2
