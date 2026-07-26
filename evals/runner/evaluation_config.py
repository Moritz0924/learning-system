"""Safety configuration for isolated and explicitly authorized evaluations."""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.engine import make_url


class EvaluationSafetyError(RuntimeError):
    pass


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _same_database(left: str, right: str) -> bool:
    def canonical(value: str) -> tuple[str, str, int | None, str]:
        url = make_url(value)
        backend = url.get_backend_name()
        default_port = 5432 if backend == "postgresql" else None
        return (
            backend,
            (url.host or "").lower(),
            url.port or default_port,
            url.database or "",
        )

    return canonical(left) == canonical(right)


@dataclass(frozen=True)
class EvaluationConfig:
    database_url: str | None
    allow_shared_database: bool
    corpus_namespace: str
    allow_remote: bool
    llm_base_url: str | None
    llm_api_key: str | None
    embedding_base_url: str | None
    embedding_api_key: str | None

    @classmethod
    def from_environment(cls, *, allow_remote: bool = False) -> "EvaluationConfig":
        return cls(
            database_url=_env("EVALUATION_DATABASE_URL"),
            allow_shared_database=(_env("EVALUATION_ALLOW_SHARED_DATABASE") or "false").lower() == "true",
            corpus_namespace=_env("EVALUATION_CORPUS_NAMESPACE") or "learning-qa-v1",
            allow_remote=allow_remote,
            llm_base_url=_env("LLM_BASE_URL"),
            llm_api_key=_env("LLM_API_KEY"),
            embedding_base_url=_env("EMBEDDING_BASE_URL") or _env("LLM_BASE_URL"),
            embedding_api_key=_env("EMBEDDING_API_KEY") or _env("LLM_API_KEY"),
        )

    def require_database_url(self, *, require_postgres: bool = False) -> str:
        if not self.database_url:
            raise EvaluationSafetyError(
                "EVALUATION_DATABASE_URL is required for evaluation corpus seeding or remote evaluation"
            )
        application_url = _env("DATABASE_URL")
        if application_url and _same_database(self.database_url, application_url) and not self.allow_shared_database:
            raise EvaluationSafetyError(
                "EVALUATION_DATABASE_URL equals DATABASE_URL; use a dedicated evaluation database"
            )
        if require_postgres and make_url(self.database_url).get_backend_name() != "postgresql":
            raise EvaluationSafetyError("formal evaluation requires PostgreSQL with pgvector")
        return self.database_url

    def require_remote(self, provider: str) -> None:
        if not self.allow_remote:
            raise EvaluationSafetyError(f"remote {provider} evaluation requires --allow-remote")
        if provider == "llm" and not (self.llm_base_url and self.llm_api_key):
            raise EvaluationSafetyError("remote LLM configuration is incomplete")
        if provider == "embedding" and not (self.embedding_base_url and self.embedding_api_key):
            raise EvaluationSafetyError("remote embedding configuration is incomplete")


def persistent_conversation_available() -> bool:
    """Require an explicit production capability contract; constructor shape alone is insufficient."""
    try:
        from adaptive_tutor.phase2.engine import Phase2TutorEngine
    except ImportError:
        return False
    return bool(
        getattr(Phase2TutorEngine, "supports_persistent_conversation", False)
        and getattr(Phase2TutorEngine, "passes_conversation_context_to_llm", False)
        and getattr(Phase2TutorEngine, "supports_cross_instance_thread_restore", False)
    )
