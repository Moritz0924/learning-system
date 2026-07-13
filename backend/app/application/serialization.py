from __future__ import annotations

import json

from adaptive_tutor.phase2.schemas import AssessmentDraft, PlanAdjustment, TutorRunResult
from backend.app.models import Document, LearningEvent, LearningSession, PlanAdjustmentRecord, PlanTask


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
    return {
        "route": result.route,
        "final_answer": result.final_answer,
        "citations": [item.model_dump() for item in result.citations],
        "runtime_metadata": result.runtime_metadata,
        "assessment_draft": result.assessment_draft.model_dump() if result.assessment_draft else None,
        "assessment_result": result.assessment_result.model_dump() if result.assessment_result else None,
        "mastery_updates": [item.model_dump() for item in result.mastery_updates],
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": result.plan_adjustment.model_dump() if result.plan_adjustment else None,
        "audit_log": result.audit_log,
    }

def _draft_to_dict(draft: AssessmentDraft) -> dict:
    return {
        "assessment_id": draft.assessment_id,
        "assessment_type": draft.assessment_type,
        "status": "active",
        "scope": draft.scope,
        "items": [item.model_dump() for item in draft.items],
    }

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
        "owner_user_id": document.owner_user_id,
        "corpus_type": document.corpus_type,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "parse_status": document.parse_status,
        "parse_error": document.parse_error,
        "source_url": document.source_url,
        "trusted_level": document.trusted_level,
        "created_at": document.created_at,
    }
