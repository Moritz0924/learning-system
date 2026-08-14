from __future__ import annotations

import asyncio
import json

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


def test_bound_chat_provider_failure_is_explicit_instead_of_offline_fallback(db_session) -> None:
    """Restoring degraded/offline fallback for a bound profile must fail this test."""
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

    assert exc_info.value.error_code == "provider_request_failed"


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
