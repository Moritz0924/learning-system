from __future__ import annotations

from time import perf_counter
from typing import Any

from langgraph.graph import END, StateGraph

from .ports import Phase2Dependencies
from .schemas import (
    PreparedTutorContext,
    TutorRagCitation,
    TutorRunRequest,
    TutorRunResult,
    TutorState,
    WorkflowAction,
)
from adaptive_tutor.tutor.memory import MEMORY_GATE_POLICY_VERSION
from adaptive_tutor.tutor.services import (
    AssessmentService,
    GroundingService,
    IntentRouter,
    MemoryService,
    ObserverService,
    PlanningService,
    RetrievalService,
    SessionContextService,
    TeacherService,
    WorkflowPersistenceService,
)
from .assessment import build_assessment_draft, grade_assessment_attempt, mastery_updates_from_attempt
from .replanning import build_observer_signals, decide_observer_action_from_signals, generate_plan_adjustment


class Phase2TutorEngine:
    def __init__(self, dependencies: Phase2Dependencies):
        self.dependencies = dependencies
        self.session_context_service = SessionContextService()
        self.intent_router = IntentRouter()
        self.retrieval_service = RetrievalService()
        self.teacher_service = TeacherService()
        self.grounding_service = GroundingService()
        self.assessment_service = AssessmentService()
        self.observer_service = ObserverService()
        self.planning_service = PlanningService()
        self.memory_service = MemoryService()
        self.workflow_persistence_service = WorkflowPersistenceService()
        self.graph = self._build_graph()

    def run(
        self,
        request: TutorRunRequest,
        *,
        prepared_context: PreparedTutorContext | None = None,
    ) -> TutorRunResult:
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
            "workflow_actions": [],
        }
        if prepared_context is not None:
            state["prepared_context"] = prepared_context
        output = self.graph.invoke(state)
        latency_ms = int((perf_counter() - started) * 1000)
        audit_payload: dict[str, Any] = {
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "goal_id": request.goal_id,
            "graph_name": "phase2_tutor_graph",
            "graph_version": "phase2-v1",
            "trigger_type": request.trigger_type,
            "status": "success",
            "latency_ms": latency_ms,
            "error_message": None,
        }
        if request.trigger_type == "chat":
            memory_selection = prepared_context.memory_selection if prepared_context is not None else None
            audit_payload["memory_context"] = {
                "selected_memory_ids": [] if memory_selection is None else memory_selection.selected_memory_ids,
                "policy_version": "memory-context-v1" if memory_selection is None else memory_selection.policy_version,
            }
        memory_decisions = output.get("memory_decisions", [])
        if memory_decisions:
            audit_payload["memory_gate"] = {
                "policy_version": MEMORY_GATE_POLICY_VERSION,
                "items": [
                    {
                        "candidate_id": decision.candidate.candidate_id,
                        "origin": decision.candidate.origin,
                        "decision": decision.decision,
                        "reason_code": decision.reason_code,
                    }
                    for decision in memory_decisions
                ],
            }
        output.setdefault("workflow_actions", []).append(
            WorkflowAction(
                action_type="record_agent_run",
                audit_payload=audit_payload,
            )
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
            workflow_actions=output.get("workflow_actions", []),
            memory_decisions=memory_decisions,
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
        graph.add_edge("planner", "memory_gate")
        graph.add_edge("memory_gate", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    def _load_context(self, state: dict) -> dict:
        return self.session_context_service.load(state, self.dependencies)

    def _diagnosis(self, state: dict) -> dict:
        state["route"] = "diagnostic"
        state["final_answer"] = "Baseline diagnosis received; preparing an initial plan patch."
        state["audit_log"].append({"node": "diagnosis", "status": "ok"})
        return state

    def _retrieve_context(self, state: dict) -> dict:
        return self.retrieval_service.retrieve(
            state, self.dependencies, citation_factory=TutorRagCitation, action_factory=WorkflowAction
        )

    def _teacher(self, state: dict) -> dict:
        state = self.teacher_service.teach(state, self.dependencies)
        grounding = self.grounding_service.validate(
            answer=state["final_answer"],
            retrieved_chunk_ids=[chunk.chunk_id for chunk in state.get("retrieved_context", [])],
        )
        workflow_state = state["workflow_state"]
        state["workflow_state"] = workflow_state.model_copy(
            update={"evidence": workflow_state.evidence.model_copy(update={"grounding_result": grounding.model_dump()})}
        )
        return state

    def _build_assessment(self, state: dict) -> dict:
        return self.assessment_service.build_draft(state, build_assessment=build_assessment_draft)

    def _grade_assessment(self, state: dict) -> dict:
        return self.assessment_service.grade_attempt(
            state,
            self.dependencies,
            grade_assessment=grade_assessment_attempt,
            mastery_updates=mastery_updates_from_attempt,
        )

    def _observer(self, state: dict) -> dict:
        return self.observer_service.observe(
            state,
            build_signals=build_observer_signals,
            decide_action=decide_observer_action_from_signals,
        )

    def _planner(self, state: dict) -> dict:
        return self.planning_service.plan(
            state,
            decide_action=decide_observer_action_from_signals,
            generate_adjustment=generate_plan_adjustment,
        )

    def _memory_gate(self, state: dict) -> dict:
        return self.memory_service.decide(state, self.dependencies)

    def _persist(self, state: dict) -> dict:
        state.setdefault("workflow_actions", []).extend(
            self.workflow_persistence_service.build_actions(state=state, action_factory=WorkflowAction)
        )
        state["audit_log"].append({"node": "persist", "status": "ok"})
        return state

    def _route_after_load(self, state: dict) -> str:
        return self.intent_router.route_after_load(state["trigger_type"])

    def _route_after_observer(self, state: dict) -> str:
        return self.intent_router.route_after_observer(
            trigger_type=state["trigger_type"], observer_decision=state.get("observer_decision")
        )
