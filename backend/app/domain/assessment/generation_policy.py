from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .contracts import (
    AssessmentGenerationBundleV2,
    AssessmentGenerationContextV2,
    GeneratedAssessmentItemV2,
    GeneratedOptionV2,
    RubricCriterionV2,
)
from .errors import AssessmentDomainError, AssessmentUnavailable


@dataclass(frozen=True)
class BlueprintRegistry:
    blueprints: dict[str, dict[str, Any]]

    @classmethod
    def default(cls) -> "BlueprintRegistry":
        path = Path(__file__).parent / "blueprints" / "ai_app_dev_v1.yaml"
        with path.open("r", encoding="utf-8") as handle:
            source = yaml.safe_load(handle) or {}
        return cls({key: value for key, value in source.items() if key != "common"})

    def for_code(self, code: str) -> dict[str, Any] | None:
        return self.blueprints.get(code)


def deterministic_generation(
    context: AssessmentGenerationContextV2,
    registry: BlueprintRegistry,
) -> AssessmentGenerationBundleV2:
    nodes = {node.knowledge_node_id: node for node in context.knowledge_nodes}
    requested = [nodes[node_id] for node_id in context.requested_knowledge_node_ids if node_id in nodes]
    if len(requested) != len(context.requested_knowledge_node_ids):
        raise AssessmentDomainError("A requested knowledge node is missing from generation context.", code="assessment.generation_output_invalid")
    unavailable = [node.code for node in requested if registry.for_code(node.code) is None]
    if unavailable:
        raise AssessmentUnavailable(f"No deterministic blueprint is available for: {', '.join(unavailable)}")

    source_ids = [excerpt.chunk_id for excerpt in context.source_excerpts]
    items: list[GeneratedAssessmentItemV2] = []
    for index in range(context.requested_item_count):
        node = requested[index % len(requested)]
        blueprint = registry.for_code(node.code)
        assert blueprint is not None
        options = list(blueprint["question_blueprints"])
        question = options[(index // len(requested)) % len(options)]
        data = {"title": node.title, "code": node.code, "objective": (node.learning_objectives or [node.title])[0]}
        rendered_options = [
            GeneratedOptionV2(option_key=item["option_key"], label=_render(item["label"], data))
            for item in question.get("options", [])
        ]
        criteria = [
            RubricCriterionV2(
                criterion_id=criterion["criterion_id"],
                description=_render(criterion["description"], data),
                max_points=criterion["max_points"],
                required_evidence=[_render(value, data) for value in criterion.get("required_evidence", [])],
                accepted_concepts=[_render(value, data) for value in criterion.get("accepted_concepts", [])],
                common_error_tags=list(criterion.get("common_error_tags", [])),
                deterministic_signals=[_render(value, data) for value in criterion.get("deterministic_signals", [])],
            )
            for criterion in question["rubric"]
        ]
        items.append(
            GeneratedAssessmentItemV2(
                item_key=f"{node.code}-{question['question_type']}-{index + 1}",
                knowledge_node_id=node.knowledge_node_id,
                question_type=question["question_type"],
                target_skill=question["target_skill"],
                prompt=_render(question["prompt_template"], data),
                options=rendered_options,
                reference_answer=_render(question["reference_answer"], data),
                rubric=criteria,
                difficulty=node.difficulty,
                source_chunk_ids=source_ids[:1],
            )
        )
    bundle = AssessmentGenerationBundleV2(
        schema_version="assessment-generation-v2",
        generator_version="assessment-generator-v2",
        items=items,
    )
    validate_generation_bundle(context, bundle)
    return bundle


def validate_generation_bundle(
    context: AssessmentGenerationContextV2,
    bundle: AssessmentGenerationBundleV2,
) -> None:
    if len(bundle.items) != context.requested_item_count:
        raise AssessmentDomainError("Generated item count does not match the request.", code="assessment.generation_output_invalid")
    requested = set(context.requested_knowledge_node_ids)
    item_keys = [item.item_key for item in bundle.items]
    if len(item_keys) != len(set(item_keys)):
        raise AssessmentDomainError("Generated item keys must be unique.", code="assessment.generation_output_invalid")
    generated_nodes = {item.knowledge_node_id for item in bundle.items}
    if not requested <= generated_nodes:
        raise AssessmentDomainError("Generated items do not cover every requested knowledge node.", code="assessment.generation_output_invalid")
    allowed_sources = {excerpt.chunk_id for excerpt in context.source_excerpts}
    for item in bundle.items:
        if item.knowledge_node_id not in requested:
            raise AssessmentDomainError("Generated item references an unrequested knowledge node.", code="assessment.generation_output_invalid")
        if not set(item.source_chunk_ids) <= allowed_sources:
            raise AssessmentDomainError("Generated item references an unavailable source chunk.", code="assessment.invalid_source_reference")
        if sum(criterion.max_points for criterion in item.rubric) != 100:
            raise AssessmentDomainError("Each rubric must total 100 points.", code="assessment.invalid_rubric")
        criterion_ids = [criterion.criterion_id for criterion in item.rubric]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise AssessmentDomainError("Rubric criterion IDs must be unique.", code="assessment.invalid_rubric")
        if item.question_type == "choice":
            option_ids = [option.option_key for option in item.options]
            if len(option_ids) < 2 or len(option_ids) != len(set(option_ids)) or item.reference_answer not in option_ids:
                raise AssessmentDomainError("Choice questions require a unique server-held correct option.", code="assessment.invalid_rubric")
        elif item.options:
            raise AssessmentDomainError("Only choice items may contain options.", code="assessment.invalid_rubric")


def _render(template: str, data: dict[str, str]) -> str:
    return template.format(**data)
