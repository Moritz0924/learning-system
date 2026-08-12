from __future__ import annotations


class HybridChunkingError(Exception):
    retryable = False


class HybridChunkingConfigurationError(HybridChunkingError):
    retryable = False


class SemanticEmbeddingUnavailable(HybridChunkingError):
    retryable = True


class HybridChunkingInvariantViolation(HybridChunkingError):
    retryable = False


class StructuredParsingError(HybridChunkingError):
    retryable = False

