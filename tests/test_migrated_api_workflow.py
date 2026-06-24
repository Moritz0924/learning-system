from collections.abc import Generator

from alembic.command import upgrade
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import get_session
from backend.app.main import app


def _migrated_session_factory(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migrated_workflow.db'}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "head")

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    return engine, sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def test_api_workflow_works_against_alembic_migrated_database(tmp_path):
    engine, factory = _migrated_session_factory(tmp_path)

    def override_get_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        goal_response = client.post(
            "/api/goals",
            json={
                "user_id": "migrated-user",
                "email": "migrated@example.com",
                "display_name": "Migrated Learner",
                "title": "Learn AI application development",
                "target_outcome": "Ship a working RAG tutor",
                "deadline": "2026-08-15",
                "weekly_hours_target": 10,
                "learning_preferences": {"style": "coach_then_code"},
            },
        )
        assert goal_response.status_code == 201
        goal = goal_response.json()

        diagnosis_response = client.post(
            "/api/onboarding/diagnosis",
            json={
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "self_assessment": {
                    "python_level": 4,
                    "api_level": 3,
                    "llm_level": 2,
                    "rag_level": 1,
                    "langgraph_level": 0,
                },
                "submitted_answers": {
                    "questions": [
                        {"node_code": "python_foundations", "is_correct": True},
                        {"node_code": "rag_foundations", "is_correct": False},
                    ]
                },
            },
        )
        assert diagnosis_response.status_code == 201

        headers = {"X-User-Id": goal["user_id"]}
        state_response = client.get(f"/api/state/current?goal_id={goal['goal_id']}", headers=headers)
        assert state_response.status_code == 200
        first_task = state_response.json()["today_tasks"][0]
        node_id = first_task["knowledge_node_id"]

        start_response = client.post(
            f"/api/tasks/{first_task['id']}/start",
            json={"user_id": goal["user_id"]},
        )
        assert start_response.status_code == 200
        complete_response = client.post(
            f"/api/tasks/{first_task['id']}/complete",
            json={
                "user_id": goal["user_id"],
                "duration_minutes": 20,
                "evidence": {"note": "completed migrated workflow task"},
            },
        )
        assert complete_response.status_code == 200

        chat_response = client.post(
            "/api/tutor/chat",
            json={
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "thread_id": "migrated-thread",
                "message": "Explain RAG with sources.",
            },
        )
        assert chat_response.status_code == 200
        assert chat_response.json()["citations"]

        assessment_response = client.post(
            "/api/assessments",
            json={
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "thread_id": "migrated-thread",
                "assessment_type": "daily",
                "knowledge_node_ids": [node_id],
            },
        )
        assert assessment_response.status_code == 201
        assessment = assessment_response.json()

        submit_response = client.post(
            f"/api/assessments/{assessment['assessment_id']}/submit",
            json={
                "user_id": goal["user_id"],
                "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
            },
        )
        assert submit_response.status_code == 200
        assert submit_response.json()["mastery_updates"]

        replan_response = client.post(
            "/api/plans/replan",
            json={
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "thread_id": "migrated-thread",
                "message": "Please rebalance my plan based on the latest evidence.",
            },
        )
        assert replan_response.status_code == 200
        replan = replan_response.json()
        assert replan["new_plan_id"] is None

        apply_response = client.post(
            f"/api/plans/adjustments/{replan['adjustment_id']}/apply",
            json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
        )
        assert apply_response.status_code == 200
        assert apply_response.json()["new_plan_id"]

        document_response = client.post(
            "/api/documents/upload",
            json={
                "user_id": goal["user_id"],
                "filename": "rag-notes.md",
                "mime_type": "text/markdown",
                "content": "# RAG\nGround answers in trusted chunks.",
            },
        )
        assert document_response.status_code == 201
        assert document_response.json()["parse_status"] == "success"

        with factory() as session:
            assert session.execute(text("select count(*) from learning_state_snapshots")).scalar_one() == 1
            assert session.execute(text("select count(*) from plan_adjustments")).scalar_one() >= 1
            assert session.execute(text("select count(*) from document_chunks")).scalar_one() >= 1
            assert session.execute(text("select count(*) from learning_sessions")).scalar_one() >= 1
            assert session.execute(text("select count(*) from learning_events")).scalar_one() >= 3
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
