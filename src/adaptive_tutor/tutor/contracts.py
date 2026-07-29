"""Structural contracts for the tutor runtime's application boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from .memory import MemoryCandidate, MemoryDecision, MemoryPrivacySettings
from adaptive_tutor.phase2.schemas import (
    AssessmentAttemptResult,
    AssessmentDraft,
    MasteryUpdate,
    RetrievedChunk,
    TutorContext,
)


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
    def load_context(self, user_id: str, goal_id: str) -> dict[str, Any]: ...

    def refresh_snapshot(self, user_id: str, goal_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class TutorRagRepository(Protocol):
    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]: ...


@runtime_checkable
class TutorLlmClient(Protocol):
    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context: TutorContext | None = None,
        conversation_context: dict[str, Any] | None = None,
        context: list[RetrievedChunk] | None = None,
    ) -> str: ...


@runtime_checkable
class TutorAssessmentRepository(Protocol):
    def get_assessment_draft(self, assessment_id: str) -> AssessmentDraft: ...


@runtime_checkable
class TutorMemoryGate(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        goal_id: str,
        explicit_candidates: list[MemoryCandidate],
        assessment_result: AssessmentAttemptResult | None,
        mastery_updates: list[MasteryUpdate],
        privacy_settings: MemoryPrivacySettings,
    ) -> list[MemoryDecision]: ...


@runtime_checkable
class TutorRuntimeDependencies(Protocol):
    state_repository: TutorStateRepository
    rag_repository: TutorRagRepository
    assessment_repository: TutorAssessmentRepository
    llm_client: TutorLlmClient
    tutor_context_factory: Callable[[dict[str, Any]], TutorContext]
    memory_gate: TutorMemoryGate


@runtime_checkable
class TutorPreparedContext(Protocol):
    state_snapshot: dict[str, Any]
    tutor_context: TutorContext
    retrieved_context: list[RetrievedChunk]
    retrieval_status: str
    degraded_reason: str | None
    memory_privacy_settings: MemoryPrivacySettings
