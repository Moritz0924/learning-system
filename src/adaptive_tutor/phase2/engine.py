from __future__ import annotations

from time import perf_counter
from typing import Any

from langgraph.graph import END, StateGraph

from .assessment import build_assessment_draft, grade_assessment_attempt, mastery_updates_from_attempt
from .ports import Phase2Dependencies
from .replanning import build_observer_signals, decide_observer_action_from_signals, generate_plan_adjustment
from .schemas import TutorRunRequest, TutorRunResult, TutorState


class Phase2TutorEngine:
    def __init__(self, dependencies: Phase2Dependencies):
        self.dependencies = dependencies
        self.graph = self._build_graph()

    def run(self, request: TutorRunRequest) -> TutorRunResult:
        started = perf_counter()
        state: dict[str, Any] = {
            "request": request,
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "goal_id": request.goal_id,
            "trigger_type": request.trigger_type,
            "user_message": request.user_message,
            "audit_log": [],
            "citations": [],
            "mastery_updates": [],
        }
        try:
            output = self.graph.invoke(state)
            status = "success"
            error_message = None
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
            raise
        finally:
            latency_ms = int((perf_counter() - started) * 1000)
            self.dependencies.audit_sink.record_agent_run(
                {
                    "thread_id": request.thread_id,
                    "user_id": request.user_id,
                    "goal_id": request.goal_id,
                    "graph_name": "phase2_tutor_graph",
                    "graph_version": "phase2-v1",
                    "trigger_type": request.trigger_type,
                    "status": status,
                    "latency_ms": latency_ms,
                    "error_message": error_message,
                }
            )
        return TutorRunResult(
            route=output.get("route", "teaching"),
            final_answer=output.get("final_answer", ""),
            citations=output.get("citations", []),
            assessment_draft=output.get("assessment_draft"),
            assessment_result=output.get("assessment_result"),
            mastery_updates=output.get("mastery_updates", []),
            observer_decision=output.get("observer_decision"),
            plan_adjustment=output.get("plan_adjustment"),
            audit_log=output.get("audit_log", []),
        )

    def _build_graph(self):
        graph = StateGraph(TutorState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("diagnosis", self._diagnosis)
        graph.add_node("retrieve_context", self._retrieve_context)
        graph.add_node("teacher", self._teacher)
        graph.add_node("build_assessment", self._build_assessment)
        graph.add_node("grade_assessment", self._grade_assessment)
        graph.add_node("observer", self._observer)
        graph.add_node("planner", self._planner)
        graph.add_node("memory_gate", self._memory_gate)
        graph.add_node("persist", self._persist)

        graph.set_entry_point("load_context")
        graph.add_conditional_edges(
            "load_context",
            self._route_after_load,
            {
                "diagnosis": "diagnosis",
                "retrieve_context": "retrieve_context",
                "build_assessment": "build_assessment",
                "grade_assessment": "grade_assessment",
                "observer": "observer",
                "planner": "planner",
            },
        )
        graph.add_edge("diagnosis", "planner")
        graph.add_edge("retrieve_context", "teacher")
        graph.add_edge("teacher", "observer")
        graph.add_edge("build_assessment", "persist")
        graph.add_edge("grade_assessment", "observer")
        graph.add_conditional_edges(
            "observer",
            self._route_after_observer,
            {"planner": "planner", "memory_gate": "memory_gate"},
        )
        graph.add_edge("planner", "persist")
        graph.add_edge("memory_gate", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    def _load_context(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        snapshot = self.dependencies.state_repository.load_context(request.user_id, request.goal_id)
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
        state["audit_log"].append({"node": "load_context", "status": "ok"})
        return state

    def _diagnosis(self, state: dict) -> dict:
        state["route"] = "diagnostic"
        state["final_answer"] = "Baseline diagnosis received; preparing an initial plan patch."
        state["audit_log"].append({"node": "diagnosis", "status": "ok"})
        return state

    def _retrieve_context(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        chunks = self.dependencies.rag_repository.retrieve(request.user_message, top_k=5, user_id=request.user_id)
        state["retrieved_context"] = chunks
        state["citations"] = chunks
        self.dependencies.audit_sink.record_tool_call(
            {
                "tool_name": "rag.retrieve",
                "request_hash": str(hash(request.user_message)),
                "response_summary": {"chunk_count": len(chunks)},
                "status": "success",
            }
        )
        state["audit_log"].append({"node": "retrieve_context", "chunk_count": len(chunks)})
        return state

    def _teacher(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        chunks = state.get("retrieved_context", [])
        state["route"] = "teaching"
        state["final_answer"] = self.dependencies.llm_client.complete(
            role="teacher",
            prompt=request.user_message or "Explain the current task.",
            context=chunks,
        )
        state["audit_log"].append({"node": "teacher", "status": "ok"})
        return state

    def _build_assessment(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        node_ids = request.knowledge_node_ids or state.get("current_task", {}).get("knowledge_node_ids", [])
        draft = build_assessment_draft(request.assessment_type, node_ids)
        state["route"] = "assessment"
        state["assessment_draft"] = draft
        state["final_answer"] = f"Assessment draft created with {len(draft.items)} items."
        state["audit_log"].append({"node": "build_assessment", "assessment_id": draft.assessment_id})
        return state

    def _grade_assessment(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        draft = self.dependencies.assessment_repository.get_assessment_draft(request.assessment_id or "")
        result = grade_assessment_attempt(draft, request.submitted_answers)
        updates = mastery_updates_from_attempt(draft, result, state.get("mastery_snapshot", {}))
        state["route"] = "assessment"
        state["assessment_draft"] = draft
        state["assessment_result"] = result
        state["mastery_updates"] = updates
        state["final_answer"] = result.feedback
        state["audit_log"].append({"node": "grade_assessment", "score": result.score})
        return state

    def _observer(self, state: dict) -> dict:
        base_signals = dict(state.get("observer_signals") or {})
        result = state.get("assessment_result")
        if result is not None:
            mastery_delta = min(
                (update.new_score - update.previous_score for update in state["mastery_updates"]),
                default=0,
            )
            signals = build_observer_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d", 0.95),
                correctness_rate=result.score / 100,
                mastery_delta=mastery_delta,
                low_mastery_nodes=[
                    {"knowledge_node_id": update.knowledge_node_id, "score": update.new_score}
                    for update in state["mastery_updates"]
                    if update.new_score < 70
                ],
                wrong_reason_tags=[
                    tag
                    for answer in result.answers
                    for tag in answer.evidence_json.get("wrong_reason_tags", [])
                ],
                recent_attempts=[{"assessment_id": result.assessment_id, "attempt_id": result.attempt_id, "score": result.score}],
                review_queue=base_signals.get("review_queue"),
                phase_assessment=base_signals.get("phase_assessment"),
            )
        elif state["request"].trigger_type == "task_completed":
            signals = build_observer_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d", 0.85),
                correctness_rate=base_signals.get("correctness_rate", 0.8),
                mastery_delta=base_signals.get("mastery_delta", 1),
                low_mastery_nodes=base_signals.get("low_mastery_nodes", []),
                wrong_reason_tags=base_signals.get("wrong_reason_tags", []),
                recent_attempts=base_signals.get("recent_attempts", []),
                review_queue=base_signals.get("review_queue"),
                phase_assessment=base_signals.get("phase_assessment"),
            )
        else:
            signals = build_observer_signals(
                completion_rate_7d=base_signals.get("completion_rate_7d"),
                correctness_rate=base_signals.get("correctness_rate"),
                mastery_delta=base_signals.get("mastery_delta"),
                low_mastery_nodes=base_signals.get("low_mastery_nodes", []),
                wrong_reason_tags=base_signals.get("wrong_reason_tags", []),
                recent_attempts=base_signals.get("recent_attempts", []),
                review_queue=base_signals.get("review_queue"),
                phase_assessment=base_signals.get("phase_assessment"),
            )
        signals["missing_data_strategy"] = {
            **base_signals.get("missing_data_strategy", {}),
            **signals.get("missing_data_strategy", {}),
        }
        decision = decide_observer_action_from_signals(signals)
        state["observer_signals"] = decision.evidence_json
        state["observer_decision"] = decision
        if state.get("route") != "teaching":
            state["route"] = "observe"
        state["audit_log"].append({"node": "observer", "decision": decision.decision})
        return state

    def _planner(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        decision = state.get("observer_decision")
        if decision is None:
            decision = decide_observer_action_from_signals(state.get("observer_signals", {}))
            state["observer_decision"] = decision
            state["observer_signals"] = decision.evidence_json
        adjustment = generate_plan_adjustment(
            user_id=request.user_id,
            goal_id=request.goal_id,
            previous_plan_id=state.get("active_plan", {}).get("id", "plan-1"),
            decision=decision,
            trigger_type="manual" if request.trigger_type == "manual_replan" else request.trigger_type,
            state_snapshot=state.get("state_snapshot"),
            observer_signals=state.get("observer_signals", decision.evidence_json),
            manual_request=request.user_message if request.trigger_type == "manual_replan" else "",
        )
        state["route"] = "replan"
        state["plan_adjustment"] = adjustment
        state["final_answer"] = f"Plan adjustment proposed: {adjustment.decision}."
        state["audit_log"].append({"node": "planner", "decision": adjustment.decision})
        return state

    def _memory_gate(self, state: dict) -> dict:
        state["approved_memories"] = []
        state["audit_log"].append({"node": "memory_gate", "approved": 0})
        return state

    def _persist(self, state: dict) -> dict:
        request: TutorRunRequest = state["request"]
        if state.get("assessment_draft") is not None and state.get("assessment_result") is None:
            self.dependencies.assessment_repository.save_assessment_draft(state["assessment_draft"])
        if state.get("assessment_result") is not None:
            self.dependencies.assessment_repository.save_attempt_result(state["assessment_result"])
            self.dependencies.assessment_repository.save_mastery_updates(state.get("mastery_updates", []))
        if state.get("plan_adjustment") is not None:
            adjustment = self.dependencies.plan_repository.save_plan_adjustment(state["plan_adjustment"])
            self.dependencies.state_repository.refresh_snapshot(
                request.user_id,
                request.goal_id,
                {
                    "latest_plan_adjustment_id": adjustment.adjustment_id,
                    "latest_plan_adjustment": adjustment.model_dump(),
                },
            )
        state["audit_log"].append({"node": "persist", "status": "ok"})
        return state

    def _route_after_load(self, state: dict) -> str:
        return {
            "onboarding": "diagnosis",
            "chat": "retrieve_context",
            "task_completed": "observer",
            "assessment_due": "build_assessment",
            "assessment_submitted": "grade_assessment",
            "manual_replan": "observer",
        }[state["trigger_type"]]

    def _route_after_observer(self, state: dict) -> str:
        if state["trigger_type"] == "manual_replan":
            return "planner"
        if state["trigger_type"] == "chat":
            return "memory_gate"
        decision = state.get("observer_decision")
        if decision is not None and decision.decision != "keep":
            return "planner"
        return "memory_gate"
