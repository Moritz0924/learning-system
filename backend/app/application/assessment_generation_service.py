from __future__ import annotations

import os
from dataclasses import dataclass

from backend.app.domain.assessment.contracts import AssessmentGenerationBundleV2, AssessmentGenerationContextV2
from backend.app.domain.assessment.errors import AssessmentUnavailable
from backend.app.domain.assessment.generation_policy import BlueprintRegistry, deterministic_generation, validate_generation_bundle
from backend.app.infrastructure.llm.structured_output_client import StructuredOutputClient


@dataclass(frozen=True)
class AssessmentGenerationOutcome:
    bundle: AssessmentGenerationBundleV2
    mode: str
    model: str | None
    metadata: dict[str, object]


class AssessmentGenerationService:
    def __init__(self, *, client: StructuredOutputClient | None = None, registry: BlueprintRegistry | None = None) -> None:
        self.client = client or StructuredOutputClient()
        self.registry = registry or BlueprintRegistry.default()

    def generate(self, context: AssessmentGenerationContextV2) -> AssessmentGenerationOutcome:
        mode = os.getenv("ASSESSMENT_GENERATOR_MODE", "hybrid").strip().lower() or "hybrid"
        if mode not in {"hybrid", "remote", "deterministic"}:
            mode = "hybrid"
        if mode != "deterministic":
            result = self.client.complete(
                role="assessment_generator",
                prompt_version="assessment-generator-v2",
                system_instructions="Generate grounded assessment items only. Do not create database IDs, plan actions, or chain-of-thought.",
                input_payload=context,
                output_model=AssessmentGenerationBundleV2,
            )
            if result.value is not None:
                validate_generation_bundle(context, result.value)
                return AssessmentGenerationOutcome(result.value, "remote", result.model, dict(self.client.last_metadata))
            if mode == "remote":
                raise AssessmentUnavailable("Remote assessment generation is unavailable.", code=result.error_code or "assessment.generation_unavailable")
        bundle = deterministic_generation(context, self.registry)
        return AssessmentGenerationOutcome(bundle, "offline" if mode == "deterministic" else "degraded", None, dict(self.client.last_metadata))
