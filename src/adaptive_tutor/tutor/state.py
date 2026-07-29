"""Adapter between the legacy LangGraph dictionary and domain workflow state."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from .identifiers import new_run_id
from .models import ConversationState, EvidenceState, ExecutionState, LearningState, TutorWorkflowState


class LegacyTutorStateAdapter:
    """Own the one-way conversion at the legacy graph boundary."""

    def ingress(
        self,
        legacy_state: Mapping[str, object],
        *,
        run_id: str | None = None,
        graph_version: str = "phase2-v1",
    ) -> TutorWorkflowState:
        existing = legacy_state.get("workflow_state")
        if isinstance(existing, TutorWorkflowState):
            return existing

        request = legacy_state.get("request")
        thread_id = _value(legacy_state, request, "thread_id")
        user_id = _value(legacy_state, request, "user_id")
        goal_id = _value(legacy_state, request, "goal_id")
        user_message = _value(legacy_state, request, "user_message")
        return TutorWorkflowState(
            conversation=ConversationState(thread_id=thread_id, user_id=user_id, user_message=user_message),
            learning=LearningState(
                goal_id=goal_id,
                active_plan=_mapping(legacy_state.get("active_plan")),
                current_task=_optional_mapping(legacy_state.get("current_task")),
                mastery_summary=_mapping(legacy_state.get("mastery_snapshot")),
                recent_learning_events=_mappings(legacy_state.get("recent_learning_events")),
            ),
            evidence=EvidenceState(retrieved_chunk_ids=_strings(legacy_state.get("retrieved_chunk_ids"))),
            execution=ExecutionState(run_id=run_id or new_run_id(), graph_version=graph_version),
        )

    def egress(
        self, legacy_state: MutableMapping[str, object], workflow_state: TutorWorkflowState
    ) -> MutableMapping[str, object]:
        """Project compatibility aliases without creating a second state authority."""

        legacy_state["workflow_state"] = workflow_state
        legacy_state["thread_id"] = workflow_state.conversation.thread_id
        legacy_state["user_id"] = workflow_state.conversation.user_id
        legacy_state["goal_id"] = workflow_state.learning.goal_id
        legacy_state["user_message"] = workflow_state.conversation.user_message
        legacy_state["active_plan"] = workflow_state.learning.active_plan
        legacy_state["current_task"] = workflow_state.learning.current_task
        legacy_state["mastery_snapshot"] = workflow_state.learning.mastery_summary
        legacy_state["recent_learning_events"] = workflow_state.learning.recent_learning_events
        legacy_state["retrieved_chunk_ids"] = workflow_state.evidence.retrieved_chunk_ids
        legacy_state["run_id"] = workflow_state.execution.run_id
        return legacy_state


def _value(legacy_state: Mapping[str, object], request: object, name: str) -> str:
    value = legacy_state.get(name, getattr(request, name, ""))
    return value if isinstance(value, str) else ""


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_mapping(value: object) -> dict[str, object] | None:
    return _mapping(value) if isinstance(value, Mapping) else None


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
