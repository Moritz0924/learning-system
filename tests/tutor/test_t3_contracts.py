from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_tutor.tutor.t3_contracts import (
    GroundingStatus,
    PublicCitation,
    RetrievalEvidenceItem,
    RetrievalEvidenceSnapshot,
    Thread3ErrorCode,
    canonical_json_hash,
    content_hash,
    normalize_chunk_text,
    validate_feature_flags,
)


def test_content_hash_normalizes_only_line_endings_and_trailing_whitespace() -> None:
    assert normalize_chunk_text("a\r\n b  \n") == "a\n b"
    assert content_hash("a\r\n b  \n") == content_hash("a\n b")


def test_snapshot_is_frozen_and_rejects_unknown_fields() -> None:
    snapshot = RetrievalEvidenceSnapshot(
        snapshot_id="snapshot-1",
        run_id="run-1",
        retrieval_run_id="retrieval-1",
        index_version="index-1",
        selected_context=(
            RetrievalEvidenceItem(
                chunk_id="chunk-1",
                document_id="doc-1",
                content_hash=content_hash("source"),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        RetrievalEvidenceSnapshot.model_validate({**snapshot.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        snapshot.index_version = "index-2"


def test_contract_enums_and_feature_dependency_are_stable() -> None:
    assert [item.value for item in GroundingStatus] == [
        "supported",
        "semantic_unverified",
        "repair_required",
        "insufficient_evidence",
        "safe_refusal",
        "validation_error",
    ]
    assert Thread3ErrorCode.UPSTREAM_TIMEOUT.value == "T3_UPSTREAM_TIMEOUT"
    with pytest.raises(ValueError, match="FEATURE_STRUCTURED_ANSWER_V2"):
        validate_feature_flags(
            {
                "FEATURE_STRUCTURED_ANSWER_V2": False,
                "FEATURE_GROUNDING_V2": True,
            }
        )


def test_public_citation_forbids_internal_fields() -> None:
    citation = PublicCitation(
        citation_id="c1",
        title="Guide",
        source_type="markdown",
        excerpt="verified excerpt",
    )
    assert citation.citation_id == "c1"
    with pytest.raises(ValidationError):
        PublicCitation.model_validate({**citation.model_dump(), "chunk_id": "internal"})


def test_canonical_json_hash_is_order_independent() -> None:
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash({"a": 1, "b": 2})
