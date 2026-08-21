from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from tests.conftest import register_user
from tests.fakes.secret_store import InMemorySecretStore


def _model_payload(**overrides) -> dict:
    payload = {
        "name": "Study chat",
        "capability": "chat",
        "provider": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "study-chat-v1",
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_models_are_user_isolated_and_only_accept_the_supported_contract(client):
    """Removing ownership filtering or capability/provider validation must fail this test."""
    owner = register_user(client, email="config-owner@example.com")
    other = register_user(client, email="config-other@example.com")

    created = client.post("/api/config/models", headers=owner["headers"], json=_model_payload())
    assert created.status_code == 201, created.text
    model = created.json()
    assert model["name"] == "Study chat"
    assert model["capability"] == "chat"
    assert model["provider"] == "openai_compatible"
    assert "secret" not in model

    assert client.get("/api/config/models", headers=owner["headers"]).json()["models"] == [model]
    assert client.get(f"/api/config/models/{model['id']}", headers=other["headers"]).status_code == 404
    assert client.put(
        f"/api/config/models/{model['id']}", headers=other["headers"], json=_model_payload(name="stolen")
    ).status_code == 404
    assert client.delete(f"/api/config/models/{model['id']}", headers=other["headers"]).status_code == 404

    assert client.post(
        "/api/config/models", headers=owner["headers"], json=_model_payload(capability="unsupported")
    ).status_code == 422
    assert client.post(
        "/api/config/models", headers=owner["headers"], json=_model_payload(provider="other")
    ).status_code == 422
    assert client.post(
        "/api/config/models", headers=owner["headers"], json=_model_payload(capability="embedding", dimensions=1024)
    ).status_code == 422
    assert client.post(
        "/api/config/models", headers=owner["headers"], json=_model_payload(name="Study embeddings", capability="embedding", dimensions=1536)
    ).status_code == 201


def test_model_secret_replacement_and_deletion_never_return_the_secret(client):
    """Removing write-before-switch cleanup or exposing a value must fail this test."""
    from backend.app.main import app
    from backend.app.routers import config

    secrets = InMemorySecretStore()
    app.dependency_overrides[config.get_secret_store] = lambda: secrets
    try:
        owner = register_user(client, email="secret-owner@example.com")
        model = client.post("/api/config/models", headers=owner["headers"], json=_model_payload()).json()

        first = client.put(
            f"/api/config/models/{model['id']}/secret",
            headers=owner["headers"],
            json={"value": "first-private-token"},
        )
        assert first.status_code == 200
        assert first.json()["configured"] is True
        assert "first-private-token" not in first.text
        first_ref = secrets.events[-1][1]

        replacement = client.put(
            f"/api/config/models/{model['id']}/secret",
            headers=owner["headers"],
            json={"value": "second-private-token"},
        )
        assert replacement.status_code == 200
        assert "second-private-token" not in replacement.text
        assert secrets.events[-1] == ("delete", first_ref)
        assert first_ref not in secrets.values

        deleted = client.delete(f"/api/config/models/{model['id']}/secret", headers=owner["headers"])
        assert deleted.status_code == 204
        assert secrets.values == {}

        client.put(
            f"/api/config/models/{model['id']}/secret",
            headers=owner["headers"],
            json={"value": "final-private-token"},
        )
        assert client.delete(f"/api/config/models/{model['id']}", headers=owner["headers"]).status_code == 204
        assert secrets.values == {}

        short_model = client.post(
            "/api/config/models", headers=owner["headers"], json=_model_payload(name="Short secret model")
        ).json()
        short_secret = client.put(
            f"/api/config/models/{short_model['id']}/secret", headers=owner["headers"], json={"value": "abc"}
        )
        assert short_secret.status_code == 200
        assert "abc" not in short_secret.text
    finally:
        app.dependency_overrides.clear()


def test_model_test_endpoint_requires_exact_embedding_dimensions_and_sanitizes_failures(
    client, db_session, monkeypatch
):
    """Accepting a non-1536 embedding or exposing provider errors must fail this test."""
    from backend.app.main import app
    from backend.app.routers import config

    secrets = InMemorySecretStore()
    app.dependency_overrides[config.get_secret_store] = lambda: secrets
    owner = register_user(client, email="model-test-owner@example.com")
    embedding = client.post(
        "/api/config/models",
        headers=owner["headers"],
        json=_model_payload(
            name="Test embeddings", capability="embedding", model_name="embed-v1", dimensions=1536
        ),
    ).json()
    client.put(
        f"/api/config/models/{embedding['id']}/secret",
        headers=owner["headers"],
        json={"value": "model-test-secret"},
    )

    class WrongDimensionsClient:
        def embed(self, text: str) -> list[float]:
            assert text == "connection test"
            return [0.0] * 3

    monkeypatch.setattr(
        "backend.app.application.config_service.RuntimeResolver.resolve_profile",
        lambda self, model_id: WrongDimensionsClient(),
    )
    response = client.post(
        f"/api/config/models/{embedding['id']}/test", headers=owner["headers"]
    )

    assert response.status_code == 200
    assert response.json() == {"status": "failed", "code": "model_test.embedding_dimensions"}
    assert "model-test-secret" not in response.text
    assert "provider" not in response.text
    persisted = db_session.get(__import__("backend.app.models", fromlist=["UserModelProfile"]).UserModelProfile, embedding["id"])
    assert persisted.last_test_status == "failed"
    assert persisted.last_tested_at is not None

    class ExactDimensionsClient:
        def embed(self, text: str) -> list[float]:
            return [0.0] * 1536

    monkeypatch.setattr(
        "backend.app.application.config_service.RuntimeResolver.resolve_profile",
        lambda self, model_id: ExactDimensionsClient(),
    )
    try:
        success = client.post(
            f"/api/config/models/{embedding['id']}/test", headers=owner["headers"]
        )
        assert success.status_code == 200
        assert success.json() == {"status": "success", "code": None}
        db_session.expire_all()
        assert db_session.get(
            __import__("backend.app.models", fromlist=["UserModelProfile"]).UserModelProfile,
            embedding["id"],
        ).last_test_status == "success"
    finally:
        app.dependency_overrides.clear()


def test_bindings_skills_and_mcp_configuration_enforce_ownership_and_shapes(client, db_session):
    """Removing model-reference guards, MCP shape checks, or tool ownership must fail this test."""
    from backend.app.models import UserMcpTool

    owner = register_user(client, email="configuration-owner@example.com")
    attacker = register_user(client, email="configuration-attacker@example.com")
    model = client.post("/api/config/models", headers=owner["headers"], json=_model_payload()).json()

    binding = client.put(
        "/api/config/bindings/chat", headers=owner["headers"], json={"model_profile_id": model["id"]}
    )
    assert binding.status_code == 200
    assert binding.json()["capability"] == "chat"
    assert client.delete(f"/api/config/models/{model['id']}", headers=owner["headers"]).status_code == 409
    assert client.put(
        "/api/config/bindings/chat", headers=attacker["headers"], json={"model_profile_id": model["id"]}
    ).status_code == 404
    assert client.put(
        f"/api/config/models/{model['id']}", headers=owner["headers"], json=_model_payload(capability="reasoning")
    ).status_code == 409
    assert client.delete("/api/config/bindings/chat", headers=owner["headers"]).status_code == 204

    skill = client.post(
        "/api/config/skills",
        headers=owner["headers"],
        json={"name": "Explain simply", "description": "Short answers", "instructions": "Use examples.", "model_profile_id": model["id"]},
    )
    assert skill.status_code == 201
    assert client.get(f"/api/config/skills/{skill.json()['id']}", headers=attacker["headers"]).status_code == 404
    assert client.delete(f"/api/config/models/{model['id']}", headers=owner["headers"]).status_code == 409
    assert client.delete(f"/api/config/skills/{skill.json()['id']}", headers=owner["headers"]).status_code == 204

    invalid_http = client.post(
        "/api/config/mcp-servers", headers=owner["headers"], json={"name": "No URL", "transport": "streamable_http"}
    )
    invalid_stdio = client.post(
        "/api/config/mcp-servers", headers=owner["headers"], json={"name": "No command", "transport": "stdio"}
    )
    assert invalid_http.status_code == 422
    assert invalid_stdio.status_code == 422
    assert client.post(
        "/api/config/mcp-servers",
        headers=owner["headers"],
        json={"name": "Credential URL", "transport": "streamable_http", "url": "https://client:short-secret@example.com/mcp"},
    ).status_code == 422
    assert client.post(
        "/api/config/mcp-servers",
        headers=owner["headers"],
        json={"name": "Secret env", "transport": "stdio", "command": "node", "env": {"API_KEY": "short-secret"}},
    ).status_code == 422

    server = client.post(
        "/api/config/mcp-servers",
        headers=owner["headers"],
        json={"name": "Files", "transport": "stdio", "command": "node", "args": ["server.js"], "enabled": True},
    )
    assert server.status_code == 201
    server_id = server.json()["id"]
    db_session.add(UserMcpTool(id="files-read", mcp_server_id=server_id, name="read", enabled=True))
    db_session.commit()
    toggled = client.put(
        f"/api/config/mcp-servers/{server_id}/tools/read", headers=owner["headers"], json={"enabled": False}
    )
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
    assert client.put(
        f"/api/config/mcp-servers/{server_id}/tools/read", headers=attacker["headers"], json={"enabled": True}
    ).status_code == 404


def test_embedding_binding_change_enqueues_one_reindex_event_per_active_owned_document(
    client, db_session
):
    """Dropping route-to-outbox wiring or dedupe must fail this test."""
    from backend.app.application.embedding_reindex_service import EMBEDDING_REINDEX_EVENT_TYPE
    from backend.app.models import Document, DocumentChunk, DocumentIndexVersion, OutboxEvent

    owner = register_user(client, email="binding-reindex@example.com")
    document = Document(
        id="binding-reindex-document",
        owner_user_id=owner["user_id"],
        filename="active.md",
        object_key="active.md",
        mime_type="text/markdown",
        parse_status="success",
        sha256="c" * 64,
    )
    active = DocumentIndexVersion(
        id="binding-reindex-active",
        document_id=document.id,
        build_key="legacy-active",
        status="active",
        chunker_version="document-parser-v3:chunking-v2",
        embedding_provider="old-provider",
        embedding_model="old-model",
        embedding_dimensions=1536,
        chunk_count=1,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(active)
    db_session.flush()
    db_session.add(
        DocumentChunk(
            id="binding-reindex-chunk",
            document_id=document.id,
            index_version_id=active.id,
            chunk_index=1,
            content="active content",
            embedding=[0.0] * 1536,
            citation_label="active.md",
        )
    )
    db_session.commit()
    profile = client.post(
        "/api/config/models",
        headers=owner["headers"],
        json=_model_payload(
            name="Bound embeddings", capability="embedding", dimensions=1536
        ),
    ).json()

    first = client.put(
        "/api/config/bindings/embedding",
        headers=owner["headers"],
        json={"model_profile_id": profile["id"]},
    )
    second = client.put(
        "/api/config/bindings/embedding",
        headers=owner["headers"],
        json={"model_profile_id": profile["id"]},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    db_session.expire_all()
    events = db_session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == EMBEDDING_REINDEX_EVENT_TYPE)
    ).all()
    assert len(events) == 1
    assert events[0].payload_json["document_id"] == document.id
    assert events[0].payload_json["model_profile_id"] == profile["id"]

    updated = client.put(
        f"/api/config/models/{profile['id']}",
        headers=owner["headers"],
        json=_model_payload(
            name="Bound embeddings",
            capability="embedding",
            dimensions=1536,
            base_url="https://embedding-new.example.test/v1",
        ),
    )
    assert updated.status_code == 200
    db_session.expire_all()
    events = db_session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == EMBEDDING_REINDEX_EVENT_TYPE)
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
    ).all()
    assert len(events) == 2
    assert (
        events[0].payload_json["embedding_profile_identity"]
        != events[1].payload_json["embedding_profile_identity"]
    )

    disabled_payload = _model_payload(
        name="Bound embeddings",
        capability="embedding",
        dimensions=1536,
        base_url="https://embedding-disabled.example.test/v1",
    )
    disabled_payload["enabled"] = False
    assert client.put(
        f"/api/config/models/{profile['id']}",
        headers=owner["headers"],
        json=disabled_payload,
    ).status_code == 200
    disabled_payload["enabled"] = True
    assert client.put(
        f"/api/config/models/{profile['id']}",
        headers=owner["headers"],
        json=disabled_payload,
    ).status_code == 200
    db_session.expire_all()
    events = db_session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == EMBEDDING_REINDEX_EVENT_TYPE)
    ).all()
    assert len(events) == 4


def test_model_and_mcp_urls_reject_credentials_and_secret_query_values(client):
    """Removing centralized URL sanitization must fail this test."""
    owner = register_user(client, email="url-owner@example.com")
    valid = client.post(
        "/api/config/models",
        headers=owner["headers"],
        json=_model_payload(name="Versioned URL", base_url="https://api.example.com/v1?version=2026-08-14"),
    )
    assert valid.status_code == 201
    assert valid.json()["base_url"] == "https://api.example.com/v1?version=2026-08-14"

    for base_url in (
        "https://client:private@example.com/v1",
        "https://api.example.com/v1?api_key=private",
        "https://api.example.com/v1?key=private",
        "https://api.example.com/v1?token=private",
    ):
        rejected = client.post(
            "/api/config/models", headers=owner["headers"], json=_model_payload(name=base_url, base_url=base_url)
        )
        assert rejected.status_code == 422
        assert "private" not in rejected.text

    for url in (
        "https://client:private@example.com/mcp",
        "https://mcp.example.com/connect?authorization=private",
    ):
        rejected = client.post(
            "/api/config/mcp-servers",
            headers=owner["headers"],
            json={"name": url, "transport": "streamable_http", "url": url},
        )
        assert rejected.status_code == 422
        assert "private" not in rejected.text

    listed = client.get("/api/config/models", headers=owner["headers"])
    assert listed.status_code == 200
    assert "private" not in listed.text
    valid_mcp = client.post(
        "/api/config/mcp-servers",
        headers=owner["headers"],
        json={"name": "Versioned MCP", "transport": "streamable_http", "url": "https://mcp.example.com/connect?version=v1"},
    )
    assert valid_mcp.status_code == 201
    assert valid_mcp.json()["url"] == "https://mcp.example.com/connect?version=v1"


def test_model_profiles_reject_blank_identity_and_signed_secret_query_names(client):
    """Blank runtime identity or presigned/SAS query credentials must fail without reflection."""
    owner = register_user(client, email="signed-url-owner@example.com")
    for field in ("name", "model_name"):
        payload = _model_payload()
        payload[field] = "   "
        assert client.post("/api/config/models", headers=owner["headers"], json=payload).status_code == 422

    secret_value = "must-never-reflect"
    for query_name in (
        "sig",
        "signature",
        "X-Amz-Signature",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "AWSAccessKeyId",
        "X-Goog-Signature",
        "X-Goog-Credential",
        "access_token",
        "client_secret",
        "subscription-key",
        "se",
        "ss",
        "srt",
        "st",
        "sp",
        "sv",
        "skoid",
    ):
        response = client.post(
            "/api/config/models",
            headers=owner["headers"],
            json=_model_payload(
                name=f"signed-{query_name}",
                base_url=f"https://api.example.test/v1?{query_name}={secret_value}",
            ),
        )
        assert response.status_code == 422
        assert secret_value not in response.text


def test_secret_values_remain_opaque_and_blank_values_are_not_persisted(client, db_session):
    """Removing opaque-value handling or empty secret rejection must fail this test."""
    from backend.app.main import app
    from backend.app.models import UserSecretReference
    from backend.app.routers import config

    secrets = InMemorySecretStore()
    app.dependency_overrides[config.get_secret_store] = lambda: secrets
    try:
        owner = register_user(client, email="opaque-secret-owner@example.com")
        model = client.post("/api/config/models", headers=owner["headers"], json=_model_payload()).json()
        exact_value = "  opaque value with spaces  "
        stored = client.put(
            f"/api/config/models/{model['id']}/secret", headers=owner["headers"], json={"value": exact_value}
        )
        assert stored.status_code == 200
        assert list(secrets.values.values()) == [exact_value]
        for blank in ("", "   ", "\t"):
            rejected = client.put(
                f"/api/config/models/{model['id']}/secret", headers=owner["headers"], json={"value": blank}
            )
            assert rejected.status_code == 422
        assert db_session.scalars(select(UserSecretReference)).all()[0].masked_value == "********"
    finally:
        app.dependency_overrides.clear()


def test_stdio_transport_requires_command_and_rejects_stdio_only_http_fields(client):
    """Allowing blank stdio commands or HTTP stdio fields must fail this test."""
    owner = register_user(client, email="transport-owner@example.com")
    for payload in (
        {"name": "Blank command", "transport": "stdio", "command": "   "},
        {"name": "HTTP args", "transport": "streamable_http", "url": "https://mcp.example.com", "args": ["server.js"]},
        {"name": "HTTP cwd", "transport": "streamable_http", "url": "https://mcp.example.com", "working_directory": "C:/mcp"},
    ):
        assert client.post("/api/config/mcp-servers", headers=owner["headers"], json=payload).status_code == 422


def test_secret_store_has_stable_non_windows_behavior_and_one_injection_seam(monkeypatch):
    """Changing non-Windows behavior or splitting the API injection seam must fail this test."""
    from backend.app.infrastructure import secrets as secret_module
    from backend.app.routers import config

    monkeypatch.setattr(secret_module.sys, "platform", "linux")
    with pytest.raises(secret_module.SecretStoreUnavailable, match=r"^Secret store is unavailable\.$"):
        secret_module.WindowsCredentialManagerSecretStore()
    assert config.get_secret_store() is None


def test_mcp_secret_lifecycle_and_stdio_changes_clear_trust(client, db_session):
    """Removing MCP secret cleanup or trust invalidation must fail this test."""
    from backend.app.main import app
    from backend.app.models import UserMcpServer
    from backend.app.routers import config

    secrets = InMemorySecretStore()
    app.dependency_overrides[config.get_secret_store] = lambda: secrets
    try:
        owner = register_user(client, email="mcp-secret-owner@example.com")
        server = client.post(
            "/api/config/mcp-servers",
            headers=owner["headers"],
            json={"name": "Trusted stdio", "transport": "stdio", "command": "node", "args": ["first.js"]},
        ).json()
        first = client.put(
            f"/api/config/mcp-servers/{server['id']}/secrets/api_key",
            headers=owner["headers"],
            json={"value": "first-mcp-secret"},
        )
        assert first.status_code == 200
        first_ref = secrets.events[-1][1]
        replacement = client.put(
            f"/api/config/mcp-servers/{server['id']}/secrets/api_key",
            headers=owner["headers"],
            json={"value": "second-mcp-secret"},
        )
        assert replacement.status_code == 200
        assert secrets.events[-1] == ("delete", first_ref)
        assert client.delete(
            f"/api/config/mcp-servers/{server['id']}/secrets/api_key", headers=owner["headers"]
        ).status_code == 204
        assert secrets.values == {}

        persisted = db_session.get(UserMcpServer, server["id"])
        persisted.trust_fingerprint = "f" * 64
        persisted.trusted_at = datetime.now(timezone.utc)
        db_session.commit()
        updated = client.put(
            f"/api/config/mcp-servers/{server['id']}",
            headers=owner["headers"],
            json={"name": "Trusted stdio", "transport": "stdio", "command": "node", "args": ["second.js"]},
        )
        assert updated.status_code == 200
        assert updated.json()["trust_fingerprint"] is None
        assert updated.json()["trusted_at"] is None
        persisted = db_session.get(UserMcpServer, server["id"])
        persisted.trust_fingerprint = "a" * 64
        persisted.trusted_at = datetime.now(timezone.utc)
        db_session.commit()
        command_updated = client.put(
            f"/api/config/mcp-servers/{server['id']}",
            headers=owner["headers"],
            json={"name": "Trusted stdio", "transport": "stdio", "command": "bun", "args": ["second.js"]},
        )
        assert command_updated.status_code == 200
        assert command_updated.json()["trust_fingerprint"] is None
        assert command_updated.json()["trusted_at"] is None
    finally:
        app.dependency_overrides.clear()
