from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import Base, enable_sqlite_foreign_keys, get_session
from backend.app.main import app
from backend.app.models import LearningGoal


def register_user(client: TestClient, *, email: str, display_name: str = "Test Learner") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse battery staple", "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"user_id": body["user"]["id"], "headers": {"Authorization": f"Bearer {body['access_token']}"}}


def register_user_with_goal(
    client: TestClient,
    session_factory,
    *,
    email: str,
    goal_id: str | None = None,
) -> dict:
    identity = register_user(client, email=email)
    resolved_goal_id = goal_id or f"goal-{identity['user_id']}"
    with session_factory() as session:
        session.add(
            LearningGoal(
                id=resolved_goal_id,
                user_id=identity["user_id"],
                title="Document goal",
                target_outcome="Index goal-scoped learning materials",
                weekly_hours_target=4,
            )
        )
        session.commit()
    return {**identity, "goal_id": resolved_goal_id}


@pytest.fixture()
def session_factory(tmp_path):
    db_path = tmp_path / "stage1_test.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_document_object_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_LOCAL_DIR", str(tmp_path / "document_objects"))
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")


@pytest.fixture()
def db_session(session_factory) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session


@pytest.fixture()
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
