"""Pure routing and grounding services for the tutor workflow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    is_valid: bool = True
    validated_citation_ids: list[str] = Field(default_factory=list)
    invalid_citation_ids: list[str] = Field(default_factory=list)


class GroundingService:
    """Deterministic P0-A citation provenance check without response changes."""

    def validate(
        self,
        *,
        answer: str,
        retrieved_chunk_ids: list[str],
        candidate_citation_ids: list[str] | None = None,
    ) -> GroundingResult:
        retrieved = set(retrieved_chunk_ids)
        candidates = list(retrieved_chunk_ids if candidate_citation_ids is None else candidate_citation_ids)
        return GroundingResult(
            answer=answer,
            validated_citation_ids=[chunk_id for chunk_id in candidates if chunk_id in retrieved],
            invalid_citation_ids=[chunk_id for chunk_id in candidates if chunk_id not in retrieved],
        )


class IntentRouter:
    _routes = {
        "onboarding": "diagnosis",
        "chat": "retrieve_context",
        "task_completed": "observer",
        "assessment_due": "build_assessment",
        "assessment_submitted": "grade_assessment",
        "manual_replan": "observer",
    }

    def route_after_load(self, trigger_type: str) -> str:
        return self._routes[trigger_type]

    def route_after_observer(self, *, trigger_type: str, observer_decision: object | None) -> str:
        if trigger_type == "manual_replan":
            return "planner"
        if trigger_type == "chat":
            return "memory_gate"
        if observer_decision is not None and getattr(observer_decision, "decision", None) != "keep":
            return "planner"
        return "memory_gate"
