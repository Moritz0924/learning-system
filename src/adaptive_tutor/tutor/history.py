"""Safe, deterministic short-term conversation history handling."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .models import ConversationState, ConversationTurn, TutorWorkflowState


MAX_HISTORY_MESSAGE_CHARS = 8_000
MAX_CONVERSATION_SUMMARY_CHARS = 4_000
SUMMARY_MESSAGE_CHARS = 600


@dataclass(frozen=True)
class HistoryPolicy:
    max_turns: int = 12
    max_estimated_tokens: int = 16_000

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            raise ValueError("history max_turns must be positive")
        if self.max_estimated_tokens <= 0:
            raise ValueError("history max_estimated_tokens must be positive")


def restore_safe_conversation(
    current: TutorWorkflowState,
    saved: TutorWorkflowState | None,
    *,
    policy: HistoryPolicy,
) -> TutorWorkflowState:
    """Restore only bounded history when checkpoint ownership matches exactly."""

    if saved is None or not _same_scope(current, saved):
        return current
    saved_conversation = saved.conversation
    summary = _safe_text(
        saved_conversation.conversation_summary,
        max_chars=MAX_CONVERSATION_SUMMARY_CHARS,
    )
    turns = [
        _safe_turn(item) for item in saved_conversation.recent_turns
    ]
    if len(turns) >= policy.max_turns or _estimated_tokens(summary, turns) >= policy.max_estimated_tokens:
        summary = _deterministic_summary(summary, turns)
        turns = []
    restored = current.conversation.model_copy(
        update={
            "conversation_summary": summary,
            "recent_turns": turns,
        }
    )
    return current.model_copy(update={"conversation": restored})


def conversation_context(
    conversation: ConversationState,
) -> dict[str, object] | None:
    summary = _safe_text(
        conversation.conversation_summary,
        max_chars=MAX_CONVERSATION_SUMMARY_CHARS,
    )
    turns = [_safe_turn(item) for item in conversation.recent_turns]
    if not summary and not turns:
        return None
    return {
        "summary": summary,
        "turns": [turn.model_dump() for turn in turns],
    }


def record_completed_turn(
    workflow_state: TutorWorkflowState,
    *,
    user_message: str,
    assistant_message: str,
    policy: HistoryPolicy,
) -> TutorWorkflowState:
    conversation = workflow_state.conversation
    turns = [
        *[_safe_turn(item) for item in conversation.recent_turns],
        ConversationTurn(
            user_message=_safe_text(
                user_message,
                max_chars=MAX_HISTORY_MESSAGE_CHARS,
            ),
            assistant_message=_safe_text(
                assistant_message,
                max_chars=MAX_HISTORY_MESSAGE_CHARS,
            ),
        ),
    ]
    summary = _safe_text(
        conversation.conversation_summary,
        max_chars=MAX_CONVERSATION_SUMMARY_CHARS,
    )
    if len(turns) >= policy.max_turns or _estimated_tokens(summary, turns) >= policy.max_estimated_tokens:
        summary = _deterministic_summary(summary, turns)
        turns = []
    updated = conversation.model_copy(
        update={
            "user_message": _safe_text(
                user_message,
                max_chars=MAX_HISTORY_MESSAGE_CHARS,
            ),
            "conversation_summary": summary,
            "recent_turns": turns,
        }
    )
    return workflow_state.model_copy(update={"conversation": updated})


def _same_scope(current: TutorWorkflowState, saved: TutorWorkflowState) -> bool:
    return (
        current.conversation.thread_id == saved.conversation.thread_id
        and current.conversation.user_id == saved.conversation.user_id
        and current.learning.goal_id == saved.learning.goal_id
    )


def _safe_turn(turn: ConversationTurn) -> ConversationTurn:
    return ConversationTurn(
        user_message=_safe_text(
            turn.user_message,
            max_chars=MAX_HISTORY_MESSAGE_CHARS,
        ),
        assistant_message=_safe_text(
            turn.assistant_message,
            max_chars=MAX_HISTORY_MESSAGE_CHARS,
        ),
    )


def _safe_text(value: str, *, max_chars: int) -> str:
    normalized = "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32
        else " "
        for character in str(value)
    ).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _estimated_tokens(summary: str, turns: list[ConversationTurn]) -> int:
    characters = len(summary) + sum(
        len(turn.user_message) + len(turn.assistant_message) for turn in turns
    )
    return ceil(characters / 4)


def _deterministic_summary(
    existing_summary: str,
    turns: list[ConversationTurn],
) -> str:
    recent_sections = [
        (
            f"User: {_safe_text(turn.user_message, max_chars=SUMMARY_MESSAGE_CHARS)}\n"
            f"Assistant: {_safe_text(turn.assistant_message, max_chars=SUMMARY_MESSAGE_CHARS)}"
        )
        for turn in turns
    ]
    recent_text = "\n".join(recent_sections)
    if len(recent_text) >= MAX_CONVERSATION_SUMMARY_CHARS:
        return _tail_text(recent_text, max_chars=MAX_CONVERSATION_SUMMARY_CHARS)
    if not existing_summary:
        return recent_text
    separator = "\n"
    label = "Earlier summary: "
    earlier_budget = (
        MAX_CONVERSATION_SUMMARY_CHARS
        - len(recent_text)
        - len(separator)
        - len(label)
    )
    if earlier_budget <= 0:
        return recent_text
    earlier = _tail_text(existing_summary, max_chars=earlier_budget)
    return f"{label}{earlier}{separator}{recent_text}"


def _tail_text(value: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars == 1:
        return "…"
    return "…" + value[-(max_chars - 1) :]
