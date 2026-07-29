"""Structural contracts for the tutor runtime's application boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from .memory import MemoryCandidate, MemoryDecision, MemoryPrivacySettings


@runtime_checkable
class TutorRunRequest(Protocol):
    trigger_type: str
    user_id: str
    goal_id: str
    thread_id: str
    user_message: str
    assessment_type: str
    assessment_id: str | None
    knowledge_node_ids: list[str]
    submitted_answers: Mapping[str, str]
    memory_candidates: list[object]


@runtime_checkable
class TutorStateRepository(Protocol):
    def load_context(self, user_id: str, goal_id: str) -> Mapping[str, object]: ...

    def refresh_snapshot(
        self, user_id: str, goal_id: str, updates: Mapping[str, object]
    ) -> Mapping[str, object]: ...


@runtime_checkable
class TutorRetrievedChunk(Protocol):
    chunk_id: str
    document_id: str
    citation_label: str
    source_title: str | None
    source_url: str | None
    trusted_level: int


@runtime_checkable
class TutorRagRepository(Protocol):
    def retrieve(
        self, query: str, *, top_k: int = 5, user_id: str | None = None
    ) -> Sequence[TutorRetrievedChunk]: ...


@runtime_checkable
class TutorContext(Protocol):
    def model_copy(self, *, update: Mapping[str, object]) -> object: ...


@runtime_checkable
class TutorLlmClient(Protocol):
    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context: TutorContext | None = None,
        conversation_context: Mapping[str, object] | None = None,
        context: Sequence[TutorRetrievedChunk] | None = None,
    ) -> str: ...


@runtime_checkable
class TutorAssessmentRepository(Protocol):
    def get_assessment_draft(self, assessment_id: str) -> object: ...


@runtime_checkable
class TutorMemoryGate(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        goal_id: str,
        explicit_candidates: Sequence[MemoryCandidate],
        assessment_result: object | None,
        mastery_updates: Sequence[object],
        privacy_settings: MemoryPrivacySettings,
    ) -> list[MemoryDecision]: ...


@runtime_checkable
class TutorRuntimeDependencies(Protocol):
    state_repository: TutorStateRepository
    rag_repository: TutorRagRepository
    assessment_repository: TutorAssessmentRepository
    llm_client: TutorLlmClient
    tutor_context_factory: Callable[[Mapping[str, object]], TutorContext]
    memory_gate: TutorMemoryGate


@runtime_checkable
class TutorPreparedContext(Protocol):
    state_snapshot: Mapping[str, object]
    tutor_context: TutorContext
    retrieved_context: Sequence[TutorRetrievedChunk]
    retrieval_status: str
    degraded_reason: str | None
    memory_privacy_settings: MemoryPrivacySettings
