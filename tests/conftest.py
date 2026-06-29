from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import Base, get_session
from backend.app.main import app


@pytest.fixture()
def session_factory(tmp_path):
    db_path = tmp_path / "stage1_test.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_document_object_storage(tmp_path, monkeypatch):
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
