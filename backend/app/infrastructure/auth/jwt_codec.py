from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from jwt import InvalidTokenError

from backend.app.core.security import AuthSettings


class InvalidAccessToken(ValueError):
    pass


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: str
    session_id: str
    role: str
    token_version: int
    token_id: str
    issued_at: datetime
    expires_at: datetime


class AccessTokenCodec:
    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings

    def issue(self, *, user_id: str, session_id: str, role: str, token_version: int) -> tuple[str, AccessTokenClaims]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._settings.access_ttl_seconds)
        claims = AccessTokenClaims(user_id, session_id, role, token_version, str(uuid4()), now, expires_at)
        token = jwt.encode(
            {
                "iss": self._settings.issuer, "aud": self._settings.audience, "sub": user_id,
                "sid": session_id, "role": role, "ver": token_version, "jti": claims.token_id,
                "iat": now, "nbf": now, "exp": expires_at,
            },
            self._settings.secret_key,
            algorithm="HS256",
            headers={"typ": "at+jwt"},
        )
        return token, claims

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "HS256" or header.get("typ") != "at+jwt":
                raise InvalidAccessToken()
            payload = jwt.decode(
                token, self._settings.secret_key, algorithms=["HS256"],
                issuer=self._settings.issuer, audience=self._settings.audience,
                options={"require": ["exp", "nbf", "iat", "sub", "sid", "jti", "ver"]},
            )
            return AccessTokenClaims(
                str(payload["sub"]), str(payload["sid"]), str(payload.get("role", "learner")),
                int(payload["ver"]), str(payload["jti"]),
                datetime.fromtimestamp(payload["iat"], timezone.utc),
                datetime.fromtimestamp(payload["exp"], timezone.utc),
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessToken() from exc
