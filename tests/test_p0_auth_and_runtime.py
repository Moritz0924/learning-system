from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import enable_sqlite_foreign_keys, get_session
from backend.app.core.runtime_config import normalize_runtime_mode, runtime_environment
from backend.app.main import app, database_operational_error_handler
from backend.app.models import Curriculum, LearningGoal, User
from backend.app.services.curriculum import ensure_curriculum_seeded
from backend.app.services.learning import NotFoundError, create_goal as create_goal_record
from tests.conftest import register_user


def test_postgres_undefined_table_error_returns_migration_required_response():
    class UndefinedTableError(Exception):
        sqlstate = "42P01"

    error = ProgrammingError(
        "SELECT * FROM learning_goals",
        {},
        UndefinedTableError("undefined table"),
    )

    assert app.exception_handlers[ProgrammingError] is database_operational_error_handler
    response = asyncio.run(database_operational_error_handler(None, error))

    assert response.status_code == 503
    detail = json.loads(response.body)["detail"]
    assert "alembic" in detail
    assert "upgrade head" in detail


def test_postgres_non_table_programming_error_is_not_masked_as_missing_migration():
    class UndefinedColumnError(Exception):
        sqlstate = "42703"

    error = ProgrammingError(
        "SELECT missing_column FROM learning_goals",
        {},
        UndefinedColumnError("column does not exist"),
    )

    with pytest.raises(ProgrammingError) as raised:
        asyncio.run(database_operational_error_handler(None, error))

    assert raised.value is error


def test_database_error_parameters_cannot_impersonate_a_missing_table():
    error = OperationalError(
        "SELECT :value",
        {"value": "no such table: users"},
        Exception("database is locked"),
    )

    with pytest.raises(OperationalError) as raised:
        asyncio.run(database_operational_error_handler(None, error))

    assert raised.value is error


def _create_goal(client: TestClient, user_id: str) -> dict:
    identity = register_user(client, email=f"{user_id}@example.com", display_name=user_id.title())
    response = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json={
            "title": "Learn AI application development",
            "target_outcome": "Ship a working RAG tutor",
            "deadline": "2026-08-15",
            "weekly_hours_target": 10,
            "learning_preferences": {"style": "coach_then_code"},
            "self_assessment": {"python_level": 4},
            "submitted_answers": {"questions": []},
        },
    )
    assert response.status_code == 201
    goal = response.json()["goal"]
    goal.update(identity)
    return goal


def _submit_diagnosis(client: TestClient, goal: dict, *, user_id: str | None = None, expected_status: int = 201) -> dict:
    response = client.post(
        "/api/onboarding/diagnosis",
        headers=goal["headers"],
        json={
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
        headers=goal["headers"],
    )
    assert response.status_code == 200
    task = response.json()["today_tasks"][0]
    return task, task["knowledge_node_id"]


def _create_assessment(client: TestClient, goal: dict, node_id: str) -> dict:
    response = client.post(
        "/api/assessments",
        headers=goal["headers"],
        json={
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

    tools = client.post(
        "/api/tools/search-official-learning-sources",
        json={"query": "FastAPI dependency injection", "domains": ["fastapi.tiangolo.com"]},
    )

    assert response.status_code == 401
    assert tools.status_code == 401


def test_protected_routes_reject_blank_x_user_id_header(client):
    goal = _create_goal(client, "blank-header-user")

    response = client.post(
        "/api/tutor/chat",
        headers={"X-User-Id": "   "},
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "blank-header",
            "message": "hello",
        },
    )

    assert response.status_code == 401


def test_legacy_body_user_id_is_rejected_even_with_a_valid_principal(client):
    goal = _create_goal(client, "legacy-owner")
    response = client.post(
        "/api/onboarding/diagnosis",
        headers=goal["headers"],
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "self_assessment": {},
            "submitted_answers": {"questions": []},
        },
    )

    assert response.status_code == 422


def test_duplicate_email_registration_returns_conflict(client):
    payload = {
        "email": "shared@example.com",
        "password": "correct horse battery staple",
        "display_name": "Owner",
    }
    first = client.post(
        "/api/auth/register",
        json=payload,
    )
    duplicate = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "auth.email_already_registered"


def test_create_goal_never_creates_a_missing_user(session_factory):
    with session_factory() as session:
        with pytest.raises(NotFoundError, match="not found"):
            create_goal_record(
                session,
                user_id="missing-user",
                title="Learn AI application development",
                target_outcome="Ship a working RAG tutor",
                deadline="2026-08-15",
                weekly_hours_target=10,
                learning_preferences={"style": "coach_then_code"},
            )


def test_unrelated_integrity_error_for_existing_user_is_not_reported_as_duplicate_email(
    session_factory,
    monkeypatch,
):
    user_id = "existing-integrity-user"
    email = "existing-integrity@example.test"
    with session_factory() as session:
        session.add(User(id=user_id, email=email, display_name="Existing", status="active"))
        session.commit()
        original_rollback = session.rollback

        def fail_goal_commit():
            raise IntegrityError(
                "INSERT INTO learning_goals",
                {"id": "duplicate-goal-id"},
                Exception("unique constraint failed: learning_goals.id"),
            )

        monkeypatch.setattr(session, "commit", fail_goal_commit)
        monkeypatch.setattr(session, "rollback", original_rollback)

        with pytest.raises(IntegrityError, match="learning_goals"):
            create_goal_record(
                session,
                user_id=user_id,
                title="Learn AI application development",
                target_outcome="Ship a working RAG tutor",
                deadline="2026-08-15",
                weekly_hours_target=10,
                learning_preferences={"style": "coach_then_code"},
            )


def test_postgres_curriculum_seed_locks_then_rechecks_before_insert(monkeypatch):
    events: list[str] = []
    existing_curriculum = Curriculum(
        id="curriculum-ai-app-v1",
        code="ai_app_v1",
        version="v1",
        title="AI Application Development V1",
        domain="ai_app_dev",
        is_active=True,
    )

    class PostgreSQLSession:
        get_count = 0

        @staticmethod
        def get_bind():
            return type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

        def get(self, model, object_id):
            self.get_count += 1
            events.append(f"get-{self.get_count}")
            return None if self.get_count == 1 else existing_curriculum

        def execute(self, statement):
            events.append(str(statement))

        @staticmethod
        def scalars(statement):
            return []

        @staticmethod
        def flush():
            return None

    monkeypatch.setattr("backend.app.services.curriculum.NODES", [])

    result = ensure_curriculum_seeded(PostgreSQLSession())

    assert result is existing_curriculum
    assert events[0] == "get-1"
    assert "pg_advisory_xact_lock" in events[1]
    assert events[2] == "get-2"


def test_sqlite_engine_configuration_enforces_foreign_keys():
    sqlite_engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(sqlite_engine)
    try:
        with sqlite_engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        sqlite_engine.dispose()

def test_atomic_onboarding_rolls_back_goal_when_diagnosis_fails(session_factory, monkeypatch):
    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    def fail_diagnosis(*args, **kwargs):
        raise RuntimeError("injected diagnosis failure")

    monkeypatch.setattr("backend.app.services.learning.build_baseline_diagnosis", fail_diagnosis)
    app.dependency_overrides[get_session] = override_get_session
    try:
        failing_client = TestClient(app, raise_server_exceptions=False)
        identity = register_user(
            failing_client,
            email="atomic-onboarding@example.com",
            display_name="Atomic Learner",
        )
        response = failing_client.post(
            "/api/onboarding/initialize",
            headers=identity["headers"],
            json={
                "title": "Learn AI application development",
                "target_outcome": "Ship a working RAG tutor",
                "deadline": "2026-08-15",
                "weekly_hours_target": 10,
                "learning_preferences": {"style": "coach_then_code"},
                "self_assessment": {"python_level": 4},
                "submitted_answers": {"questions": []},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    with session_factory() as session:
        assert session.scalar(select(User).where(User.email == "atomic-onboarding@example.com")) is not None
        assert session.scalar(select(LearningGoal).where(LearningGoal.user_id == identity["user_id"])) is None


def test_goal_creation_requires_bearer_and_rejects_legacy_identity_body(client):
    owner = _create_goal(client, "existing-goal-owner")

    missing_identity = client.post(
        "/api/goals",
        json={
            "title": "Create another learning goal",
            "target_outcome": "Keep the existing profile protected",
            "deadline": "2026-09-15",
            "weekly_hours_target": 20,
            "learning_preferences": {"style": "unauthorized"},
        },
    )
    legacy_identity = client.post(
        "/api/goals",
        headers=owner["headers"],
        json={
            "user_id": owner["user_id"],
            "title": "Create another learning goal",
            "target_outcome": "Keep the existing profile protected",
            "deadline": "2026-09-15",
            "weekly_hours_target": 20,
            "learning_preferences": {"style": "unauthorized"},
        },
    )

    assert missing_identity.status_code == 401
    assert legacy_identity.status_code == 422


def test_cross_user_goal_write_endpoints_return_not_found(client):
    owner = _create_goal(client, "owner-user")
    attacker = _create_goal(client, "attacker-user")
    _submit_diagnosis(client, owner)
    _, node_id = _first_task_and_node(client, owner)

    chat = client.post(
        "/api/tutor/chat",
        headers=attacker["headers"],
        json={
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "message": "touch owner goal",
        },
    )
    assessment = client.post(
        "/api/assessments",
        headers=attacker["headers"],
        json={
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [node_id],
        },
    )
    phase_assessment = client.post(
        "/api/assessments/phase",
        headers=attacker["headers"],
        json={
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "phase_code": "phase-ai-app-v1",
            "knowledge_node_ids": [node_id],
        },
    )
    replan = client.post(
        "/api/plans/replan",
        headers=attacker["headers"],
        json={
            "goal_id": owner["goal_id"],
            "thread_id": "attack-thread",
            "message": "change owner plan",
        },
    )
    diagnosis = client.post(
        "/api/onboarding/diagnosis",
        headers=attacker["headers"],
        json={
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


def test_cross_user_state_read_endpoints_return_not_found(client):
    owner = _create_goal(client, "state-owner")
    attacker = _create_goal(client, "state-attacker")
    _submit_diagnosis(client, owner)

    state = client.get(
        f"/api/state/current?goal_id={owner['goal_id']}",
        headers=attacker["headers"],
    )
    today_tasks = client.get(
        f"/api/tasks/today?goal_id={owner['goal_id']}",
        headers=attacker["headers"],
    )

    assert state.status_code == 404
    assert today_tasks.status_code == 404


def test_cross_user_resource_endpoints_return_not_found(client):
    owner = _create_goal(client, "resource-owner")
    attacker = _create_goal(client, "resource-attacker")
    _submit_diagnosis(client, owner)
    task, node_id = _first_task_and_node(client, owner)
    assessment = _create_assessment(client, owner, node_id)
    submit_as_owner = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers=owner["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
        },
    )
    assert submit_as_owner.status_code == 200
    replan_as_owner = client.post(
        "/api/plans/replan",
        headers=owner["headers"],
        json={
            "goal_id": owner["goal_id"],
            "thread_id": "owner-thread",
            "message": "Please add focused review.",
        },
    )
    assert replan_as_owner.status_code == 200
    adjustment_id = replan_as_owner.json()["adjustment_id"]

    submit = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers=attacker["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
        },
    )
    apply_adjustment = client.post(
        f"/api/plans/adjustments/{adjustment_id}/apply",
        headers=attacker["headers"],
        json={"goal_id": owner["goal_id"]},
    )
    start_task = client.post(
        f"/api/tasks/{task['id']}/start",
        headers=attacker["headers"],
        json={},
    )
    complete_task = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers=attacker["headers"],
        json={"duration_minutes": 10, "evidence": {}},
    )

    assert submit.status_code == 404
    assert apply_adjustment.status_code == 404
    assert start_task.status_code == 404
    assert complete_task.status_code == 404


def test_documents_use_principal_identity_and_reject_legacy_body_identity(client):
    owner = _create_goal(client, "doc-owner")
    attacker = _create_goal(client, "doc-attacker")
    upload = client.post(
        "/api/documents/upload",
        headers=owner["headers"],
        json={
            "filename": "owner-note.md",
            "mime_type": "text/markdown",
            "content": "# RAG\nOwner-only note.",
        },
    )
    list_response = client.get("/api/documents", headers=owner["headers"])
    mismatch_upload = client.post(
        "/api/documents/upload",
        headers=attacker["headers"],
        json={
            "user_id": owner["user_id"],
            "filename": "stolen-note.md",
            "mime_type": "text/markdown",
            "content": "# Attack\nWrong owner.",
        },
    )
    mismatch_list = client.get(
        "/api/documents",
        headers=attacker["headers"],
    )

    assert upload.status_code == 201
    assert list_response.status_code == 200
    listed_document = list_response.json()["documents"][0]
    assert listed_document["id"] == upload.json()["id"]
    assert "owner_user_id" not in listed_document
    assert "object_key" not in listed_document
    assert "sha256" not in listed_document
    assert mismatch_upload.status_code == 422
    assert mismatch_list.status_code == 200
    assert mismatch_list.json() == {"documents": []}


def test_document_apis_do_not_authenticate_unknown_legacy_header(client):
    upload = client.post(
        "/api/documents/upload",
        headers={"X-User-Id": "missing-document-user"},
        json={
            "filename": "unknown-owner.md",
            "mime_type": "text/markdown",
            "content": "# Unknown\nThis user has not onboarded.",
        },
    )
    listed = client.get(
        "/api/documents",
        headers={"X-User-Id": "missing-document-user"},
    )

    assert upload.status_code == 401
    assert listed.status_code == 401


def test_document_upload_rejects_oversized_raw_request_before_json_parsing(client, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MAX_REQUEST_BYTES", "64")

    response = client.post(
        "/api/documents/upload",
        headers={"X-User-Id": "raw-limit-user", "Content-Type": "application/json"},
        content=b"{" + (b"x" * 64),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "document upload request exceeds 64 byte limit"}


def test_fresh_database_without_migrations_returns_actionable_503(tmp_path):
    db_path = tmp_path / "fresh.db"
    script = f"""
import os
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///{db_path.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app, raise_server_exceptions=False)
response = client.post("/api/auth/register", json={{
    "email": "fresh@example.com",
    "password": "correct horse battery staple",
    "display_name": "Fresh",
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


def test_cors_allowed_origins_can_be_configured_from_environment():
    script = """
import asyncio
import json
import os
os.environ["CORS_ALLOWED_ORIGINS"] = "https://preview.example.test"
from fastapi.testclient import TestClient
from backend.app.main import app, database_operational_error_handler

client = TestClient(app)
response = client.options(
    "/api/goals",
    headers={
        "Origin": "https://preview.example.test",
        "Access-Control-Request-Method": "POST",
    },
)
print(response.status_code)
print(response.headers.get("access-control-allow-origin"))
raise SystemExit(
    0
    if response.status_code == 200
    and response.headers.get("access-control-allow-origin") == "https://preview.example.test"
    else 1
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _set_complete_production_runtime(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:strong-test-password@postgres:5432/adaptive_tutor",
    )
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "strong-test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "adaptive-tutor-documents")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "pgvector")
    monkeypatch.setenv("OCR_BACKEND", "tesseract")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("VISION_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("VISION_API_KEY", "vision-key")
    monkeypatch.setenv("VISION_MODEL", "glm-4.5v")
    monkeypatch.setenv("VISION_ENABLED", "true")


def test_runtime_environment_normalizes_case_and_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("APP_ENV", "  ProDuction  ")

    assert runtime_environment() == "production"


def test_blank_app_env_falls_back_to_normalized_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "   ")
    monkeypatch.setenv("ENVIRONMENT", "  ProDuction  ")

    assert runtime_environment() == "production"


def test_blank_runtime_mode_uses_normalized_default():
    assert normalize_runtime_mode("   ", default="  PGVECTOR  ") == "pgvector"


def test_readiness_reports_missing_production_runtime_configuration(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    for name in (
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "EMBEDDING_API_KEY",
        "LLM_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["environment"] == "production"
    assert {
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "EMBEDDING_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "DATABASE_URL",
        "LLM_API_KEY",
    }.issubset(set(payload["missing"]))


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("DOCUMENT_PROCESSING_MODE", "inline", "DOCUMENT_PROCESSING_MODE must be celery in production"),
        (
            "DOCUMENT_OBJECT_STORAGE_BACKEND",
            "local",
            "DOCUMENT_OBJECT_STORAGE_BACKEND must be minio in production",
        ),
        ("EMBEDDING_BACKEND", "deterministic", "EMBEDDING_BACKEND must be openai in production"),
        ("RAG_RETRIEVAL_BACKEND", "local", "RAG_RETRIEVAL_BACKEND must be pgvector in production"),
        (
            "OFFICIAL_SEARCH_PROVIDER",
            "url_template",
            "OFFICIAL_SEARCH_PROVIDER must be brave in production",
        ),
        ("OCR_BACKEND", "unknown", "OCR_BACKEND must be tesseract in production"),
    ],
)
def test_readiness_rejects_non_production_provider_modes(client, monkeypatch, name, value, expected):
    _set_complete_production_runtime(monkeypatch)
    monkeypatch.setenv(name, value)

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert expected in response.json()["missing"]


def test_readiness_reports_dependency_probe_failures_separately(client, monkeypatch):
    import backend.app.routers.health as health_router

    _set_complete_production_runtime(monkeypatch)
    monkeypatch.setattr(
        health_router,
        "probe_runtime_dependencies",
        lambda: ["database connectivity failed", "redis connectivity failed"],
    )

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == []
    assert response.json()["unavailable"] == [
        "database connectivity failed",
        "redis connectivity failed",
    ]


def test_readiness_requires_independent_vision_configuration_when_enabled(client, monkeypatch):
    _set_complete_production_runtime(monkeypatch)
    monkeypatch.delenv("VISION_API_KEY", raising=False)

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == ["VISION_API_KEY"]


def test_readiness_rejects_production_llm_base_url_without_api_key(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:strong-test-password@postgres:5432/adaptive_tutor",
    )
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "strong-test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "adaptive-tutor-documents")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == ["LLM_API_KEY"]


def test_readiness_rejects_default_development_credentials_in_production(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://tutor:tutor@postgres:5432/adaptive_tutor")
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_BUCKET", "adaptive-tutor-documents")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == [
        "DATABASE_URL contains default development credentials",
        "MINIO credentials use default development values",
    ]


def test_readiness_rejects_production_without_remote_llm_configuration(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:strong-test-password@postgres:5432/adaptive_tutor",
    )
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "strong-test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "adaptive-tutor-documents")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == ["LLM_BASE_URL", "LLM_API_KEY"]


def test_readiness_treats_blank_runtime_configuration_as_missing(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "strong-test-secret")
    monkeypatch.setenv("MINIO_BUCKET", "adaptive-tutor-documents")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("OFFICIAL_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    monkeypatch.setenv("LLM_BASE_URL", "   ")
    monkeypatch.setenv("LLM_API_KEY", "\t")

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["missing"] == ["DATABASE_URL", "LLM_BASE_URL", "LLM_API_KEY"]


def test_runtime_optional_dependencies_are_installed():
    for module_name in ("celery", "redis", "psycopg", "mcp"):
        __import__(module_name)


def test_celery_document_upload_failure_returns_durable_pending_record(client, monkeypatch):
    goal = _create_goal(client, "celery-user")
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "celery")
    import backend.app.worker as worker

    def fail_delay(*args, **kwargs):
        raise RuntimeError("broker offline")

    monkeypatch.setattr(worker.process_document_upload_task, "delay", fail_delay)

    response = client.post(
        "/api/documents/upload",
        headers=goal["headers"],
        json={
            "filename": "celery-note.md",
            "mime_type": "text/markdown",
            "content": "# Celery\nQueue should be unavailable.",
        },
    )

    assert response.status_code == 201
    assert response.json()["parse_status"] == "pending"


def test_celery_document_upload_import_failure_returns_durable_pending_record(tmp_path, monkeypatch):
    db_path = tmp_path / "celery-import.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})
    from backend.app.db import Base

    enable_sqlite_foreign_keys(engine)
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
        identity = register_user(
            client,
            email="celery-import-user@example.com",
            display_name="Celery Import User",
        )
        response = client.post(
            "/api/documents/upload",
            headers=identity["headers"],
            json={
                "filename": "celery-import-note.md",
                "mime_type": "text/markdown",
                "content": "# Celery\nSDK import should be unavailable.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 201
    assert response.json()["parse_status"] == "pending"
