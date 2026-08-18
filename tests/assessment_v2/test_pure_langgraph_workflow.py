from __future__ import annotations

from backend.app.domain.assessment.generation_policy import BlueprintRegistry, deterministic_generation
from adaptive_tutor.phase2.assessment_v2 import AssessmentV2WorkflowPorts, Phase2AssessmentV2Workflow
from tests.assessment_v2.test_generation_contracts import _context


def test_v2_langgraph_generation_workflow_only_uses_prepared_context_and_ports() -> None:
    calls: list[str] = []

    def generate(context):
        calls.append(context.context_hash)
        return deterministic_generation(context, BlueprintRegistry.default())

    workflow = Phase2AssessmentV2Workflow(AssessmentV2WorkflowPorts(generator=generate))

    result = workflow.run_generation(_context())

    assert result.generation_bundle is not None
    assert calls == [_context().context_hash]
    assert [action.action_type for action in result.actions] == ["assessment_generated"]
