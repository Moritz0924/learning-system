from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class GeneratedRefreshToken:
    token_id: str
    secret: str
    cookie_value: str
    token_hash: str


class RefreshTokenFactory:
    def generate(self) -> GeneratedRefreshToken:
        token_id = str(uuid4())
        secret = secrets.token_urlsafe(48)
        return GeneratedRefreshToken(
            token_id=token_id,
            secret=secret,
            cookie_value=f"{token_id}.{secret}",
            token_hash=self._hash(secret),
        )

    def parse(self, cookie_value: str | None) -> tuple[str, str] | None:
        if not cookie_value:
            return None
        token_id, separator, secret = cookie_value.partition(".")
        return (token_id, secret) if separator and token_id and secret else None

    def verify(self, *, presented_secret: str, expected_hash: str) -> bool:
        return hmac.compare_digest(self._hash(presented_secret), expected_hash)

    @staticmethod
    def _hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()
