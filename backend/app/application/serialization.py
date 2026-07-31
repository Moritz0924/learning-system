from __future__ import annotations

import json

from pydantic import ValidationError

from adaptive_tutor.phase2.schemas import AssessmentDraft, PlanAdjustment, TutorRunResult
from backend.app.api.schemas.assessments import (
    AssessmentItemPublic,
    AssessmentOptionPublic,
    AssessmentPublicResponse,
)
from backend.app.models import Document, LearningEvent, LearningSession, PlanAdjustmentRecord, PlanTask


def _assessment_options_to_public(options_json: dict) -> list[AssessmentOptionPublic]:
    raw_options = options_json.get("options", [])
    if not isinstance(raw_options, list):
        raise ValueError("assessment options must be a list")
    public_options: list[AssessmentOptionPublic] = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            raise ValueError("assessment option must be an object")
        try:
            public_options.append(
                AssessmentOptionPublic(
                    option_id=raw_option["option_id"],
                    label=raw_option["label"],
                )
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise ValueError("assessment option is malformed") from exc
    return public_options


def _json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}

def _to_iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

def _run_result_to_dict(result: TutorRunResult) -> dict:
    citations = (
        [item.model_dump() for item in result.public_citations]
        if result.public_citations
        else [item.model_dump() for item in result.citations]
    )
    payload = {
        "route": result.route,
        "final_answer": result.final_answer,
        "citations": citations,
        "runtime_metadata": result.runtime_metadata,
        "assessment_draft": result.assessment_draft.model_dump() if result.assessment_draft else None,
        "assessment_result": result.assessment_result.model_dump() if result.assessment_result else None,
        "mastery_updates": [item.model_dump() for item in result.mastery_updates],
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": result.plan_adjustment.model_dump() if result.plan_adjustment else None,
        "audit_log": result.audit_log,
    }
    if result.grounding_status is not None:
        payload.update(
            {
                "grounding_status": result.grounding_status,
                "insufficient_evidence": result.insufficient_evidence,
                "missing_information": result.missing_information,
                "public_citations": [item.model_dump() for item in result.public_citations],
            }
        )
    return payload

def assessment_draft_to_public(draft: AssessmentDraft) -> AssessmentPublicResponse:
    return AssessmentPublicResponse(
        assessment_id=draft.assessment_id,
        assessment_type=draft.assessment_type,
        status="active",
        scope={"knowledge_node_ids": list(draft.scope.get("knowledge_node_ids", []))},
        items=[
            AssessmentItemPublic(
                item_id=item.item_id,
                knowledge_node_id=item.knowledge_node_id,
                question_type=item.question_type,
                prompt=item.prompt,
                options=_assessment_options_to_public(item.options_json),
                difficulty=item.difficulty,
            )
            for item in draft.items
        ],
    )

def _plan_adjustment_model_to_dict(adjustment: PlanAdjustment) -> dict:
    return {
        "adjustment_id": adjustment.adjustment_id,
        "user_id": adjustment.user_id,
        "goal_id": adjustment.goal_id,
        "previous_plan_id": adjustment.previous_plan_id,
        "new_plan_id": adjustment.new_plan_id,
        "trigger_type": adjustment.trigger_type,
        "decision": adjustment.decision,
        "status": adjustment.status,
        "evidence_json": adjustment.evidence_json,
        "before_snapshot": adjustment.before_snapshot,
        "after_snapshot": adjustment.after_snapshot,
        "plan_patch": adjustment.plan_patch,
        "change_summary": adjustment.change_summary,
        "rationale_json": adjustment.rationale_json,
    }

def _plan_adjustment_record_to_dict(record: PlanAdjustmentRecord) -> dict:
    return {
        "adjustment_id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "previous_plan_id": record.previous_plan_id,
        "new_plan_id": record.new_plan_id,
        "trigger_type": record.trigger_type,
        "decision": record.decision,
        "status": record.status,
        "evidence_json": _json_dict(record.evidence_json),
        "before_snapshot": _json_dict(record.before_snapshot),
        "after_snapshot": _json_dict(record.after_snapshot),
        "plan_patch": _json_dict(record.plan_patch),
        "change_summary": _json_dict(record.change_summary),
        "rationale_json": _json_dict(record.rationale_json),
        "created_at": _to_iso(record.created_at),
    }

def _task_to_dict(task: PlanTask) -> dict:
    return {
        "id": task.id,
        "knowledge_node_code": task.knowledge_node_code,
        "knowledge_node_id": task.knowledge_node_id,
        "knowledge_node_title": task.knowledge_node_code.replace("_", " ").title(),
        "title": task.title,
        "objective": task.objective,
        "task_type": task.task_type,
        "scheduled_date": _to_iso(task.scheduled_date),
        "estimated_minutes": task.estimated_minutes,
        "status": task.status,
    }

def _learning_session_to_dict(record: LearningSession) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "plan_id": record.plan_id,
        "task_id": record.task_id,
        "started_at": _to_iso(record.started_at),
        "ended_at": _to_iso(record.ended_at),
        "duration_minutes": record.duration_minutes,
        "status": record.status,
        "evidence_json": record.evidence_json,
    }

def _learning_event_to_dict(record: LearningEvent) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "session_id": record.session_id,
        "task_id": record.task_id,
        "event_type": record.event_type,
        "source": record.source,
        "event_payload": record.event_payload,
        "occurred_at": _to_iso(record.occurred_at),
    }

def _document_to_dict(document: Document) -> dict:
    return {
        "id": document.id,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "size_bytes": document.size_bytes,
        "parse_status": document.parse_status,
        "parse_error_code": document.parse_error_code,
        "parse_error": document.parse_error,
        "page_count": document.page_count,
        "block_count": document.block_count,
        "parser_version": document.parser_version,
        "created_at": document.created_at,
        "processing_started_at": document.processing_started_at,
        "processing_completed_at": document.processing_completed_at,
    }
