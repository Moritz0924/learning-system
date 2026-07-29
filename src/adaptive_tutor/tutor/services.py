from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .memory import MemoryPrivacySettings
from .models import ConversationState, EvidenceState, ExecutionState, LearningState, TutorWorkflowState


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

    def route_after_observer(self, *, trigger_type: str, observer_decision: Any | None) -> str:
        if trigger_type == "manual_replan":
            return "planner"
        if trigger_type == "chat":
            return "memory_gate"
        if observer_decision is not None and observer_decision.decision != "keep":
            return "planner"
        return "memory_gate"


class SessionContextService:
    def load(self, state: dict[str, Any], dependencies: Any) -> dict[str, Any]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        snapshot = (
            prepared_context.state_snapshot
            if request.trigger_type == "chat" and prepared_context is not None
            else dependencies.state_repository.load_context(request.user_id, request.goal_id)
        )
        tutor_context = None
        if request.trigger_type == "chat":
            tutor_context = (
                prepared_context.tutor_context
                if prepared_context is not None
                else dependencies.tutor_context_factory(snapshot)
            )
        state.update(
            {
                "state_snapshot": snapshot,
                "active_plan": snapshot.get("active_plan", {}),
                "current_task": snapshot.get("current_task"),
                "mastery_snapshot": snapshot.get("mastery_summary", {}),
                "recent_learning_events": snapshot.get("recent_learning_events", []),
                "observer_signals": snapshot.get("observer_signals", {}),
            }
        )
        if tutor_context is not None:
            state["tutor_context"] = tutor_context
        state["workflow_state"] = TutorWorkflowState(
            conversation=ConversationState(
                thread_id=request.thread_id,
                user_id=request.user_id,
                user_message=request.user_message,
            ),
            learning=LearningState(
                goal_id=request.goal_id,
                active_plan=state["active_plan"],
                current_task=state["current_task"],
                mastery_summary=state["mastery_snapshot"],
                recent_learning_events=state["recent_learning_events"],
            ),
            evidence=EvidenceState(),
            execution=ExecutionState(run_id=request.thread_id, graph_version="phase2-v1"),
        )
        state["audit_log"].append({"node": "load_context", "status": "ok"})
        return state


class RetrievalService:
    def retrieve(self, state: dict[str, Any], dependencies: Any, *, citation_factory: Any, action_factory: Any) -> dict[str, Any]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        if prepared_context is not None:
            chunks = list(prepared_context.retrieved_context)
            retrieval_status = prepared_context.retrieval_status
            degraded_reason = prepared_context.degraded_reason
        else:
            chunks = dependencies.rag_repository.retrieve(request.user_message, top_k=5, user_id=request.user_id)
            retrieval_status = getattr(dependencies.rag_repository, "last_retrieval_status", "grounded" if chunks else "no_context")
            degraded_reason = getattr(dependencies.rag_repository, "degraded_reason", None)
        state["retrieved_context"] = chunks
        state["citations"] = chunks
        state["tutor_context"] = state["tutor_context"].model_copy(
            update={
                "rag_citations": [
                    citation_factory(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        citation_label=chunk.citation_label,
                        source_title=chunk.source_title,
                        source_url=chunk.source_url,
                        trusted_level=chunk.trusted_level,
                    )
                    for chunk in chunks
                ]
            }
        )
        state["workflow_state"] = state["workflow_state"].model_copy(
            update={"evidence": EvidenceState(retrieved_chunk_ids=[chunk.chunk_id for chunk in chunks])}
        )
        state.setdefault("workflow_actions", []).append(
            action_factory(
                action_type="record_tool_call",
                audit_payload={
                    "tool_name": "rag.retrieve",
                    "request_hash": str(hash(request.user_message)),
                    "response_summary": {
                        "chunk_count": len(chunks),
                        "retrieval_status": retrieval_status,
                        "degraded_reason": degraded_reason,
                    },
                    "status": "failed" if retrieval_status == "failed" else "success",
                },
            )
        )
        state["audit_log"].append(
            {"node": "retrieve_context", "chunk_count": len(chunks), "retrieval_status": retrieval_status, "degraded_reason": degraded_reason}
        )
        return state


class TeacherService:
    def teach(self, state: dict[str, Any], dependencies: Any) -> dict[str, Any]:
        request = state["request"]
        state["route"] = "teaching"
        state["final_answer"] = dependencies.llm_client.complete(
            role="teacher",
            prompt=request.user_message or "Explain the current task.",
            tutor_context=state["tutor_context"],
            conversation_context=None,
            context=state.get("retrieved_context", []),
        )
        state["audit_log"].append({"node": "teacher", "status": "ok"})
        return state


class AssessmentService:
    def build_draft(self, state: dict[str, Any], *, build_assessment: Any) -> dict[str, Any]:
        request = state["request"]
        node_ids = request.knowledge_node_ids or state.get("current_task", {}).get("knowledge_node_ids", [])
        draft = build_assessment(request.assessment_type, node_ids)
        state.update(route="assessment", assessment_draft=draft, final_answer=f"Assessment draft created with {len(draft.items)} items.")
        state["audit_log"].append({"node": "build_assessment", "assessment_id": draft.assessment_id})
        return state

    def grade_attempt(self, state: dict[str, Any], dependencies: Any, *, grade_assessment: Any, mastery_updates: Any) -> dict[str, Any]:
        request = state["request"]
        draft = dependencies.assessment_repository.get_assessment_draft(request.assessment_id or "")
        result = grade_assessment(draft, request.submitted_answers)
        updates = mastery_updates(draft, result, state.get("mastery_snapshot", {}))
        state.update(route="assessment", assessment_draft=draft, assessment_result=result, mastery_updates=updates, final_answer=result.feedback)
        state["audit_log"].append({"node": "grade_assessment", "score": result.score})
        return state


class ObserverService:
    def observe(self, state: dict[str, Any], *, build_signals: Any, decide_action: Any) -> dict[str, Any]:
        base_signals = dict(state.get("observer_signals") or {})
        result = state.get("assessment_result")
        if result is not None:
            mastery_delta = min((update.new_score - update.previous_score for update in state["mastery_updates"]), default=0)
            signals = build_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d", 0.95), correctness_rate=result.score / 100,
                mastery_delta=mastery_delta,
                low_mastery_nodes=[{"knowledge_node_id": update.knowledge_node_id, "score": update.new_score} for update in state["mastery_updates"] if update.new_score < 70],
                wrong_reason_tags=[tag for answer in result.answers for tag in answer.evidence_json.get("wrong_reason_tags", [])],
                recent_attempts=[{"assessment_id": result.assessment_id, "attempt_id": result.attempt_id, "score": result.score}],
                review_queue=base_signals.get("review_queue"), phase_assessment=base_signals.get("phase_assessment"),
            )
        elif state["request"].trigger_type == "task_completed":
            signals = build_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d", 0.85), correctness_rate=base_signals.get("correctness_rate", 0.8),
                mastery_delta=base_signals.get("mastery_delta", 1), low_mastery_nodes=base_signals.get("low_mastery_nodes", []),
                wrong_reason_tags=base_signals.get("wrong_reason_tags", []), recent_attempts=base_signals.get("recent_attempts", []),
                review_queue=base_signals.get("review_queue"), phase_assessment=base_signals.get("phase_assessment"),
            )
        else:
            signals = build_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d"), correctness_rate=base_signals.get("correctness_rate"),
                mastery_delta=base_signals.get("mastery_delta"), low_mastery_nodes=base_signals.get("low_mastery_nodes", []),
                wrong_reason_tags=base_signals.get("wrong_reason_tags", []), recent_attempts=base_signals.get("recent_attempts", []),
                review_queue=base_signals.get("review_queue"), phase_assessment=base_signals.get("phase_assessment"),
            )
        signals["missing_data_strategy"] = {**base_signals.get("missing_data_strategy", {}), **signals.get("missing_data_strategy", {})}
        decision = decide_action(signals)
        state.update(observer_signals=decision.evidence_json, observer_decision=decision)
        if state.get("route") != "teaching":
            state["route"] = "observe"
        state["audit_log"].append({"node": "observer", "decision": decision.decision})
        return state


class PlanningService:
    def plan(self, state: dict[str, Any], *, decide_action: Any, generate_adjustment: Any) -> dict[str, Any]:
        request = state["request"]
        decision = state.get("observer_decision")
        if decision is None:
            decision = decide_action(state.get("observer_signals", {}))
            state.update(observer_decision=decision, observer_signals=decision.evidence_json)
        adjustment = generate_adjustment(
            user_id=request.user_id, goal_id=request.goal_id, previous_plan_id=state.get("active_plan", {}).get("id", "plan-1"),
            decision=decision, trigger_type="manual" if request.trigger_type == "manual_replan" else request.trigger_type,
            state_snapshot=state.get("state_snapshot"), observer_signals=state.get("observer_signals", decision.evidence_json),
            manual_request=request.user_message if request.trigger_type == "manual_replan" else "",
        )
        state.update(route="replan", plan_adjustment=adjustment, final_answer=f"Plan adjustment proposed: {adjustment.decision}.")
        state["audit_log"].append({"node": "planner", "decision": adjustment.decision})
        return state


class MemoryService:
    def decide(self, state: dict[str, Any], dependencies: Any) -> dict[str, Any]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        privacy_settings = (
            prepared_context.memory_privacy_settings
            if prepared_context is not None
            else MemoryPrivacySettings.model_validate(state.get("state_snapshot", {}).get("memory_privacy_settings", {}))
        )
        decisions = dependencies.memory_gate(
            user_id=request.user_id, goal_id=request.goal_id, explicit_candidates=list(request.memory_candidates),
            assessment_result=state.get("assessment_result"), mastery_updates=list(state.get("mastery_updates", [])), privacy_settings=privacy_settings,
        )
        state["memory_decisions"] = decisions
        state["audit_log"].append(
            {"node": "memory_gate", "candidate_count": len(decisions), "approved": sum(item.decision == "approved" for item in decisions), "rejected": sum(item.decision == "rejected" for item in decisions), "policy_version": "memory-gate-v1"}
        )
        return state


class WorkflowPersistenceService:
    def build_actions(self, *, state: dict[str, Any], action_factory: Any) -> list[Any]:
        actions: list[Any] = []
        request = state.get("request")
        if state.get("assessment_draft") is not None and state.get("assessment_result") is None:
            actions.append(action_factory(action_type="save_assessment_draft", assessment_draft=state["assessment_draft"]))
        if state.get("assessment_result") is not None:
            actions.extend(
                [
                    action_factory(action_type="save_attempt_result", assessment_result=state["assessment_result"]),
                    action_factory(action_type="save_mastery_updates", mastery_updates=state.get("mastery_updates", [])),
                ]
            )
        if state.get("plan_adjustment") is not None:
            adjustment = state["plan_adjustment"]
            actions.extend(
                [
                    action_factory(action_type="save_plan_adjustment", plan_adjustment=adjustment),
                    action_factory(action_type="refresh_state_snapshot", user_id=request.user_id, goal_id=request.goal_id, snapshot_updates={"latest_plan_adjustment_id": adjustment.adjustment_id, "latest_plan_adjustment": adjustment.model_dump()}),
                ]
            )
        if state.get("memory_decisions"):
            actions.append(action_factory(action_type="save_memory", user_id=request.user_id, goal_id=request.goal_id, memory_decisions=state["memory_decisions"]))
        return actions
