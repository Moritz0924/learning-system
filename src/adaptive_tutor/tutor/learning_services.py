"""Assessment, observation, planning, memory, and persistence workflow services."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping

from .contracts import TutorRuntimeDependencies
from .memory import MemoryPrivacySettings


class AssessmentService:
    def build_draft(self, state: MutableMapping[str, object], *, build_assessment: Callable[[str, list[str]], object]) -> MutableMapping[str, object]:
        request = state["request"]
        current_task = state.get("current_task")
        node_ids = getattr(request, "knowledge_node_ids") or (
            current_task.get("knowledge_node_ids", []) if isinstance(current_task, dict) else []
        )
        draft = build_assessment(getattr(request, "assessment_type"), node_ids)
        state.update(route="assessment", assessment_draft=draft, final_answer=f"Assessment draft created with {len(draft.items)} items.")
        _audit_log(state).append({"node": "build_assessment", "assessment_id": draft.assessment_id})
        return state

    def grade_attempt(
        self,
        state: MutableMapping[str, object],
        dependencies: TutorRuntimeDependencies,
        *,
        grade_assessment: Callable[[object, object], object],
        mastery_updates: Callable[[object, object, object], object],
    ) -> MutableMapping[str, object]:
        request = state["request"]
        draft = dependencies.assessment_repository.get_assessment_draft(getattr(request, "assessment_id") or "")
        result = grade_assessment(draft, getattr(request, "submitted_answers"))
        updates = mastery_updates(draft, result, state.get("mastery_snapshot", {}))
        state.update(route="assessment", assessment_draft=draft, assessment_result=result, mastery_updates=updates, final_answer=result.feedback)
        _audit_log(state).append({"node": "grade_assessment", "score": result.score})
        return state


class ObserverService:
    def observe(
        self,
        state: MutableMapping[str, object],
        *,
        build_signals: Callable[..., dict[str, object]],
        decide_action: Callable[[dict[str, object]], object],
    ) -> MutableMapping[str, object]:
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
        elif getattr(state["request"], "trigger_type") == "task_completed":
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
        _audit_log(state).append({"node": "observer", "decision": decision.decision})
        return state


class PlanningService:
    def plan(
        self,
        state: MutableMapping[str, object],
        *,
        decide_action: Callable[[dict[str, object]], object],
        generate_adjustment: Callable[..., object],
    ) -> MutableMapping[str, object]:
        request = state["request"]
        decision = state.get("observer_decision")
        if decision is None:
            decision = decide_action(state.get("observer_signals", {}))
            state.update(observer_decision=decision, observer_signals=decision.evidence_json)
        active_plan = state.get("active_plan")
        adjustment = generate_adjustment(
            user_id=getattr(request, "user_id"), goal_id=getattr(request, "goal_id"),
            previous_plan_id=active_plan.get("id", "plan-1") if isinstance(active_plan, dict) else "plan-1",
            decision=decision, trigger_type="manual" if getattr(request, "trigger_type") == "manual_replan" else getattr(request, "trigger_type"),
            state_snapshot=state.get("state_snapshot"), observer_signals=state.get("observer_signals", decision.evidence_json),
            manual_request=getattr(request, "user_message") if getattr(request, "trigger_type") == "manual_replan" else "",
        )
        state.update(route="replan", plan_adjustment=adjustment, final_answer=f"Plan adjustment proposed: {adjustment.decision}.")
        _audit_log(state).append({"node": "planner", "decision": adjustment.decision})
        return state


class MemoryService:
    def decide(
        self, state: MutableMapping[str, object], dependencies: TutorRuntimeDependencies
    ) -> MutableMapping[str, object]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        privacy_settings = (
            prepared_context.memory_privacy_settings
            if prepared_context is not None
            else MemoryPrivacySettings.model_validate(dict(state.get("state_snapshot", {})).get("memory_privacy_settings", {}))
        )
        decisions = dependencies.memory_gate(
            user_id=getattr(request, "user_id"), goal_id=getattr(request, "goal_id"), explicit_candidates=list(getattr(request, "memory_candidates")),
            assessment_result=state.get("assessment_result"), mastery_updates=list(state.get("mastery_updates", [])), privacy_settings=privacy_settings,
        )
        state["memory_decisions"] = decisions
        _audit_log(state).append(
            {"node": "memory_gate", "candidate_count": len(decisions), "approved": sum(item.decision == "approved" for item in decisions), "rejected": sum(item.decision == "rejected" for item in decisions), "policy_version": "memory-gate-v1"}
        )
        return state


class WorkflowPersistenceService:
    def build_actions(self, *, state: MutableMapping[str, object], action_factory: Callable[..., object]) -> list[object]:
        actions: list[object] = []
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
                    action_factory(action_type="refresh_state_snapshot", user_id=getattr(request, "user_id"), goal_id=getattr(request, "goal_id"), snapshot_updates={"latest_plan_adjustment_id": adjustment.adjustment_id, "latest_plan_adjustment": adjustment.model_dump()}),
                ]
            )
        if state.get("memory_decisions"):
            actions.append(action_factory(action_type="save_memory", user_id=getattr(request, "user_id"), goal_id=getattr(request, "goal_id"), memory_decisions=state["memory_decisions"]))
        return actions


def _audit_log(state: MutableMapping[str, object]) -> list[dict[str, object]]:
    audit_log = state.setdefault("audit_log", [])
    if not isinstance(audit_log, list):
        raise TypeError("legacy tutor state audit_log must be a list")
    return audit_log
