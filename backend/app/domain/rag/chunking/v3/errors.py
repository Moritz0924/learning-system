from __future__ import annotations


class HybridChunkingError(Exception):
    retryable = False


class HybridChunkingConfigurationError(ValueError, HybridChunkingError):
    retryable = False


class HybridChunkingSnapshotIncompatible(HybridChunkingConfigurationError):
    retryable = False


class SemanticEmbeddingUnavailable(HybridChunkingError):
    retryable = True


class TemporaryProviderUnavailable(HybridChunkingError):
    retryable = True


class HybridChunkingInvariantViolation(ValueError, HybridChunkingError):
    retryable = False


class StructuredParsingError(ValueError, HybridChunkingError):
    retryable = False
