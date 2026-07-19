from __future__ import annotations

from datetime import datetime, timezone

from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository
from backend.app.models import LearnerProfile
from tests.conftest import register_user

from .helpers import preference_command


NOW = datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc)


def _create_memory(session_factory, *, user_id: str, key: str, source_kind: str = "explicit_user"):
    with session_factory() as session:
        command = preference_command(
            user_id=user_id,
            content={"preference_key": key, "preference_value": "value"},
            source_kind=source_kind,
            source_ref_id=None if source_kind == "explicit_user" else f"source-{key}",
            idempotency_key=f"memory-api:{key}",
            source_metadata={"private": "hidden"},
        )
        record = SQLAlchemyMemoryRepository(session).create_or_get(command, now=NOW)
        session.commit()
        return record


def test_memory_list_get_disable_and_cross_user_404(client, session_factory) -> None:
    owner = register_user(client, email="memory-owner@example.com")
    other = register_user(client, email="memory-other@example.com")
    explicit = _create_memory(session_factory, user_id=owner["user_id"], key="explicit")
    _create_memory(
        session_factory,
        user_id=owner["user_id"],
        key="system",
        source_kind="system_derived",
    )

    listed = client.get(
        "/api/memories?source_category=explicit_user_statement&status=active&limit=1&offset=0",
        headers=owner["headers"],
    )
    fetched = client.get(f"/api/memories/{explicit.id}", headers=owner["headers"])
    foreign = client.get(f"/api/memories/{explicit.id}", headers=other["headers"])
    disabled = client.post(f"/api/memories/{explicit.id}/disable", headers=owner["headers"])
    disabled_again = client.post(f"/api/memories/{explicit.id}/disable", headers=owner["headers"])

    assert listed.status_code == 200
    assert listed.json()["items"][0]["memory_id"] == explicit.id
    assert listed.json()["items"][0]["origin"] == "explicit_user_statement"
    assert fetched.status_code == 200
    forbidden = {
        "user_id",
        "source_ref_id",
        "source_metadata",
        "idempotency_key",
        "content_hash",
        "schema_version",
    }
    assert forbidden.isdisjoint(fetched.json())
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "memory.not_found"
    assert disabled.status_code == 200
    assert disabled.json()["disabled_reason"] == "user_revoked"
    assert disabled_again.json()["disabled_at"] == disabled.json()["disabled_at"]


def test_privacy_api_defaults_updates_strictly_and_preserves_other_keys(client, session_factory) -> None:
    identity = register_user(client, email="memory-privacy@example.com")
    with session_factory() as session:
        profile = session.get(LearnerProfile, identity["user_id"])
        profile.privacy_settings = {"data_scope": "v1", "analytics": {"enabled": False}}
        session.commit()

    defaults = client.get("/api/memories/privacy", headers=identity["headers"])
    updated = client.put(
        "/api/memories/privacy",
        headers=identity["headers"],
        json={
            "enabled": False,
            "allow_explicit_user": False,
            "allow_system_inference": True,
            "allow_learning_results": False,
        },
    )
    invalid = client.put(
        "/api/memories/privacy",
        headers=identity["headers"],
        json={**updated.json(), "provider_payload": {}},
    )

    assert defaults.json() == {
        "enabled": True,
        "allow_explicit_user": True,
        "allow_system_inference": False,
        "allow_learning_results": True,
    }
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert invalid.status_code == 422
    with session_factory() as session:
        stored = session.get(LearnerProfile, identity["user_id"]).privacy_settings
        assert stored["data_scope"] == "v1"
        assert stored["analytics"] == {"enabled": False}


def test_memory_openapi_contract_does_not_expose_internal_fields(client) -> None:
    schema = client.get("/openapi.json").json()
    public_properties = schema["components"]["schemas"]["MemoryPublicResponse"]["properties"]

    assert {
        "user_id",
        "source_ref_id",
        "source_metadata",
        "idempotency_key",
        "content_hash",
        "schema_version",
    }.isdisjoint(public_properties)
    assert schema["paths"]["/api/memories"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
