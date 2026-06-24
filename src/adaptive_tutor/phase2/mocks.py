from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from .assessment import build_assessment_draft
from .ports import Phase2Dependencies
from .rag import InMemoryRagRepository
from .schemas import AssessmentAttemptResult, AssessmentDraft, MasteryUpdate, PlanAdjustment, RetrievedChunk


class MockEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        digest = sha256(text.lower().encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:16]]


class MockOCRClient:
    def extract_text(self, content: bytes, *, filename: str) -> str:
        return f"OCR text extracted from image {filename}."


class MockLLMClient:
    def complete(self, *, role: str, prompt: str, context: list[RetrievedChunk] | None = None) -> str:
        if role == "teacher" and context:
            return f"{prompt} Based on {context[0].citation_label}, retrieval grounds the answer in sources."
        return f"{role}: {prompt}".strip()


@dataclass
class InMemoryStateRepository:
    snapshots: dict[tuple[str, str], dict] = field(default_factory=dict)

    def load_context(self, user_id: str, goal_id: str) -> dict:
        key = (user_id, goal_id)
        if key not in self.snapshots:
            self.snapshots[key] = {
                "user_id": user_id,
                "goal_id": goal_id,
                "active_plan": {"id": "plan-1", "version": 1},
                "current_task": {"knowledge_node_ids": ["rag_foundations"]},
                "mastery_summary": {"rag_foundations": {"score": 60}},
                "recent_learning_events": [],
            }
        return self.snapshots[key]

    def refresh_snapshot(self, user_id: str, goal_id: str, updates: dict) -> dict:
        snapshot = self.load_context(user_id, goal_id)
        snapshot.update(updates)
        return snapshot


@dataclass
class InMemoryAssessmentRepository:
    assessment_drafts: dict[str, AssessmentDraft] = field(default_factory=dict)
    attempts: list[AssessmentAttemptResult] = field(default_factory=list)
    mastery_updates: list[MasteryUpdate] = field(default_factory=list)

    def save_assessment_draft(self, draft: AssessmentDraft) -> AssessmentDraft:
        self.assessment_drafts[draft.assessment_id] = draft
        return draft

    def get_assessment_draft(self, assessment_id: str) -> AssessmentDraft:
        return self.assessment_drafts[assessment_id]

    def save_attempt_result(self, result: AssessmentAttemptResult) -> AssessmentAttemptResult:
        self.attempts.append(result)
        return result

    def save_mastery_updates(self, updates: list[MasteryUpdate]) -> list[MasteryUpdate]:
        self.mastery_updates.extend(updates)
        return updates


@dataclass
class InMemoryPlanRepository:
    plan_adjustments: list[PlanAdjustment] = field(default_factory=list)

    def save_plan_adjustment(self, adjustment: PlanAdjustment) -> PlanAdjustment:
        self.plan_adjustments.append(adjustment)
        return adjustment


@dataclass
class InMemoryAuditSink:
    agent_runs: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)

    def record_agent_run(self, payload: dict) -> None:
        self.agent_runs.append(payload)

    def record_tool_call(self, payload: dict) -> None:
        self.tool_calls.append(payload)


def build_mock_phase2_dependencies() -> Phase2Dependencies:
    embedding = MockEmbeddingClient()
    return Phase2Dependencies(
        state_repository=InMemoryStateRepository(),
        rag_repository=InMemoryRagRepository(embedding_client=embedding),
        assessment_repository=InMemoryAssessmentRepository(),
        plan_repository=InMemoryPlanRepository(),
        audit_sink=InMemoryAuditSink(),
        llm_client=MockLLMClient(),
        embedding_client=embedding,
        ocr_client=MockOCRClient(),
        assessment_factory=build_assessment_draft,
    )
