from sqlalchemy import select

from backend.app.models import BaselineDiagnostic, LearningStateSnapshot
from backend.app.services.learning import create_goal, submit_onboarding_diagnosis


def test_repeated_diagnosis_keeps_one_current_snapshot_per_user_goal(db_session):
    goal = create_goal(
        db_session,
        user_id="user-stage1",
        email="stage1@example.com",
        display_name="Stage One Learner",
        title="Build AI apps",
        target_outcome="Ship an AI tutor demo",
        deadline="2026-08-01",
        weekly_hours_target=8,
        learning_preferences={"style": "examples_first"},
    )

    first = submit_onboarding_diagnosis(
        db_session,
        user_id=goal.user_id,
        goal_id=goal.id,
        self_assessment={
            "python_level": 1,
            "api_level": 0,
            "llm_level": 0,
            "rag_level": 0,
            "langgraph_level": 0,
        },
        submitted_answers={"questions": []},
    )
    second = submit_onboarding_diagnosis(
        db_session,
        user_id=goal.user_id,
        goal_id=goal.id,
        self_assessment={
            "python_level": 4,
            "api_level": 3,
            "llm_level": 3,
            "rag_level": 1,
            "langgraph_level": 0,
        },
        submitted_answers={"questions": []},
    )

    snapshots = db_session.scalars(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == goal.user_id,
            LearningStateSnapshot.goal_id == goal.id,
        )
    ).all()
    diagnostics = db_session.scalars(
        select(BaselineDiagnostic).where(
            BaselineDiagnostic.user_id == goal.user_id,
            BaselineDiagnostic.goal_id == goal.id,
        )
    ).all()

    assert len(diagnostics) == 2
    assert len(snapshots) == 1
    assert snapshots[0].baseline_diagnostic_id == second.baseline_diagnostic_id
    assert snapshots[0].active_plan_version == 2
    assert snapshots[0].generated_from["baseline_diagnostic_id"] == second.baseline_diagnostic_id
    assert first.baseline_diagnostic_id != second.baseline_diagnostic_id
