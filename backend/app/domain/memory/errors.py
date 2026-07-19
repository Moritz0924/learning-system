from __future__ import annotations


class MemoryError(ValueError):
    """Base error for safe, domain-level memory validation failures."""


class UnsupportedMemoryType(MemoryError):
    pass


class InvalidMemoryContent(MemoryError):
    pass


class InvalidMemoryScope(MemoryError):
    pass


class MemoryScopeNotFound(MemoryError):
    pass


class MemoryNotFound(MemoryError):
    pass


class MemoryIdempotencyConflict(MemoryError):
    pass


class MemoryGateError(MemoryError):
    pass


class MemoryGateInvariantError(MemoryGateError):
    pass


class MemoryGateLimitError(MemoryGateError):
    pass
