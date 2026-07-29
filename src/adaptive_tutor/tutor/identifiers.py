"""Deterministic identifiers for tutor workflow audit records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from unicodedata import normalize
from uuid import uuid4

from pydantic import BaseModel


def new_run_id() -> str:
    """Return a run correlation ID that is independent from a conversation ID."""

    return str(uuid4())


def stable_request_hash(request: object) -> str:
    """Return a process-independent SHA-256 hash of a normalized request."""

    normalized = _normalize(request)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _normalize(value: object) -> object:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, str):
        return normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, Mapping):
        return {_normalize(str(key)): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    return value
