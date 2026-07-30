from __future__ import annotations

from typing import Protocol

from .domain import FusedCandidate, QueryAnalysis, RetrievalCandidate, RetrievalRequest


class QueryRewritePort(Protocol):
    def rewrite(self, analysis: QueryAnalysis) -> tuple[str, ...]: ...


class RerankerPort(Protocol):
    def rerank(
        self,
        request: RetrievalRequest,
        candidates: tuple[FusedCandidate, ...],
    ) -> tuple[FusedCandidate, ...]: ...


class VectorRetriever(Protocol):
    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]: ...


class KeywordRetriever(Protocol):
    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]: ...


class MetadataRetriever(Protocol):
    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]: ...
