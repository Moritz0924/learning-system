"""Load and hash prompt variants and the fixed response envelope separately."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from evals.models import PromptVariant


@dataclass(frozen=True)
class ResponseEnvelope:
    content: str
    sha256: str
    file_path: str


def _read(path: Path) -> tuple[str, str]:
    content = path.read_text(encoding="utf-8")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_prompt_variant(path: Path) -> PromptVariant:
    content, digest = _read(path)
    return PromptVariant(
        name=path.stem.replace("_", "-"),
        content=content,
        sha256=digest,
        file_path=str(path),
    )


def load_response_envelope(path: Path) -> ResponseEnvelope:
    content, digest = _read(path)
    return ResponseEnvelope(content=content, sha256=digest, file_path=str(path))
