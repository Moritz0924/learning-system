from __future__ import annotations

import os
from dataclasses import dataclass

from .runtime_config import is_production_environment


@dataclass(frozen=True)
class AuthSettings:
    secret_key: str
    algorithm: str
    issuer: str
    audience: str
    access_ttl_seconds: int
    refresh_idle_ttl_seconds: int
    refresh_absolute_ttl_seconds: int
    refresh_rotation_grace_seconds: int
    legacy_header_enabled: bool
    cookie_name: str
    cookie_secure: bool
    cookie_samesite: str
    cookie_domain: str | None
    allowed_origins: frozenset[str]
    require_origin_check: bool


def auth_settings() -> AuthSettings:
    production = is_production_environment()
    return AuthSettings(
        secret_key=os.getenv("JWT_SECRET_KEY", "").strip(),
        algorithm=os.getenv("JWT_ALGORITHM", "HS256").strip() or "HS256",
        issuer=os.getenv("JWT_ISSUER", "learning-system").strip() or "learning-system",
        audience=os.getenv("JWT_AUDIENCE", "learning-system-api").strip() or "learning-system-api",
        access_ttl_seconds=_positive_int("ACCESS_TOKEN_TTL_SECONDS", 900),
        refresh_idle_ttl_seconds=_positive_int("REFRESH_IDLE_TTL_SECONDS", 604800),
        refresh_absolute_ttl_seconds=_positive_int("REFRESH_ABSOLUTE_TTL_SECONDS", 2592000),
        refresh_rotation_grace_seconds=_positive_int("REFRESH_ROTATION_GRACE_SECONDS", 3),
        legacy_header_enabled=_bool("AUTH_LEGACY_HEADER_ENABLED", False),
        cookie_name=os.getenv(
            "AUTH_REFRESH_COOKIE_NAME", "__Host-learning_refresh" if production else "learning_refresh"
        ).strip(),
        cookie_secure=_bool("AUTH_COOKIE_SECURE", production),
        cookie_samesite=(os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax"),
        cookie_domain=os.getenv("AUTH_COOKIE_DOMAIN", "").strip() or None,
        allowed_origins=frozenset(
            item.strip() for item in os.getenv(
                "AUTH_ALLOWED_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
            ).split(",") if item.strip()
        ),
        require_origin_check=_bool("AUTH_REQUIRE_ORIGIN_CHECK", True),
    )


def auth_configuration_errors() -> list[str]:
    settings = auth_settings()
    if not is_production_environment():
        return []
    missing: list[str] = []
    if len(settings.secret_key.encode("utf-8")) < 32:
        missing.append("JWT_SECRET_KEY must contain at least 32 bytes")
    if settings.algorithm != "HS256":
        missing.append("JWT_ALGORITHM must be HS256")
    if not settings.issuer:
        missing.append("JWT_ISSUER")
    if not settings.audience:
        missing.append("JWT_AUDIENCE")
    if settings.access_ttl_seconds <= 0:
        missing.append("ACCESS_TOKEN_TTL_SECONDS")
    if settings.refresh_idle_ttl_seconds <= settings.access_ttl_seconds:
        missing.append("REFRESH_IDLE_TTL_SECONDS must exceed ACCESS_TOKEN_TTL_SECONDS")
    if settings.refresh_absolute_ttl_seconds < settings.refresh_idle_ttl_seconds:
        missing.append("REFRESH_ABSOLUTE_TTL_SECONDS must be at least REFRESH_IDLE_TTL_SECONDS")
    if not settings.cookie_secure:
        missing.append("AUTH_COOKIE_SECURE must be true")
    if settings.cookie_domain is not None:
        missing.append("AUTH_COOKIE_DOMAIN must be empty")
    if settings.legacy_header_enabled:
        missing.append("AUTH_LEGACY_HEADER_ENABLED must be false")
    if not settings.require_origin_check:
        missing.append("AUTH_REQUIRE_ORIGIN_CHECK must be true")
    if not settings.allowed_origins:
        missing.append("AUTH_ALLOWED_ORIGINS")
    return missing


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
