from __future__ import annotations

import os
from dataclasses import dataclass

from backend.app.domain.assessment.contracts import (
    AssessmentGradeBundleV2,
    AssessmentGradingContextV2,
    OpenAnswerGradeBundleV2,
)
from backend.app.domain.assessment.errors import AssessmentUnavailable
from backend.app.domain.assessment.grading_policy import deterministic_grade, validate_grade_bundle
from backend.app.infrastructure.llm.structured_output_client import StructuredOutputClient


@dataclass(frozen=True)
class AssessmentGradingOutcome:
    bundle: AssessmentGradeBundleV2
    item_modes: dict[str, str]
    model: str | None
    metadata: dict[str, object]


class AssessmentGradingService:
    def __init__(self, *, client: StructuredOutputClient | None = None) -> None:
        self.client = client or StructuredOutputClient()

    def grade(self, context: AssessmentGradingContextV2) -> AssessmentGradingOutcome:
        configured_mode = os.getenv("ASSESSMENT_GRADER_MODE", "hybrid").strip().lower() or "hybrid"
        if configured_mode not in {"hybrid", "remote", "deterministic"}:
            configured_mode = "hybrid"
        deterministic = deterministic_grade(context)
        open_item_ids = {
            item.item_id
            for item in context.items
            if item.question_type != "choice" and bool(context.submitted_answers.get(item.item_id, "").strip())
        }
        item_modes = {
            item.item_id: "deterministic_exact"
            if item.question_type == "choice" or not context.submitted_answers.get(item.item_id, "").strip()
            else ("manual_review_required" if grade.needs_human_review else "deterministic_fallback")
            for item, grade in zip(context.items, deterministic.item_grades)
        }
        if not open_item_ids or configured_mode == "deterministic":
            return AssessmentGradingOutcome(deterministic, item_modes, None, dict(self.client.last_metadata))

        open_context = context.model_copy(
            update={
                "items": [item for item in context.items if item.item_id in open_item_ids],
                "submitted_answers": {item_id: context.submitted_answers[item_id] for item_id in open_item_ids},
            }
        )
        result = self.client.complete(
            role="assessment_grader",
            prompt_version="assessment-grader-v2",
            system_instructions="Grade only the submitted open answers by rubric criterion. Return concise feedback and no chain-of-thought.",
            input_payload=open_context,
            output_model=OpenAnswerGradeBundleV2,
        )
        if result.value is None:
            if configured_mode == "remote":
                raise AssessmentUnavailable("Remote assessment grading is unavailable.", code=result.error_code or "assessment.grading_unavailable")
            return AssessmentGradingOutcome(deterministic, item_modes, None, dict(self.client.last_metadata))

        remote_ids = {grade.item_id for grade in result.value.item_grades}
        if remote_ids != open_item_ids or len(remote_ids) != len(result.value.item_grades):
            if configured_mode == "remote":
                raise AssessmentUnavailable("Remote assessment grading returned invalid item IDs.", code="assessment.grading_output_invalid")
            return AssessmentGradingOutcome(deterministic, item_modes, None, dict(self.client.last_metadata))
        by_id = {grade.item_id: grade for grade in deterministic.item_grades}
        by_id.update({grade.item_id: grade for grade in result.value.item_grades})
        bundle = AssessmentGradeBundleV2(
            schema_version="assessment-grade-v2",
            grader_version="assessment-grader-v2",
            item_grades=[by_id[item.item_id] for item in context.items],
            overall_feedback=result.value.overall_feedback,
        )
        try:
            validate_grade_bundle(context, bundle)
        except Exception:
            if configured_mode == "remote":
                raise AssessmentUnavailable("Remote assessment grading returned invalid output.", code="assessment.grading_output_invalid")
            return AssessmentGradingOutcome(deterministic, item_modes, None, dict(self.client.last_metadata))
        item_modes.update({item_id: "remote_structured" for item_id in open_item_ids})
        return AssessmentGradingOutcome(bundle, item_modes, result.model, dict(self.client.last_metadata))
