from adaptive_tutor.phase2.schemas import TutorRunRequest

from backend.app.application.engine import (
    DEFAULT_TUTOR_RAG_TOP_K,
    MAX_TUTOR_RAG_TOP_K,
    MIN_TUTOR_RAG_TOP_K,
    _prepare_tutor_context,
    _tutor_rag_top_k,
)
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.models import LearnerProfile, LearningGoal, User


def test_tutor_rag_top_k_defaults_to_five_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("TUTOR_RAG_TOP_K", raising=False)
    assert _tutor_rag_top_k() == DEFAULT_TUTOR_RAG_TOP_K


def test_tutor_rag_top_k_reads_configured_value(monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_RAG_TOP_K", "3")
    assert _tutor_rag_top_k() == 3


def test_tutor_rag_top_k_is_clamped_to_supported_range(monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_RAG_TOP_K", "99")
    assert _tutor_rag_top_k() == MAX_TUTOR_RAG_TOP_K
    monkeypatch.setenv("TUTOR_RAG_TOP_K", "0")
    assert _tutor_rag_top_k() == MIN_TUTOR_RAG_TOP_K


def test_tutor_rag_top_k_falls_back_on_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_RAG_TOP_K", "not-a-number")
    assert _tutor_rag_top_k() == DEFAULT_TUTOR_RAG_TOP_K


def test_prepare_tutor_context_uses_configured_top_k(session_factory, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_RAG_TOP_K", "3")
    captured = {}

    def capture_retrieve(self, query, *, top_k, user_id):
        captured.update(query=query, top_k=top_k, user_id=user_id)
        return []

    monkeypatch.setattr(SQLAlchemyRagRepository, "retrieve", capture_retrieve)
    with session_factory() as session:
        session.add(User(id="user-1", email="rag-topk@example.com", normalized_email="rag-topk@example.com", display_name="RAG TopK"))
        session.flush()
        session.add(LearnerProfile(user_id="user-1"))
        session.add(LearningGoal(id="goal-1", user_id="user-1", title="RAG", target_outcome="Learn", weekly_hours_target=4))
        session.commit()
        result = _prepare_tutor_context(session, TutorRunRequest(trigger_type="chat", user_id="user-1", goal_id="goal-1", thread_id="thread-1", user_message="Explain RAG"))

    assert captured == {"query": "Explain RAG", "top_k": 3, "user_id": "user-1"}
    assert result.retrieved_context == []
