"""LangGraph checkpoint runtime selection and lifecycle ownership."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import dict_row

from adaptive_tutor.tutor.history import HistoryPolicy
from adaptive_tutor.tutor.models import TutorWorkflowState


class CheckpointConfigurationError(ValueError):
    pass


class TutorCheckpointRuntime(Protocol):
    saver: BaseCheckpointSaver
    history_policy: HistoryPolicy

    def setup(self) -> None: ...

    def delete_thread(self, thread_id: str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CheckpointSettings:
    backend: str
    database_url: str | None
    history_policy: HistoryPolicy

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "CheckpointSettings":
        environment = _mode(values.get("APP_ENV") or values.get("ENVIRONMENT"), "development")
        default_backend = (
            "memory" if environment in {"test", "testing"} else "postgres"
        )
        backend = _mode(values.get("TUTOR_CHECKPOINT_BACKEND"), default_backend)
        if backend not in {"postgres", "memory"}:
            raise CheckpointConfigurationError(
                "TUTOR_CHECKPOINT_BACKEND must be postgres or memory"
            )
        if backend == "memory" and environment not in {"test", "testing"}:
            raise CheckpointConfigurationError(
                "in-memory tutor checkpoints are test-only"
            )
        database_url: str | None = None
        if backend == "postgres":
            raw_url = (
                values.get("TUTOR_CHECKPOINT_DATABASE_URL")
                or values.get("DATABASE_URL")
                or ""
            ).strip()
            database_url = _postgres_connection_url(raw_url)
        return cls(
            backend=backend,
            database_url=database_url,
            history_policy=HistoryPolicy(
                max_turns=_positive_int(values, "TUTOR_HISTORY_MAX_TURNS", 12),
                max_estimated_tokens=_positive_int(
                    values,
                    "TUTOR_HISTORY_MAX_ESTIMATED_TOKENS",
                    16_000,
                ),
            ),
        )

    @classmethod
    def from_environment(cls) -> "CheckpointSettings":
        return cls.from_mapping(os.environ)


class InMemoryTutorCheckpointRuntime:
    def __init__(self, *, history_policy: HistoryPolicy | None = None) -> None:
        self.saver = InMemorySaver(serde=_checkpoint_serializer())
        self.history_policy = history_policy or HistoryPolicy()

    def setup(self) -> None:
        return None

    def delete_thread(self, thread_id: str) -> None:
        self.saver.delete_thread(thread_id)

    def close(self) -> None:
        return None


class PostgresTutorCheckpointRuntime:
    def __init__(
        self,
        database_url: str,
        *,
        history_policy: HistoryPolicy | None = None,
    ) -> None:
        self.database_url = _postgres_connection_url(database_url)
        self.history_policy = history_policy or HistoryPolicy()
        self._connection: Connection | None = None
        self._saver: PostgresSaver | None = None

    @property
    def saver(self) -> BaseCheckpointSaver:
        if self._saver is None:
            raise RuntimeError("PostgreSQL tutor checkpoint runtime is not initialized")
        return self._saver

    def setup(self) -> None:
        if self._saver is not None:
            return
        connection = Connection.connect(
            self.database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = PostgresSaver(
            connection,
            serde=_checkpoint_serializer(),
        )
        try:
            saver.setup()
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        self._saver = saver

    def delete_thread(self, thread_id: str) -> None:
        cast(PostgresSaver, self.saver).delete_thread(thread_id)

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        self._saver = None
        if connection is not None:
            connection.close()


def build_checkpoint_runtime(settings: CheckpointSettings) -> TutorCheckpointRuntime:
    if settings.backend == "memory":
        return InMemoryTutorCheckpointRuntime(
            history_policy=settings.history_policy
        )
    if settings.database_url is None:
        raise CheckpointConfigurationError(
            "PostgreSQL tutor checkpoint database URL is required"
        )
    return PostgresTutorCheckpointRuntime(
        settings.database_url,
        history_policy=settings.history_policy,
    )


_runtime_lock = Lock()
_active_runtime: TutorCheckpointRuntime | None = None


def initialize_checkpoint_runtime(
    settings: CheckpointSettings | None = None,
) -> TutorCheckpointRuntime:
    global _active_runtime
    with _runtime_lock:
        if _active_runtime is None:
            runtime = build_checkpoint_runtime(
                settings or CheckpointSettings.from_environment()
            )
            runtime.setup()
            _active_runtime = runtime
        return _active_runtime


def active_checkpoint_runtime() -> TutorCheckpointRuntime | None:
    return _active_runtime


def shutdown_checkpoint_runtime() -> None:
    global _active_runtime
    with _runtime_lock:
        runtime = _active_runtime
        _active_runtime = None
        if runtime is not None:
            runtime.close()


def _mode(value: str | None, default: str) -> str:
    return (value or default).strip().lower() or default


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise CheckpointConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise CheckpointConfigurationError(f"{name} must be a positive integer")
    return value


def _postgres_connection_url(value: str) -> str:
    normalized = value.strip().replace("postgresql+psycopg://", "postgresql://", 1)
    if not normalized.startswith(("postgresql://", "postgres://")):
        raise CheckpointConfigurationError(
            "tutor checkpoint database URL must use PostgreSQL"
        )
    return normalized


def _checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[TutorWorkflowState],
    )
