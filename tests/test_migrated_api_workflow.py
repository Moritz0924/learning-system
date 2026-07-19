from collections.abc import Generator

from alembic.command import upgrade
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import enable_sqlite_foreign_keys, get_session
from backend.app.main import app


def _migrated_session_factory(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migrated_workflow.db'}"
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    upgrade(config, "head")

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    enable_sqlite_foreign_keys(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def test_authenticated_learning_workflow_works_against_alembic_head(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    engine, factory = _migrated_session_factory(tmp_path)

    def override_get_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "migrated@example.com",
                "password": "correct horse battery staple",
                "display_name": "Migrated Learner",
            },
        )
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

        onboarding = client.post(
            "/api/onboarding/initialize",
            headers=headers,
            json={
                "title": "Learn AI application development",
                "target_outcome": "Ship a working RAG tutor",
                "deadline": "2026-08-15",
                "weekly_hours_target": 10,
                "learning_preferences": {"style": "coach_then_code"},
                "available_slots": {},
                "self_assessment": {"python_level": 4, "api_level": 3, "llm_level": 2},
                "submitted_answers": {"questions": [{"node_code": "rag_foundations", "is_correct": False}]},
            },
        )
        assert onboarding.status_code == 201
        goal_id = onboarding.json()["goal"]["goal_id"]
        task = onboarding.json()["state"]["today_tasks"][0]

        tutor = client.post(
            "/api/tutor/chat",
            headers=headers,
            json={"goal_id": goal_id, "thread_id": "migrated-thread", "message": "Explain RAG with sources."},
        )
        assert tutor.status_code == 200

        started = client.post(f"/api/tasks/{task['id']}/start", headers=headers, json={})
        assert started.status_code == 200
        completed = client.post(
            f"/api/tasks/{task['id']}/complete",
            headers=headers,
            json={"duration_minutes": 20, "evidence": {"note": "completed migrated workflow task"}},
        )
        assert completed.status_code == 200

        refreshed = client.post("/api/auth/refresh")
        assert refreshed.status_code == 200
        refreshed_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
        assert client.get(f"/api/state/current?goal_id={goal_id}", headers=refreshed_headers).status_code == 200

        logged_out = client.post("/api/auth/logout", headers=refreshed_headers)
        assert logged_out.status_code == 204
        assert client.get(f"/api/state/current?goal_id={goal_id}", headers=refreshed_headers).status_code == 401
        assert client.post("/api/auth/refresh").status_code == 401

        with factory() as session:
            assert session.execute(text("select count(*) from auth_sessions")).scalar_one() == 1
            assert session.execute(text("select count(*) from learning_state_snapshots")).scalar_one() == 1
            assert session.execute(text("select count(*) from learning_events")).scalar_one() >= 2
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
