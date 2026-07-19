from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.models import AgentRun, Memory
from backend.app.application.memory_privacy_service import LONG_TERM_MEMORY_PRIVACY_KEY
from backend.app.domain.memory import MemoryPrivacySettings
from backend.app.models import LearnerProfile
from tests.conftest import register_user


REQUEST_ID = "00000000-0000-4000-8000-000000000111"


def _goal(client, email: str) -> dict:
    identity = register_user(client, email=email)
    response = client.post(
        "/api/goals",
        headers=identity["headers"],
        json={
            "title": "Learn memory systems",
            "target_outcome": "Ship M3",
            "deadline": "2026-12-31",
            "weekly_hours_target": 5,
            "learning_preferences": {},
            "available_slots": {},
        },
    )
    assert response.status_code == 201, response.text
    return {**identity, "goal_id": response.json()["goal_id"]}


def _chat_payload(goal_id: str, *, value: str = "examples") -> dict:
    return {
        "goal_id": goal_id,
        "thread_id": "memory-write-thread",
        "message": "Explain retrieval.",
        "memory_declaration": {
            "memory_type": "learning_preference",
            "request_id": REQUEST_ID,
            "preference_key": "explanation_style",
            "preference_value": value,
        },
    }


def test_explicit_chat_declaration_writes_once_reuses_and_returns_minimal_audit(
    client,
    session_factory,
) -> None:
    goal = _goal(client, "chat-memory@example.com")

    created = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"]),
    )
    retried = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"]),
    )

    assert created.status_code == 200, created.text
    assert retried.status_code == 200, retried.text
    assert created.json()["runtime_metadata"]["memory_write"] == {
        "candidate_count": 1,
        "approved_count": 1,
        "saved_count": 1,
        "rejected_count": 0,
        "conflict_count": 0,
        "policy_version": "memory-gate-v1",
    }
    assert retried.json()["runtime_metadata"]["memory_write"]["saved_count"] == 1
    with session_factory() as session:
        rows = list(session.scalars(select(Memory).where(Memory.user_id == goal["user_id"])))
        assert len(rows) == 1
        run = session.scalar(
            select(AgentRun)
            .where(AgentRun.user_id == goal["user_id"])
            .order_by(AgentRun.created_at.desc())
        )
        item = run.input_snapshot["memory_gate"]["items"][0]
        assert item == {
            "candidate_id": item["candidate_id"],
            "origin": "explicit_user_statement",
            "decision": "approved",
            "reason_code": "policy_approved",
            "status": "reused",
            "write_reason_code": "existing_match",
            "memory_id": rows[0].id,
        }
        private_audit = json.dumps(run.input_snapshot, ensure_ascii=False)
        assert "examples" not in private_audit
        assert "memory-v1:explicit" not in private_audit
        assert "source_metadata" not in private_audit


def test_same_explicit_uuid_with_changed_content_returns_stable_409(client, session_factory) -> None:
    goal = _goal(client, "chat-conflict@example.com")
    first = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"], value="examples"),
    )

    conflict = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"], value="concise"),
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "memory.idempotency_conflict"
    with session_factory() as session:
        rows = list(session.scalars(select(Memory).where(Memory.user_id == goal["user_id"])))
        assert len(rows) == 1
        assert rows[0].content_json["preference_value"] == "examples"


def test_known_explicit_conflict_is_rejected_before_provider_call(client, session_factory, monkeypatch) -> None:
    goal = _goal(client, "chat-preflight@example.com")
    first = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"], value="examples"),
    )
    assert first.status_code == 200

    class _UnexpectedProvider:
        def __init__(self) -> None:
            raise AssertionError("known memory conflict must fail before provider construction")

    monkeypatch.setattr("backend.app.application.engine.LLMGatewayClient", _UnexpectedProvider)

    conflict = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"], value="changed"),
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "memory.idempotency_conflict"


def test_write_transaction_rechecks_privacy_changed_while_provider_runs(
    client,
    session_factory,
    monkeypatch,
) -> None:
    goal = _goal(client, "chat-privacy-race@example.com")

    class _PrivacyClosingProvider:
        def __init__(self) -> None:
            self.last_completion_metadata = {
                "mode": "test",
                "is_remote": False,
                "model": "privacy-race",
            }

        def complete(self, **kwargs) -> str:
            with session_factory() as writer:
                profile = writer.get(LearnerProfile, goal["user_id"])
                profile.privacy_settings = {
                    **dict(profile.privacy_settings or {}),
                    LONG_TERM_MEMORY_PRIVACY_KEY: MemoryPrivacySettings(enabled=False).model_dump(),
                }
                writer.commit()
            return "Privacy changed while the provider was running."

    monkeypatch.setattr("backend.app.application.engine.LLMGatewayClient", _PrivacyClosingProvider)

    response = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["runtime_metadata"]["memory_write"] == {
        "candidate_count": 1,
        "approved_count": 1,
        "saved_count": 0,
        "rejected_count": 1,
        "conflict_count": 0,
        "policy_version": "memory-gate-v1",
    }
    with session_factory() as reader:
        assert reader.scalar(select(Memory).where(Memory.user_id == goal["user_id"])) is None
        run = reader.scalar(
            select(AgentRun)
            .where(AgentRun.user_id == goal["user_id"])
            .order_by(AgentRun.created_at.desc())
        )
        assert run.input_snapshot["memory_gate"]["items"][0]["status"] == "rejected"
        assert run.input_snapshot["memory_gate"]["items"][0]["write_reason_code"] == "memory_privacy_disabled"


def test_chat_declaration_is_strict_and_privacy_disabled_is_audited_rejection(client, session_factory) -> None:
    goal = _goal(client, "chat-privacy@example.com")
    invalid_payload = _chat_payload(goal["goal_id"])
    invalid_payload["memory_declaration"]["source_kind"] = "explicit_user"
    invalid = client.post("/api/tutor/chat", headers=goal["headers"], json=invalid_payload)
    oversized = _chat_payload(goal["goal_id"])
    oversized["memory_declaration"]["preference_value"] = ["x" * 1000] * 20
    oversized_response = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=oversized,
    )
    privacy = client.put(
        "/api/memories/privacy",
        headers=goal["headers"],
        json={
            "enabled": False,
            "allow_explicit_user": True,
            "allow_system_inference": False,
            "allow_learning_results": True,
        },
    )
    rejected = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json=_chat_payload(goal["goal_id"]),
    )

    assert invalid.status_code == 422
    assert oversized_response.status_code == 422
    assert oversized_response.json()["detail"]["code"] == "memory.invalid_declaration"
    assert privacy.status_code == 200
    assert rejected.status_code == 200
    assert rejected.json()["runtime_metadata"]["memory_write"] == {
        "candidate_count": 1,
        "approved_count": 0,
        "saved_count": 0,
        "rejected_count": 1,
        "conflict_count": 0,
        "policy_version": "memory-gate-v1",
    }
    with session_factory() as session:
        assert session.scalar(select(Memory).where(Memory.user_id == goal["user_id"])) is None


def test_long_term_goal_declaration_is_bound_to_current_goal_and_openapi_hides_server_fields(
    client,
    session_factory,
) -> None:
    goal = _goal(client, "chat-goal-memory@example.com")
    response = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json={
            "goal_id": goal["goal_id"],
            "thread_id": "goal-memory-thread",
            "message": "Help me plan toward this outcome.",
            "memory_declaration": {
                "memory_type": "long_term_goal",
                "request_id": "00000000-0000-4000-8000-000000000222",
                "title": "Ship a grounded tutor",
                "target_outcome": "Deploy with transaction-safe memory",
                "deadline": "2026-12-31",
            },
        },
    )

    assert response.status_code == 200, response.text
    with session_factory() as session:
        memory = session.scalar(select(Memory).where(Memory.user_id == goal["user_id"]))
        assert memory.goal_id == goal["goal_id"]
        assert memory.source_kind == "explicit_user"
        assert memory.content_json["deadline"] == "2026-12-31"

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    declaration_schema = json.dumps(
        {
            "preference": schemas["LearningPreferenceDeclaration"],
            "goal": schemas["LongTermGoalDeclaration"],
        }
    )
    for forbidden in (
        "user_id",
        "goal_id",
        "source_kind",
        "source_metadata",
        "importance",
        "confidence",
        "idempotency_key",
    ):
        assert forbidden not in declaration_schema
