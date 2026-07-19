import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+pysqlite:///./adaptive_tutor_stage1.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def enable_sqlite_foreign_keys(target_engine: Engine) -> None:
    if target_engine.url.get_backend_name() != "sqlite" or getattr(
        target_engine,
        "_sqlite_foreign_keys_enabled",
        False,
    ):
        return

    @event.listens_for(target_engine, "connect")
    def set_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @event.listens_for(target_engine, "savepoint")
    def begin_sqlite_savepoint_transaction(connection, name) -> None:
        # Python 3.11 sqlite3 does not start an outer transaction for a
        # SAVEPOINT. Start one only at this boundary so releasing the
        # SAVEPOINT cannot commit independently of the caller transaction.
        dbapi_connection = connection.connection.driver_connection
        if not dbapi_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    setattr(target_engine, "_sqlite_foreign_keys_enabled", True)


enable_sqlite_foreign_keys(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
