from __future__ import annotations

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
    app.dependency_overrides[config.get_optional_secret_store] = lambda: secrets
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
