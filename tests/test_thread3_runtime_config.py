from __future__ import annotations

import pytest

from backend.app.core.runtime_config import missing_runtime_configuration, thread3_feature_flags


def test_thread3_flags_default_to_false(monkeypatch) -> None:
    for name in (
        "FEATURE_STRUCTURED_ANSWER_V2",
        "FEATURE_GROUNDING_V2",
        "FEATURE_ASSESSMENT_INTELLIGENCE_V2",
        "FEATURE_PLANNER_PROPOSAL_V2",
        "FEATURE_MCP_TOOL_ROUTER_V2",
        "FEATURE_AGENT_TOOL_LOOP_V1",
    ):
        monkeypatch.delenv(name, raising=False)
    assert all(value is False for value in thread3_feature_flags().values())


def test_thread3_grounding_requires_structured_answer(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_GROUNDING_V2", "true")
    monkeypatch.delenv("FEATURE_STRUCTURED_ANSWER_V2", raising=False)
    with pytest.raises(ValueError, match="FEATURE_STRUCTURED_ANSWER_V2"):
        thread3_feature_flags()


def test_agent_decision_budget_is_validated_as_runtime_configuration(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AGENT_MAX_DECISIONS", "9")

    errors = missing_runtime_configuration()

    assert any("AGENT_MAX_DECISIONS" in error for error in errors)
