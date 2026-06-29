from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import get_session
from backend.app.main import app


def _create_goal(client: TestClient, user_id: str) -> dict:
    response = client.post(
        "/api/goals",
        json={
            "user_id": user_id,
            "email": f"{user_id}@example.test",
            "display_name": user_id.title(),
            "title": "Learn AI application development",
            "target_outcome": "Ship a working RAG tutor",
            "deadline": "2026-08-15",
            "weekly_hours_target": 10,
            "learning_preferences": {"style": "coach_then_code"},
        },
    )
    assert response.status_code == 201
    return response.json()


def _submit_diagnosis(client: TestClient, goal: dict, *, user_id: str | None = None, expected_status: int = 201) -> dict:
    response = client.post(
        "/api/onboarding/diagnosis",
        headers={"X-User-Id": user_id or goal["user_id"]},
        json={
            "user_id": user_id or goal["user_id"],
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
                    {"node_code": "fastapi_basics", "is_correct": True},
                    {"node_code": "llm_api_basics", "is_correct": False},
                    {"node_code": "rag_foundations", "is_correct": False},
                ]
            },
        },
    )
    assert response.status_code == expected_status
    return response.json() if response.content else {}


def _first_task_and_node(client: TestClient, goal: dict) -> tuple[dict, str]:
    response = client.get(
        f"/api/state/current?goal_id={goal['goal_id']}",
        headers={"X-User-Id": goal["user_id"]},
    )
    assert response.status_code == 200
    task = response.json()["today_tasks"][0]
    return task, task["knowledge_node_id"]


def _create_assessment(client: TestClient, goal: dict, node_id: str) -> dict:
    response = client.post(
        "/api/assessments",
        headers={"X-User-Id": goal["user_id"]},
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "p0-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [node_id],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_protected_routes_require_x_user_id_header(client):
    goal = _create_goal(client, "missing-header-user")

    response = client.post(
        "/api/tutor/chat",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "missing-header",
            "message": "hello",
        },
    )

    assert response.status_code == 401


def test_legacy_body_user_id_must_match_x_user_id(client):
    goal = _create_goal(client, "legacy-owner")
    response = client.post(
        "/api/onboarding/diagnosis",
        headers={"X-User-Id": "legacy-attacker"},
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "self_assessment": {},
            "submitted_answers": {"questions": []},
        },
    )

    assert response.status_code == 400
    assert "user_id" in response.json()["detail"]


def test_cross_user_goal_write_endpoints_return_not_found(client):
    owner = _create_goal(client, "owner-user")
    attacker = _create_goal(client, "attacker-user")
    _submit_diagnosis(client, owner)
    _, node_id = _first_task_and_node(client, owner)

    chat = client.post(
        "/api/tutor/chat",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "message": "touch owner goal",
        },
    )
    assessment = client.post(
        "/api/assessments",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [node_id],
        },
    )
    phase_assessment = client.post(
        "/api/assessments/phase",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "phase_code": "phase-ai-app-v1",
            "knowledge_node_ids": [node_id],
        },
    )
    replan = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "message": "change owner plan",
        },
    )
    diagnosis = client.post(
        "/api/onboarding/diagnosis",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "goal_id": owner["goal_id"],
            "self_assessment": {},
            "submitted_answers": {"questions": []},
        },
    )

    assert chat.status_code == 404
    assert assessment.status_code == 404
    assert phase_assessment.status_code == 404
    assert replan.status_code == 404
    assert diagnosis.status_code == 404


def test_cross_user_resource_endpoints_return_not_found(client):
    owner = _create_goal(client, "resource-owner")
    attacker = _create_goal(client, "resource-attacker")
    _submit_diagnosis(client, owner)
    task, node_id = _first_task_and_node(client, owner)
    assessment = _create_assessment(client, owner, node_id)
    submit_as_owner = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers={"X-User-Id": owner["user_id"]},
        json={
            "user_id": owner["user_id"],
            "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
        },
    )
    assert submit_as_owner.status_code == 200
    replan_as_owner = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": owner["user_id"]},
        json={
            "user_id": owner["user_id"],
            "goal_id": owner["goal_id"],
            "thread_id": "owner-thread",
            "message": "Please add focused review.",
        },
    )
    assert replan_as_owner.status_code == 200
    adjustment_id = replan_as_owner.json()["adjustment_id"]

    submit = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers={"X-User-Id": attacker["user_id"]},
        json={
            "user_id": attacker["user_id"],
            "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
        },
    )
    apply_adjustment = client.post(
        f"/api/plans/adjustments/{adjustment_id}/apply",
        headers={"X-User-Id": attacker["user_id"]},
        json={"user_id": attacker["user_id"], "goal_id": owner["goal_id"]},
    )
    start_task = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": attacker["user_id"]},
        json={"user_id": attacker["user_id"]},
    )
    complete_task = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers={"X-User-Id": attacker["user_id"]},
        json={"user_id": attacker["user_id"], "duration_minutes": 10, "evidence": {}},
    )

    assert submit.status_code == 404
    assert apply_adjustment.status_code == 404
    assert start_task.status_code == 404
    assert complete_task.status_code == 404


def test_documents_use_header_identity_and_reject_legacy_mismatch(client):
    owner = _create_goal(client, "doc-owner")
    upload = client.post(
        "/api/documents/upload",
        headers={"X-User-Id": owner["user_id"]},
        json={
            "filename": "owner-note.md",
            "mime_type": "text/markdown",
            "content": "# RAG\nOwner-only note.",
        },
    )
    list_response = client.get("/api/documents", headers={"X-User-Id": owner["user_id"]})
    mismatch_upload = client.post(
        "/api/documents/upload",
        headers={"X-User-Id": "doc-attacker"},
        json={
            "user_id": owner["user_id"],
            "filename": "stolen-note.md",
            "mime_type": "text/markdown",
            "content": "# Attack\nWrong owner.",
        },
    )
    mismatch_list = client.get(
        f"/api/documents?user_id={owner['user_id']}",
        headers={"X-User-Id": "doc-attacker"},
    )

    assert upload.status_code == 201
    assert list_response.status_code == 200
    assert list_response.json()["documents"][0]["owner_user_id"] == owner["user_id"]
    assert mismatch_upload.status_code == 400
    assert mismatch_list.status_code == 400


def test_fresh_database_without_migrations_returns_actionable_503(tmp_path):
    db_path = tmp_path / "fresh.db"
    script = f"""
import os
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///{db_path.as_posix()}"
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app, raise_server_exceptions=False)
response = client.post("/api/goals", json={{
    "user_id": "fresh-user",
    "email": "fresh@example.test",
    "display_name": "Fresh",
    "title": "Learn AI apps",
    "target_outcome": "Ship",
    "deadline": "2026-08-15",
    "weekly_hours_target": 5,
    "learning_preferences": {{}},
}})
print(response.status_code)
print(response.text)
raise SystemExit(0 if response.status_code == 503 else 1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "alembic" in result.stdout
    assert "upgrade head" in result.stdout


def test_alembic_console_entrypoint_can_import_backend():
    script_dir = Path(sys.executable).resolve().parent
    executable = script_dir / ("alembic.exe" if os.name == "nt" else "alembic")
    result = subprocess.run(
        [str(executable), "-c", "backend/alembic.ini", "current"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_optional_dependencies_are_installed():
    for module_name in ("celery", "redis", "psycopg", "mcp"):
        __import__(module_name)


def test_celery_document_upload_failure_returns_503(client, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    import backend.app.worker as worker

    def fail_delay(*args, **kwargs):
        raise RuntimeError("broker offline")

    monkeypatch.setattr(worker.process_document_upload_task, "delay", fail_delay)

    response = client.post(
        "/api/documents/upload",
        headers={"X-User-Id": "celery-user"},
        json={
            "filename": "celery-note.md",
            "mime_type": "text/markdown",
            "content": "# Celery\nQueue should be unavailable.",
        },
    )

    assert response.status_code == 503
    assert "document processing queue" in response.json()["detail"]


def test_celery_document_upload_import_failure_returns_503(tmp_path, monkeypatch):
    db_path = tmp_path / "celery-import.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})
    from backend.app.db import Base

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    def override_get_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    def block_worker_import(name, *args, **kwargs):
        if name == "backend.app.worker":
            raise ModuleNotFoundError("No module named 'celery'")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setattr("builtins.__import__", block_worker_import)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/documents/upload",
            headers={"X-User-Id": "celery-import-user"},
            json={
                "filename": "celery-import-note.md",
                "mime_type": "text/markdown",
                "content": "# Celery\nSDK import should be unavailable.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 503
    assert "document processing queue" in response.json()["detail"]
