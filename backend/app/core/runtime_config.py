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


def thread3_feature_flags() -> dict[str, bool]:
    from adaptive_tutor.tutor.t3_contracts import feature_flags_from_env

    return feature_flags_from_env(os.environ)


def missing_runtime_configuration() -> list[str]:
    parser_errors = _document_parser_configuration_errors()
    if not is_production_environment():
        return parser_errors

    missing: list[str] = list(parser_errors)
    document_mode = runtime_mode("DOCUMENT_PROCESSING_MODE", default="inline")
    object_storage_backend = runtime_mode("DOCUMENT_OBJECT_STORAGE_BACKEND", default="local")
    embedding_backend = runtime_mode("EMBEDDING_BACKEND", default="deterministic")
    rag_retrieval_backend = runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector")
    ocr_backend = runtime_mode("OCR_BACKEND", default="tesseract")
    official_search_provider = runtime_mode("OFFICIAL_SEARCH_PROVIDER", default="url_template")
    checkpoint_backend = runtime_mode("TUTOR_CHECKPOINT_BACKEND", default="postgres")
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
    _require_production_mode(
        missing,
        "TUTOR_CHECKPOINT_BACKEND",
        checkpoint_backend,
        "postgres",
    )

    for name in [
        "TUTOR_HISTORY_MAX_TURNS",
        "TUTOR_HISTORY_MAX_ESTIMATED_TOKENS",
    ]:
        if _positive_env(name) is False:
            missing.append(f"{name} must be a positive integer")

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

    from .security import auth_configuration_errors
    missing.extend(auth_configuration_errors())
    return missing


def _document_parser_configuration_errors() -> list[str]:
    errors: list[str] = []
    fallback_mode = runtime_mode("OCR_VISION_FALLBACK", default="auto")
    if fallback_mode not in {"disabled", "auto", "always"}:
        errors.append("OCR_VISION_FALLBACK must be disabled, auto, or always")
    confidence = _float_env("OCR_MIN_CONFIDENCE", 0.65)
    if confidence is None or not 0 <= confidence <= 1:
        errors.append("OCR_MIN_CONFIDENCE must be between 0 and 1")
    for name in [
        "OCR_MIN_TEXT_CHARS", "DOCUMENT_PDF_MIN_TEXT_CHARS", "DOCUMENT_MAX_PPT_SLIDES",
        "DOCUMENT_RENDER_DPI", "VISION_MAX_CONCURRENCY", "VISION_MAX_PAGES_PER_DOCUMENT",
    ]:
        if _positive_env(name) is False:
            errors.append(f"{name} must be a positive integer")
    return errors


def _float_env(name: str, default: float) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return None


def _positive_env(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return True
    try:
        return int(raw) > 0
    except ValueError:
        return False


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
