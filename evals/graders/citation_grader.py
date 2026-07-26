"""Citation chain validity and explicitly separate semantic support grading."""
from __future__ import annotations

from adaptive_tutor.phase2.schemas import RetrievedChunk
from evals.models import (
    CitationReferenceGrade,
    CitationSemanticGrade,
    CitationValidityDetail,
    JudgeVerdict,
)


def grade_citation_references(
    citations: list[dict[str, str]],
    *,
    retrieved_chunks: list[RetrievedChunk],
    corpus_document_ids: set[str],
    require_citations: bool,
) -> CitationReferenceGrade:
    retrieved = {chunk.chunk_id: chunk for chunk in retrieved_chunks}
    seen: set[tuple[str, str]] = set()
    details: list[CitationValidityDetail] = []
    for citation in citations:
        chunk_id = citation.get("chunk_id", "")
        document_id = citation.get("document_id", "")
        pair = (chunk_id, document_id)
        reason = "valid"
        valid = True
        chunk = retrieved.get(chunk_id)
        if pair in seen:
            valid, reason = False, "duplicate_reference"
        elif chunk is None:
            valid, reason = False, "chunk_not_retrieved"
        elif document_id not in corpus_document_ids:
            valid, reason = False, "document_outside_evaluation_corpus"
        elif chunk.document_id != document_id:
            valid, reason = False, "document_id_mismatch"
        elif chunk.metadata.get("contains_prompt_injection") is True:
            valid, reason = False, "prompt_injection_evidence"
        seen.add(pair)
        details.append(
            CitationValidityDetail(
                cited_chunk_id=chunk_id,
                cited_document_id=document_id,
                valid=valid,
                reason=reason,
            )
        )
    valid_count = sum(detail.valid for detail in details)
    total = len(details)
    return CitationReferenceGrade(
        valid_reference_count=valid_count,
        invalid_reference_count=total - valid_count,
        total_citation_count=total,
        citation_reference_validity_rate=(valid_count / total if total else None),
        details=details,
    )


def ungraded_semantic_citations() -> CitationSemanticGrade:
    return CitationSemanticGrade(
        citation_support_rate=None,
        semantically_graded_count=0,
        semantic_grade_status="not_graded",
    )


def semantic_citation_grade(
    verdict: JudgeVerdict | None,
    *,
    citation_count: int = 0,
    judge_error: bool = False,
) -> CitationSemanticGrade:
    if judge_error:
        return CitationSemanticGrade(
            citation_support_rate=None,
            semantically_graded_count=0,
            semantic_grade_status="judge_error",
        )
    if verdict is None or citation_count == 0:
        return ungraded_semantic_citations()
    if verdict.citation_support_by_index:
        if len(verdict.citation_support_by_index) != citation_count:
            return ungraded_semantic_citations()
        return CitationSemanticGrade(
            citation_support_rate=sum(verdict.citation_support_by_index) / citation_count,
            semantically_graded_count=citation_count,
            semantic_grade_status="judge_graded",
        )
    if verdict.citation_supported is None or citation_count != 1:
        return ungraded_semantic_citations()
    return CitationSemanticGrade(
        citation_support_rate=float(verdict.citation_supported),
        semantically_graded_count=1,
        semantic_grade_status="judge_graded",
    )
