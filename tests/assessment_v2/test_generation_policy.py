from __future__ import annotations

import pytest

from backend.app.domain.assessment.contracts import AssessmentSourceExcerpt
from backend.app.domain.assessment.errors import AssessmentDomainError
from backend.app.domain.assessment.generation_policy import (
    BlueprintRegistry,
    deterministic_generation,
    validate_generation_bundle,
)
from tests.assessment_v2.test_generation_contracts import _context


def test_blueprint_generation_covers_requested_node_with_a_verifiable_rubric() -> None:
    context = _context().model_copy(
        update={
            "requested_item_count": 3,
            "source_excerpts": [
                AssessmentSourceExcerpt(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    citation_label="Python guide",
                    content="A function is declared with def.",
                    trusted_level=2,
                )
            ],
        }
    )
    registry = BlueprintRegistry.default()

    bundle = deterministic_generation(context, registry)

    validate_generation_bundle(context, bundle)
    assert len(bundle.items) == 3
    assert {item.knowledge_node_id for item in bundle.items} == {"node-python"}
    assert all(sum(criterion.max_points for criterion in item.rubric) == 100 for item in bundle.items)
    assert all(set(item.source_chunk_ids) <= {"chunk-1"} for item in bundle.items)


def test_generation_validation_rejects_unavailable_source_reference() -> None:
    context = _context()
    bundle = deterministic_generation(context, BlueprintRegistry.default())
    invalid = bundle.model_copy(
        update={"items": [bundle.items[0].model_copy(update={"source_chunk_ids": ["forged"]})]}
    )

    with pytest.raises(AssessmentDomainError, match="source") as error:
        validate_generation_bundle(context, invalid)

    assert error.value.code == "assessment.invalid_source_reference"
