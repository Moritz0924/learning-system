from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .schemas import AssessmentAttemptResult, AssessmentDraft, MasteryUpdate, PlanAdjustment, RetrievedChunk


class LLMClient(Protocol):
    def complete(self, *, role: str, prompt: str, context: list[RetrievedChunk] | None = None) -> str:
        ...


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class OCRClient(Protocol):
    def extract_text(self, content: bytes, *, filename: str) -> str:
        ...


class StateRepository(Protocol):
    def load_context(self, user_id: str, goal_id: str) -> dict:
        ...

    def refresh_snapshot(self, user_id: str, goal_id: str, updates: dict) -> dict:
        ...


class RagRepository(Protocol):
    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]:
        ...


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
    def record_agent_run(self, payload: dict) -> None:
        ...

    def record_tool_call(self, payload: dict) -> None:
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
