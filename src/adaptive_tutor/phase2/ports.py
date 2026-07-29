from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Protocol

from adaptive_tutor.tutor.contracts import TutorLlmClient, TutorMemoryGate, TutorRagRepository, TutorStateRepository

from .schemas import AssessmentAttemptResult, AssessmentDraft, MasteryUpdate, PlanAdjustment, RetrievedChunk, TutorContext


LLMClient = TutorLlmClient


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class OCRClient(Protocol):
    def extract_text(self, content: bytes, *, filename: str) -> str:
        ...


StateRepository = TutorStateRepository
RagRepository = TutorRagRepository


class AssessmentRepository(Protocol):
    def save_assessment_draft(self, draft: AssessmentDraft) -> AssessmentDraft:
        ...

    def get_assessment_draft(self, assessment_id: str) -> AssessmentDraft:
        ...

    def save_attempt_result(self, result: AssessmentAttemptResult) -> AssessmentAttemptResult:
        ...

    def save_mastery_updates(self, updates: list[MasteryUpdate]) -> list[MasteryUpdate]:
        ...


class PlanRepository(Protocol):
    def save_plan_adjustment(self, adjustment: PlanAdjustment) -> PlanAdjustment:
        ...


class AuditSink(Protocol):
    def record_agent_run(self, payload: Mapping[str, object]) -> None:
        ...

    def record_tool_call(self, payload: Mapping[str, object]) -> None:
        ...


@dataclass
class Phase2Dependencies:
    state_repository: StateRepository
    rag_repository: RagRepository
    assessment_repository: AssessmentRepository
    plan_repository: PlanRepository
    audit_sink: AuditSink
    llm_client: LLMClient
    embedding_client: EmbeddingClient
    ocr_client: OCRClient
    assessment_factory: Callable
    tutor_context_factory: Callable[[Mapping[str, object]], TutorContext]
    memory_gate: TutorMemoryGate
