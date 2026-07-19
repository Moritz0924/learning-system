from __future__ import annotations

from pwdlib import PasswordHash


class PasswordHasher:
    def __init__(self) -> None:
        self._hasher = PasswordHash.recommended()
        self._dummy_hash = self._hasher.hash("not-a-real-user-password")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        return self._hasher.verify(password, password_hash)

    def verify_or_dummy(self, password: str, password_hash: str | None) -> bool:
        return self._hasher.verify(password, password_hash or self._dummy_hash) if password_hash else False
