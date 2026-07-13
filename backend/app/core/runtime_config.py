from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit


PRODUCTION_ENVIRONMENTS = {"prod", "production"}
DEFAULT_DATABASE_CREDENTIALS = {("tutor", "tutor")}
DEFAULT_MINIO_CREDENTIALS = {("minioadmin", "minioadmin")}


def runtime_environment() -> str:
    app_environment = normalize_runtime_mode(os.getenv("APP_ENV"), default="")
    if app_environment:
        return app_environment
    return normalize_runtime_mode(os.getenv("ENVIRONMENT"), default="development")


def is_production_environment() -> bool:
    return runtime_environment() in PRODUCTION_ENVIRONMENTS


def normalize_runtime_mode(value: str | None, *, default: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized or default.strip().lower()


def runtime_mode(name: str, *, default: str) -> str:
    return normalize_runtime_mode(os.getenv(name), default=default)


def missing_runtime_configuration() -> list[str]:
    if not is_production_environment():
        return []

    missing: list[str] = []
    document_mode = runtime_mode("DOCUMENT_PROCESSING_MODE", default="inline")
    object_storage_backend = runtime_mode("DOCUMENT_OBJECT_STORAGE_BACKEND", default="local")
    embedding_backend = runtime_mode("EMBEDDING_BACKEND", default="deterministic")
    rag_retrieval_backend = runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector")
    ocr_backend = runtime_mode("OCR_BACKEND", default="tesseract")
    official_search_provider = runtime_mode("OFFICIAL_SEARCH_PROVIDER", default="url_template")
    llm_base_url = _env_value("LLM_BASE_URL")
    llm_api_key = _env_value("LLM_API_KEY")

    database_url = _env_value("DATABASE_URL")
    _require_any(missing, ["DATABASE_URL"])
    if database_url and _uses_default_database_credentials(database_url):
        missing.append("DATABASE_URL contains default development credentials")

    _require_production_mode(missing, "DOCUMENT_PROCESSING_MODE", document_mode, "celery")
    _require_production_mode(
        missing,
        "DOCUMENT_OBJECT_STORAGE_BACKEND",
        object_storage_backend,
        "minio",
    )
    _require_production_mode(missing, "EMBEDDING_BACKEND", embedding_backend, "openai")
    _require_production_mode(missing, "RAG_RETRIEVAL_BACKEND", rag_retrieval_backend, "pgvector")
    _require_production_mode(missing, "OFFICIAL_SEARCH_PROVIDER", official_search_provider, "brave")
    _require_production_mode(missing, "OCR_BACKEND", ocr_backend, "tesseract")

    if document_mode == "celery":
        _require_any(missing, ["REDIS_URL"])

    if object_storage_backend == "minio":
        _require_any(missing, ["MINIO_ENDPOINT"])
        _require_any(missing, ["MINIO_ACCESS_KEY"])
        _require_any(missing, ["MINIO_SECRET_KEY"])
        _require_any(missing, ["MINIO_BUCKET"])
        minio_credentials = (_env_value("MINIO_ACCESS_KEY"), _env_value("MINIO_SECRET_KEY"))
        if minio_credentials in DEFAULT_MINIO_CREDENTIALS:
            missing.append("MINIO credentials use default development values")

    if embedding_backend == "openai":
        _require_any(missing, ["EMBEDDING_API_KEY", "LLM_API_KEY"], label="EMBEDDING_API_KEY or LLM_API_KEY")

    if official_search_provider == "brave":
        _require_any(missing, ["BRAVE_SEARCH_API_KEY"])

    if not llm_base_url:
        missing.append("LLM_BASE_URL")
    if not llm_api_key:
        missing.append("LLM_API_KEY")

    return missing


def _require_any(missing: list[str], names: list[str], *, label: str | None = None) -> None:
    if not any(_env_value(name) for name in names):
        missing.append(label or names[0])


def _require_production_mode(missing: list[str], name: str, actual: str, required: str) -> None:
    if actual != required:
        missing.append(f"{name} must be {required} in production")


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _uses_default_database_credentials(database_url: str) -> bool:
    try:
        parsed = urlsplit(database_url)
    except ValueError:
        return False
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    return (username, password) in DEFAULT_DATABASE_CREDENTIALS
