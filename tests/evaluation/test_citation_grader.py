from __future__ import annotations

from adaptive_tutor.phase2.schemas import RetrievedChunk


def _chunk(chunk_id: str = "c1", document_id: str = "d1", **metadata) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        content="evidence",
        citation_label="source",
        trusted_level=3,
        metadata=metadata,
    )


def test_reference_grader_validates_retrieval_document_and_corpus_chain() -> None:
    from evals.graders.citation_grader import grade_citation_references

    grade = grade_citation_references(
        [{"chunk_id": "c1", "document_id": "d1"}],
        retrieved_chunks=[_chunk()],
        corpus_document_ids={"d1"},
        require_citations=True,
    )
    assert grade.citation_reference_validity_rate == 1.0
    assert grade.valid_reference_count == 1


def test_reference_grader_rejects_forged_mismatched_duplicate_and_injection_citations() -> None:
    from evals.graders.citation_grader import grade_citation_references

    citations = [
        {"chunk_id": "missing", "document_id": "d1"},
        {"chunk_id": "c1", "document_id": "wrong"},
        {"chunk_id": "c1", "document_id": "d1"},
        {"chunk_id": "c1", "document_id": "d1"},
        {"chunk_id": "inject", "document_id": "d2"},
    ]
    grade = grade_citation_references(
        citations,
        retrieved_chunks=[_chunk(), _chunk("inject", "d2", contains_prompt_injection=True)],
        corpus_document_ids={"d1", "d2"},
        require_citations=True,
    )
    assert grade.valid_reference_count == 1
    assert grade.invalid_reference_count == 4
    assert grade.citation_reference_validity_rate == 0.2


def test_missing_required_citation_is_not_treated_as_semantic_failure() -> None:
    from evals.graders.citation_grader import grade_citation_references, ungraded_semantic_citations

    references = grade_citation_references([], retrieved_chunks=[_chunk()], corpus_document_ids={"d1"}, require_citations=True)
    semantic = ungraded_semantic_citations()

    assert references.citation_reference_validity_rate is None
    assert references.invalid_reference_count == 0
    assert semantic.citation_support_rate is None
    assert semantic.semantic_grade_status == "not_graded"


def test_semantic_citation_denominator_counts_graded_citations() -> None:
    from evals.graders.citation_grader import semantic_citation_grade
    from evals.models import JudgeVerdict

    grade = semantic_citation_grade(
        JudgeVerdict(
            citation_supported=False,
            citation_support_by_index=[True, False, True],
            reason="two supported",
        ),
        citation_count=3,
    )

    assert grade.citation_support_rate == 2 / 3
    assert grade.semantically_graded_count == 3
