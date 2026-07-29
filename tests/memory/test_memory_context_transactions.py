from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.application.tutor_service import answer_tutor_question
from backend.app.application.memory_context_service import MemoryContextOwnershipError
from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from backend.app.models import AgentRun, ConversationThread
from tests.conftest import register_user
from tests.memory.helpers import preference_command


def _create_goal(client, *, email: str) -> dict[str, Any]:
    identity = register_user(client, email=email, display_name="Memory Context Learner")
    response = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json={
            "title": "Learn AI application development",
            "target_outcome": "Build a safe tutor",
            "deadline": "2026-09-01",
            "weekly_hours_target": 8,
            "learning_preferences": {"style": "examples_first"},
            "self_assessment": {"python_level": 3, "api_level": 2},
            "submitted_answers": {"questions": []},
        },
    )
    assert response.status_code == 201, response.text
    return {**identity, **response.json()["goal"]}


class _TransactionCheckingLLM:
    instances: list["_TransactionCheckingLLM"] = []
    session = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.last_completion_metadata = {
            "mode": "test",
            "is_remote": False,
            "model": "transaction-check",
        }
        self.__class__.instances.append(self)

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context=None,
        conversation_context=None,
        context=None,
    ) -> str:
        assert self.__class__.session is not None
        assert self.__class__.session.in_transaction() is False
        self.calls.append(
            {
                "role": role,
                "prompt": prompt,
                "tutor_context": tutor_context,
                "conversation_context": conversation_context,
                "context": list(context or []),
            }
        )
        return "Detached memory-aware answer"


def test_chat_ends_read_transaction_before_llm_and_persists_minimal_memory_audit(
    client,
    session_factory,
    monkeypatch,
):
    goal = _create_goal(client, email="memory-transaction@example.com")
    private_value = "examples with a private marker"
    with session_factory() as writer:
        memory = SQLAlchemyMemoryRepository(writer).create_or_get(
            preference_command(
                user_id=goal["user_id"],
                idempotency_key="private-idempotency-key",
                content={
                    "preference_key": "explanation_style",
                    "preference_value": private_value,
                },
                source_metadata={"private_origin": "settings-screen"},
            )
        )
        writer.commit()

    monkeypatch.setattr(
        "backend.app.application.engine.LLMGatewayClient",
        _TransactionCheckingLLM,
    )
    _TransactionCheckingLLM.instances = []
    with session_factory() as chat_session:
        _TransactionCheckingLLM.session = chat_session
        payload = answer_tutor_question(
            chat_session,
            user_id=goal["user_id"],
            goal_id=goal["goal_id"],
            thread_id="memory-transaction-thread",
            message="How should I study this task?",
        )

    assert payload["final_answer"] == "Detached memory-aware answer"
    assert payload["runtime_metadata"]["memory"] == {
        "selected_count": 1,
        "skipped_by_budget": 0,
        "policy_version": "memory-context-v1",
    }
    public_runtime = json.dumps(payload["runtime_metadata"], ensure_ascii=False)
    assert memory.id not in public_runtime
    assert private_value not in public_runtime
    assert len(_TransactionCheckingLLM.instances) == 1
    provider_call = _TransactionCheckingLLM.instances[0].calls[0]
    assert [item.memory_id for item in provider_call["tutor_context"].long_term_memories] == [memory.id]
    assert provider_call["conversation_context"] is None

    with session_factory() as reader:
        agent_run = reader.scalar(
            select(AgentRun)
            .where(
                AgentRun.user_id == goal["user_id"],
                AgentRun.trigger_type == "chat",
            )
            .order_by(AgentRun.created_at.desc())
        )
        assert agent_run is not None
        assert agent_run.goal_id == goal["goal_id"]
        assert UUID(agent_run.correlation_id).version == 4
        assert len(agent_run.request_hash) == 64
        assert agent_run.node_trace
        assert agent_run.started_at is not None
        assert agent_run.completed_at is not None
        assert agent_run.input_snapshot["memory_context"] == {
            "selected_memory_ids": [memory.id],
            "policy_version": "memory-context-v1",
        }
        private_audit = json.dumps(agent_run.input_snapshot, ensure_ascii=False)
        assert private_value not in private_audit
        assert "private-idempotency-key" not in private_audit
        assert "settings-screen" not in private_audit


def test_memory_context_failure_rolls_back_and_never_calls_llm(
    client,
    session_factory,
    monkeypatch,
):
    goal = _create_goal(client, email="memory-failure@example.com")
    llm_called = False

    class _UnexpectedLLM:
        def __init__(self) -> None:
            self.last_completion_metadata = {}

        def complete(self, **kwargs: Any) -> str:
            nonlocal llm_called
            llm_called = True
            raise AssertionError("LLM must not run after memory context failure")

    def fail_memory_build(*args: Any, **kwargs: Any):
        raise MemoryContextOwnershipError("Memory context ownership validation failed.")

    monkeypatch.setattr(
        "backend.app.application.engine.LLMGatewayClient",
        _UnexpectedLLM,
    )
    monkeypatch.setattr(
        "backend.app.application.memory_context_service.MemoryContextService.build",
        fail_memory_build,
    )

    with session_factory() as session:
        with pytest.raises(MemoryContextOwnershipError):
            answer_tutor_question(
                session,
                user_id=goal["user_id"],
                goal_id=goal["goal_id"],
                thread_id="memory-failure-thread",
                message="Do not call the provider.",
            )
        assert session.in_transaction() is False

    assert llm_called is False
    with session_factory() as reader:
        failed_chat_run = reader.scalar(
            select(AgentRun).where(
                AgentRun.user_id == goal["user_id"],
                AgentRun.thread_id == "memory-failure-thread",
            )
        )
        assert failed_chat_run is None


def test_no_memory_chat_keeps_empty_context_and_existing_audit_path(
    client,
    session_factory,
    monkeypatch,
):
    goal = _create_goal(client, email="no-memory-context@example.com")
    monkeypatch.setattr(
        "backend.app.application.engine.LLMGatewayClient",
        _TransactionCheckingLLM,
    )
    _TransactionCheckingLLM.instances = []

    with session_factory() as chat_session:
        _TransactionCheckingLLM.session = chat_session
        payload = answer_tutor_question(
            chat_session,
            user_id=goal["user_id"],
            goal_id=goal["goal_id"],
            thread_id="no-memory-thread",
            message="Use the existing no-memory path.",
        )

    assert payload["final_answer"] == "Detached memory-aware answer"
    assert payload["runtime_metadata"]["memory"] == {
        "selected_count": 0,
        "skipped_by_budget": 0,
        "policy_version": "memory-context-v1",
    }
    provider_context = _TransactionCheckingLLM.instances[0].calls[0]["tutor_context"]
    assert provider_context.long_term_memories == []
    with session_factory() as reader:
        agent_run = reader.scalar(
            select(AgentRun)
            .join(ConversationThread, ConversationThread.id == AgentRun.thread_id)
            .where(
                AgentRun.user_id == goal["user_id"],
                AgentRun.goal_id == goal["goal_id"],
                ConversationThread.legacy_key == "no-memory-thread",
            )
        )
        assert agent_run is not None
        assert agent_run.thread_id != "no-memory-thread"
        assert agent_run.input_snapshot["thread_id"] == agent_run.thread_id
        assert agent_run.input_snapshot["memory_context"] == {
            "selected_memory_ids": [],
            "policy_version": "memory-context-v1",
        }
