from datetime import date, datetime

import pytest
from sqlalchemy import select

from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.models import LearnerProfile, LearningEvent, LearningGoal, MasteryRecord, PlanTask, User
from backend.app.services.learning import create_goal, submit_onboarding_diagnosis


def _create_personalized_goal(db_session, *, user_id: str = "context-user"):
    db_session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.com",
            display_name="Context Learner",
            status="active",
        )
    )
    db_session.commit()
    goal = create_goal(
        db_session,
        user_id=user_id,
        title="Build a grounded tutor",
        target_outcome="Ship a personalized RAG tutor",
        deadline="2026-08-15",
        weekly_hours_target=8,
        learning_preferences={"style": "examples_first"},
    )
    profile = db_session.get(LearnerProfile, user_id)
    assert profile is not None
    profile.learning_preferences = {"style": "concept_first", "tone": "concise"}
    submit_onboarding_diagnosis(
        db_session,
        user_id=user_id,
        goal_id=goal.id,
        self_assessment={
            "python_level": 2,
            "api_level": 1,
            "llm_level": 1,
            "rag_level": 0,
            "langgraph_level": 0,
        },
        submitted_answers={"questions": []},
    )
    task = db_session.scalar(
        select(PlanTask)
        .where(PlanTask.user_id == user_id, PlanTask.goal_id == goal.id)
        .order_by(PlanTask.scheduled_day, PlanTask.id)
    )
    assert task is not None
    task.status = "active"
    task.title = "RAG foundations"
    task.objective = "Explain retrieval before generation"
    task.estimated_minutes = 35

    mastery = db_session.scalar(
        select(MasteryRecord).where(
            MasteryRecord.user_id == user_id,
            MasteryRecord.goal_id == goal.id,
            MasteryRecord.knowledge_node_id == task.knowledge_node_id,
        )
    )
    if mastery is None:
        mastery = MasteryRecord(
            id=f"mastery-{user_id}",
            user_id=user_id,
            goal_id=goal.id,
            knowledge_node_id=task.knowledge_node_id,
            mastery_score=42,
            confidence=0.8,
            evidence_count=3,
            source_breakdown={},
        )
        db_session.add(mastery)
    else:
        mastery.mastery_score = 42
        mastery.confidence = 0.8
        mastery.evidence_count = 3

    db_session.add(
        LearningEvent(
            id=f"event-{user_id}",
            user_id=user_id,
            goal_id=goal.id,
            task_id=task.id,
            session_id=None,
            event_type="task_completed",
            source="task_api",
            event_payload={
                "task_title": "Earlier retrieval exercise",
                "duration_minutes": 28,
                "evidence": {"notes": "do not expose arbitrary nested evidence"},
            },
            occurred_at=datetime(2026, 7, 16, 9, 30),
        )
    )
    db_session.commit()
    return goal, task


def test_state_repository_loads_personalized_goal_preferences_task_mastery_and_safe_events(db_session):
    goal, task = _create_personalized_goal(db_session)

    context = SQLAlchemyStateRepository(db_session).load_context(goal.user_id, goal.id)

    assert context["learning_goal"] == {
        "goal_id": goal.id,
        "title": "Build a grounded tutor",
        "target_outcome": "Ship a personalized RAG tutor",
        "domain": "ai_app_dev",
        "deadline": date(2026, 8, 15),
        "weekly_hours_target": 8,
    }
    assert context["learning_preferences"] == {"style": "examples_first", "tone": "concise"}
    assert context["current_task"] == {
        "task_id": task.id,
        "title": "RAG foundations",
        "objective": "Explain retrieval before generation",
        "task_type": task.task_type,
        "knowledge_node_id": task.knowledge_node_id,
        "knowledge_node_ids": [task.knowledge_node_id],
        "estimated_minutes": 35,
        "status": "active",
    }
    assert context["mastery_summary"][task.knowledge_node_id] == {
        "score": 42,
        "confidence": 0.8,
        "evidence_count": 3,
    }
    assert context["recent_learning_events"][-1] == {
        "event_type": "task_completed",
        "source": "task_api",
        "task_id": task.id,
        "occurred_at": datetime(2026, 7, 16, 9, 30),
        "details": {"task_title": "Earlier retrieval exercise", "duration_minutes": 28},
    }


def test_state_repository_uses_goal_preferences_when_profile_is_missing(db_session):
    db_session.add(User(id="profileless-user", email="profileless@example.com", display_name="No Profile"))
    db_session.commit()
    goal = LearningGoal(
        id="profileless-goal",
        user_id="profileless-user",
        title="Learn RAG",
        domain="ai_app_dev",
        target_outcome="Explain grounded generation",
        deadline=None,
        weekly_hours_target=4,
        status="active",
        learning_preferences={"style": "visual"},
    )
    db_session.add(goal)
    db_session.commit()

    context = SQLAlchemyStateRepository(db_session).load_context("profileless-user", goal.id)

    assert context["learning_preferences"] == {"style": "visual"}
    assert context["learning_goal"]["goal_id"] == goal.id


def test_state_repository_rejects_goal_owned_by_another_user(db_session):
    goal, _ = _create_personalized_goal(db_session, user_id="owner-user")
    db_session.add(User(id="other-user", email="other@example.com", display_name="Other Learner"))
    db_session.commit()

    with pytest.raises(LookupError, match="learning goal not found"):
        SQLAlchemyStateRepository(db_session).load_context("other-user", goal.id)
