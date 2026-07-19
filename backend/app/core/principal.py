from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str | None
    role: Literal["learner", "admin"]
    token_version: int
    auth_method: Literal["access_jwt", "legacy_header"]

    @property
    def is_legacy(self) -> bool:
        return self.auth_method == "legacy_header"
