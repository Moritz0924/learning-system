from __future__ import annotations

from typing import Any


class DeterministicTutorClient:
    def __init__(self) -> None:
        self.last_completion_metadata: dict[str, Any] = {
            "mode": "offline",
            "is_remote": False,
            "model": "deterministic-test-tutor",
        }

    def complete(self, *, role: str, prompt: str, **_kwargs: Any) -> str:
        return f"{role}: {prompt}. Use retrieved context when available."
