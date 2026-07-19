from __future__ import annotations

from fastapi import Response

from backend.app.core.security import AuthSettings


def set_refresh_cookie(response: Response, value: str, settings: AuthSettings) -> None:
    response.set_cookie(
        key=settings.cookie_name, value=value, httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, domain=settings.cookie_domain, path="/",
        max_age=settings.refresh_absolute_ttl_seconds,
    )


def clear_refresh_cookie(response: Response, settings: AuthSettings) -> None:
    response.delete_cookie(
        key=settings.cookie_name, domain=settings.cookie_domain, path="/",
        secure=settings.cookie_secure, samesite=settings.cookie_samesite,
    )
