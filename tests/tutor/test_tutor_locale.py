from types import SimpleNamespace

import pytest

from adaptive_tutor.tutor.context_services import TeacherService, TutorLocaleMismatchError
from adaptive_tutor.tutor.models import ConversationState, EvidenceState, ExecutionState, LearningState, TutorWorkflowState
from backend.app.models import LearningGoal
from tests.conftest import register_user


class _LocaleLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _state(*, locale: str) -> dict:
    return {
        "request": SimpleNamespace(user_message="Explain the trade-off.", metadata={"locale": locale}),
        "tutor_context": None,
        "selected_evidence_items": [],
        "retrieved_context": [],
        "tool_results": [],
        "workflow_state": TutorWorkflowState(
            conversation=ConversationState(thread_id="thread-1", user_id="user-1"),
            learning=LearningState(goal_id="goal-1"),
            evidence=EvidenceState(),
            execution=ExecutionState(run_id="run-1", graph_version="test"),
        ),
        "audit_log": [],
    }


def test_teacher_rewrites_one_obviously_wrong_language_response() -> None:
    llm = _LocaleLLM(["This answer is in English.", "这是中文回答。"])
    state = _state(locale="zh-CN")

    TeacherService().teach(state, SimpleNamespace(llm_client=llm, teacher_delta_callback=None))

    assert state["final_answer"] == "这是中文回答。"
    assert len(llm.calls) == 2
    assert "Simplified Chinese" in llm.calls[0]["response_envelope"]
    assert "Simplified Chinese" in llm.calls[1]["response_envelope"]


def test_teacher_does_not_expose_response_after_one_failed_language_rewrite() -> None:
    llm = _LocaleLLM(["This answer is in English.", "Still English."])

    with pytest.raises(TutorLocaleMismatchError):
        TeacherService().teach(_state(locale="zh-CN"), SimpleNamespace(llm_client=llm, teacher_delta_callback=None))

    assert len(llm.calls) == 2


def test_tutor_without_a_bound_text_model_fails_safely_instead_of_echoing(client, session_factory) -> None:
    identity = register_user(client, email="tutor-without-model@example.com")
    with session_factory() as session:
        session.add(
            LearningGoal(
                id="goal-without-model",
                user_id=identity["user_id"],
                title="Safe tutor failure",
                target_outcome="Do not show an offline echo",
                weekly_hours_target=4,
            )
        )
        session.commit()

    response = client.post(
        "/api/tutor/chat",
        headers=identity["headers"],
        json={
            "goal_id": "goal-without-model",
            "thread_id": "thread-without-model",
            "message": "What model are you?",
            "locale": "en-US",
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "runtime.tutor_model_unconfigured"
    assert "teacher:" not in response.text
