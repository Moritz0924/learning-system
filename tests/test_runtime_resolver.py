from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from backend.app.models import (
    User,
    UserCapabilityBinding,
    UserModelProfile,
    UserSecretReference,
)
from backend.app.services.llm_gateway import EvaluationProviderError, LLMGatewayClient
from backend.app.services.document_parsing.models import (
    DocumentFileType,
    SourceElementType,
    VisionContext,
)
from tests.fakes.secret_store import InMemorySecretStore


def _runtime_module():
    try:
        from backend.app.application import config_service
    except ModuleNotFoundError:
        pytest.fail("RuntimeResolver application service is missing")
    return config_service


def _seed_user(db_session, user_id: str = "runtime-user") -> None:
    db_session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.com",
            normalized_email=f"{user_id}@example.com",
            display_name="Runtime User",
        )
    )
    db_session.flush()


def _seed_bound_profile(
    db_session,
    secrets: InMemorySecretStore,
    *,
    capability: str = "chat",
    profile_id: str = "profile-explicit",
    enabled: bool = True,
) -> UserModelProfile:
    profile = UserModelProfile(
        id=profile_id,
        user_id="runtime-user",
        name=profile_id,
        capability=capability,
        provider="openai_compatible",
        base_url="https://models.example.test/v1",
        model_name="explicit-model",
        dimensions=1536 if capability == "embedding" else None,
        enabled=enabled,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(
        UserCapabilityBinding(
            id=f"binding-{capability}",
            user_id="runtime-user",
            capability=capability,
            model_profile_id=profile.id,
        )
    )
    secret_ref = f"secret-{profile.id}"
    secrets.put(secret_ref, "profile-private-key")
    db_session.add(
        UserSecretReference(
            id=f"reference-{profile.id}",
            user_id="runtime-user",
            owner_type="model",
            owner_id=profile.id,
            slot="api_key",
            secret_ref=secret_ref,
            configured=True,
            masked_value="********",
        )
    )
    db_session.flush()
    return profile


def test_runtime_resolver_uses_environment_only_when_no_user_binding(db_session, monkeypatch) -> None:
    """Adding a user-config requirement to the no-binding path must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")
    monkeypatch.setenv("LLM_MODEL", "environment-model")

    resolved = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=None
    ).resolve("chat")

    assert isinstance(resolved, LLMGatewayClient)
    assert resolved.base_url == "https://environment.example/v1"
    assert resolved.api_key == "environment-key"
    assert resolved.model == "environment-model"


def test_runtime_resolver_prefers_bound_profile_and_isolates_provider_credentials(
    db_session, monkeypatch
) -> None:
    """Falling back to LLM env credentials or DeepSeek routing must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(db_session, secrets)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "unrelated-environment-key")
    monkeypatch.setenv("DEEPSEEK_PRO_MODEL", "deepseek-pro")

    resolved = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=secrets
    ).resolve("chat")

    assert resolved.base_url == "https://models.example.test/v1"
    assert resolved.api_key == "profile-private-key"
    assert resolved.model == "explicit-model"
    assert resolved.provider == "openai_compatible"
    assert resolved.pro_model == "explicit-model"


def test_tutor_text_resolution_prefers_chat_then_reuses_reasoning_without_environment_fallback(
    db_session, monkeypatch
) -> None:
    """Returning an offline environment client when a user text model exists must fail."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(
        db_session,
        secrets,
        capability="reasoning",
        profile_id="reasoning-profile",
    )
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")

    resolver = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=secrets
    )
    reused_reasoning = resolver.resolve_tutor_text()
    assert reused_reasoning.model == "explicit-model"
    assert reused_reasoning.base_url == "https://models.example.test/v1"

    _seed_bound_profile(
        db_session,
        secrets,
        capability="chat",
        profile_id="chat-profile",
    ).model_name = "chat-model"
    db_session.flush()
    preferred_chat = resolver.resolve_tutor_text()
    assert preferred_chat.model == "chat-model"
    assert preferred_chat.base_url == "https://models.example.test/v1"


def test_tutor_text_resolution_rejects_missing_user_text_model(db_session, monkeypatch) -> None:
    """Falling back to an environment client without a user text binding must fail."""
    config_service = _runtime_module()
    _seed_user(db_session)
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")

    with pytest.raises(config_service.RuntimeResolutionError) as exc_info:
        config_service.RuntimeResolver(
            db_session, user_id="runtime-user", secret_store=None
        ).resolve_tutor_text()

    assert exc_info.value.code == "runtime.tutor_model_unconfigured"


def test_invalid_bound_profile_never_falls_back_to_environment(db_session, monkeypatch) -> None:
    """Treating a missing bound credential as an absent binding must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    profile = _seed_bound_profile(db_session, secrets)
    reference = db_session.query(UserSecretReference).filter_by(owner_id=profile.id).one()
    secrets.delete(reference.secret_ref)
    monkeypatch.setenv("LLM_API_KEY", "must-not-be-used")

    with pytest.raises(config_service.RuntimeResolutionError) as exc_info:
        config_service.RuntimeResolver(
            db_session, user_id="runtime-user", secret_store=secrets
        ).resolve("chat")

    assert exc_info.value.code == "runtime.credential_missing"


def test_bound_chat_provider_failure_exposes_safe_http_status_instead_of_offline_fallback(db_session) -> None:
    """Replacing a provider HTTP status with offline fallback or a generic code must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(db_session, secrets)

    resolved = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=secrets
    ).resolve("chat")
    resolved.http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        resolved.complete(role="teacher", prompt="hello")

    assert exc_info.value.error_code == "provider_http_503"


def test_embedding_profile_never_borrows_llm_credentials(db_session, monkeypatch) -> None:
    """Removing the explicit embedding API key must fail this isolation test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(db_session, secrets, capability="embedding", profile_id="embedding-profile")
    monkeypatch.setenv("LLM_API_KEY", "unrelated-llm-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "unrelated-embedding-key")

    resolved = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=secrets
    ).resolve("embedding")

    assert resolved.api_key == "profile-private-key"
    assert resolved.model == "explicit-model"
    assert resolved.dimensions == 1536


def test_bound_embedding_provider_failure_raises_stable_runtime_error(db_session) -> None:
    """Restoring retrieval degradation for a configured embedding must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(
        db_session, secrets, capability="embedding", profile_id="embedding-profile"
    )
    resolved = config_service.RuntimeResolver(
        db_session, user_id="runtime-user", secret_store=secrets
    ).resolve("embedding")
    resolved._client.http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    )

    with pytest.raises(config_service.RuntimeResolutionError) as exc_info:
        resolved.embed("query")

    assert exc_info.value.code == "runtime.provider_call_failed"


def test_explicit_non_deepseek_vision_profile_omits_thinking_parameters(db_session) -> None:
    """Sending DeepSeek thinking fields to an explicit non-DeepSeek vision URL must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(db_session, secrets, capability="vision", profile_id="vision-profile")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"supplemental_text":"ok","confidence":1,"complex_visual":false}'
                        }
                    }
                ]
            },
            request=request,
        )

    async def run():
        resolved = config_service.RuntimeResolver(
            db_session, user_id="runtime-user", secret_store=secrets
        ).resolve("vision")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolved.http_client = client
            return await resolved.analyze_image(
                b"image",
                mime_type="image/png",
                context=VisionContext(
                    filename="tiny.png",
                    file_type=DocumentFileType.IMAGE,
                    page_number=1,
                    source_element=SourceElementType.IMAGE_FILE,
                ),
            )

    asyncio.run(run())
    assert "thinking" not in seen


def test_resolver_revalidates_blank_persisted_profile_fields(db_session) -> None:
    """Trusting legacy/corrupt persisted profile strings must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    profile = _seed_bound_profile(db_session, secrets)
    profile.model_name = "   "
    db_session.flush()

    with pytest.raises(config_service.RuntimeResolutionError) as exc_info:
        config_service.RuntimeResolver(
            db_session, user_id="runtime-user", secret_store=secrets
        ).resolve("chat")

    assert exc_info.value.code == "runtime.profile_invalid"


def test_explicit_deepseek_profile_pins_flash_and_pro_to_selected_model(monkeypatch) -> None:
    """Inheriting DeepSeek flash/pro env models for an explicit profile must fail this test."""
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    monkeypatch.setenv("DEEPSEEK_FLASH_MODEL", "environment-flash")
    monkeypatch.setenv("DEEPSEEK_PRO_MODEL", "environment-pro")

    client = LLMGatewayClient(
        base_url="https://api.deepseek.com/v1",
        api_key="profile-key",
        model="profile-selected",
    )

    assert client._select_model("flash") == "profile-selected"
    assert client._select_model("pro") == "profile-selected"


def test_default_llm_timeout_allows_long_reasoning_responses(monkeypatch) -> None:
    """Restoring the old 15-second timeout must fail this real reasoning-readiness boundary."""
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="reasoning-model",
    )
    try:
        assert client.http_client.timeout.read == 60
    finally:
        client.http_client.close()


def test_llm_timeout_accepts_positive_environment_override(monkeypatch) -> None:
    """Ignoring an operator's positive timeout override must fail this configuration test."""
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "75")
    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="reasoning-model",
    )
    try:
        assert client.http_client.timeout.read == 75
    finally:
        client.http_client.close()


def test_non_timed_completion_failure_still_reports_real_latency() -> None:
    """Zeroing operational latency on normal completion failures must fail observability."""

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.01)
        return httpx.Response(503, request=request)

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="reasoning-model",
        max_retries=0,
        strict_remote_default=True,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        client.complete(role="planner", prompt="Build a roadmap.")

    assert exc_info.value.request_latency_ms >= 5
    assert exc_info.value.total_latency_ms >= exc_info.value.request_latency_ms


def test_non_stream_json_output_requests_json_object_and_records_finish_reason() -> None:
    """Dropping JSON mode or completion metadata must fail this provider-boundary test."""
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.complete(role="planner", prompt="Return JSON.", json_output=True) == '{"ok":true}'
    assert seen_payload["response_format"] == {"type": "json_object"}
    assert client.last_completion_metadata["finish_reason"] == "stop"


def test_non_stream_text_completion_omits_json_output_request() -> None:
    """Forcing JSON mode onto legacy text callers must fail this compatibility test."""
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "plain text"},
                    }
                ]
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="text-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.complete(role="teacher", prompt="Explain RAG.") == "plain text"
    assert "response_format" not in seen_payload


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [("", "stop"), ('{"partial":true}', "length")],
)
def test_non_stream_completion_rejects_empty_or_truncated_content(
    content: str, finish_reason: str
) -> None:
    """Accepting empty or length-truncated output must fail as an incomplete provider response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {"content": content},
                    }
                ]
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        client.complete(role="planner", prompt="Return JSON.", json_output=True)

    assert exc_info.value.error_code == "provider_response_incomplete"
    assert client.last_completion_metadata["finish_reason"] == finish_reason


def test_json_output_rejects_non_stop_terminal_reason() -> None:
    """Accepting content-filtered JSON as a complete structured response must fail this test."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "content_filter",
                        "message": {"content": '{"ok":true}'},
                    }
                ]
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        client.complete(role="planner", prompt="Return JSON.", json_output=True)

    assert exc_info.value.error_code == "provider_response_incomplete"
    assert client.last_completion_metadata["finish_reason"] == "content_filter"


def test_non_stream_completion_classifies_malformed_choice_as_invalid() -> None:
    """A malformed choice container must not escape the provider parsing boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [[]]}, request=request)

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        client.complete(role="planner", prompt="Return JSON.", json_output=True)

    assert exc_info.value.error_code == "provider_response_invalid"


def test_non_stream_completion_classifies_malformed_usage_as_invalid() -> None:
    """Malformed optional usage must remain inside the provider parsing boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": ["not-an-object"],
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        client.complete(role="planner", prompt="Return JSON.", json_output=True)

    assert exc_info.value.error_code == "provider_response_invalid"


def test_finish_reason_metadata_replaces_untrusted_values_with_unknown() -> None:
    """Provider-controlled finish metadata must be bounded before logging."""
    unsafe_reason = "secret-like-value\nforged-log-line"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": unsafe_reason,
                        "message": {"content": '{"ok":true}'},
                    }
                ]
            },
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="json-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError):
        client.complete(role="planner", prompt="Return JSON.", json_output=True)

    assert client.last_completion_metadata["finish_reason"] == "unknown"
    assert unsafe_reason not in str(client.last_completion_metadata)


def test_configured_vision_failure_raises_stable_runtime_error(db_session) -> None:
    """Returning a soft failed VisionResult for a configured provider must fail this test."""
    config_service = _runtime_module()
    _seed_user(db_session)
    secrets = InMemorySecretStore()
    _seed_bound_profile(db_session, secrets, capability="vision", profile_id="vision-profile")

    async def run():
        resolved = config_service.RuntimeResolver(
            db_session, user_id="runtime-user", secret_store=secrets
        ).resolve("vision")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
        ) as client:
            resolved._client.http_client = client
            with pytest.raises(config_service.RuntimeResolutionError) as exc_info:
                await resolved.analyze_image(
                    b"image",
                    mime_type="image/png",
                    context=VisionContext(
                        filename="tiny.png",
                        file_type=DocumentFileType.IMAGE,
                        page_number=1,
                        source_element=SourceElementType.IMAGE_FILE,
                    ),
                )
            return exc_info.value.code

    assert asyncio.run(run()) == "runtime.provider_call_failed"


def test_openai_compatible_stream_yields_content_deltas_and_sets_stream_flag() -> None:
    """Replacing streamed provider fragments with a completed response must fail this test."""
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                b'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                b'data: [DONE]\n\n'
            ),
            request=request,
        )

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="stream-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert list(client.stream(role="teacher", prompt="Say hello")) == ["Hel", "lo"]
    assert seen_payload["stream"] is True


def test_openai_compatible_stream_stops_without_retry_after_a_public_delta() -> None:
    """Retrying a broken response after emitting text would duplicate it and must fail this test."""

    class BrokenAfterDelta(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"once"}}]}\n\n'
            raise httpx.ReadError("provider detail must stay private")

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=BrokenAfterDelta(), request=request)

    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="stream-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        strict_remote_default=True,
    )

    iterator = client.stream(role="teacher", prompt="Say once")
    assert next(iterator) == "once"
    with pytest.raises(EvaluationProviderError) as exc_info:
        next(iterator)

    assert attempts == 1
    assert exc_info.value.error_code == "provider_request_failed"
    assert "provider detail" not in str(exc_info.value)


def test_openai_compatible_stream_sanitizes_malformed_sse_payload() -> None:
    """Leaking a parser exception for malformed provider data must fail this test."""
    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="stream-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"data: []\n\n",
                    request=request,
                )
            )
        ),
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        list(client.stream(role="teacher", prompt="Keep parser details private"))

    assert exc_info.value.error_code == "provider_response_invalid"
    assert "AttributeError" not in str(exc_info.value)


def test_openai_compatible_stream_rejects_clean_eof_without_done() -> None:
    """Treating a partial stream as completed when its terminal frame is absent must fail this test."""
    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="stream-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
                    request=request,
                )
            )
        ),
        max_retries=0,
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        list(client.stream(role="teacher", prompt="Do not complete partial output"))

    assert exc_info.value.error_code == "provider_response_invalid"
    assert "partial" not in str(exc_info.value)


def test_openai_compatible_stream_rejects_provider_error_frame() -> None:
    """Ignoring a provider error frame and returning a completed run must fail this test."""
    client = LLMGatewayClient(
        base_url="https://models.example.test/v1",
        api_key="profile-private-key",
        model="stream-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b'data: {"error":{"message":"provider secret"}}\n\n',
                    request=request,
                )
            )
        ),
        max_retries=0,
        strict_remote_default=True,
    )

    with pytest.raises(EvaluationProviderError) as exc_info:
        list(client.stream(role="teacher", prompt="Do not expose provider errors"))

    assert exc_info.value.error_code == "provider_response_invalid"
    assert "provider secret" not in str(exc_info.value)
