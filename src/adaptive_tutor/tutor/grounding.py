"""Structured-answer parsing and deterministic citation validation."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from adaptive_tutor.phase2.schemas import RetrievedChunk

from .t3_contracts import (
    GroundedAnswerDraft,
    GroundedClaim,
    GroundingStatus,
    PublicCitation,
    RetrievalEvidenceItem,
    RetrievalEvidenceSnapshot,
    content_hash,
)


class StructuredAnswerParser:
    def parse(self, raw: str) -> GroundedAnswerDraft:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("structured answer must be a JSON object")
        return GroundedAnswerDraft.model_validate(value)


@dataclass(frozen=True)
class GroundingEvaluation:
    status: GroundingStatus
    draft: GroundedAnswerDraft | None
    public_citations: list[PublicCitation]
    referenced_chunks: list[RetrievedChunk]
    repair_count: int
    invalid_citation_ids: list[str]


def build_retrieval_snapshot(
    *,
    run_id: str,
    retrieval_run_id: str,
    chunks: Sequence[RetrievedChunk],
) -> RetrievalEvidenceSnapshot:
    versions = sorted(
        {
            str(chunk.metadata.get("index_version_id"))
            for chunk in chunks
            if chunk.metadata.get("index_version_id")
        }
    )
    index_version = versions[0] if len(versions) == 1 else ",".join(versions) or "legacy"
    return RetrievalEvidenceSnapshot(
        snapshot_id=f"snapshot-{uuid4()}",
        run_id=run_id,
        retrieval_run_id=retrieval_run_id,
        index_version=index_version,
        selected_context=tuple(
            RetrievalEvidenceItem(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content_hash=content_hash(chunk.content),
            )
            for chunk in chunks
        ),
    )


class GroundingPipeline:
    def __init__(self, parser: StructuredAnswerParser | None = None):
        self.parser = parser or StructuredAnswerParser()

    def evaluate(
        self,
        *,
        raw: str,
        question: str,
        chunks: Sequence[RetrievedChunk],
        snapshot: RetrievalEvidenceSnapshot,
        repair: Callable[[str], str] | None = None,
        judge: Callable[[GroundedClaim, list[RetrievedChunk]], bool] | None = None,
    ) -> GroundingEvaluation:
        repair_count = 0
        try:
            draft = self.parser.parse(raw)
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
            if repair is None:
                return self._result(GroundingStatus.VALIDATION_ERROR, None, chunks, repair_count, [])
            repair_count = 1
            try:
                draft = self.parser.parse(repair(self._repair_prompt(question, raw, "structured_output")))
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
                return self._result(GroundingStatus.VALIDATION_ERROR, None, chunks, repair_count, [])

        invalid = self._invalid_citations(draft, chunks, snapshot)
        if invalid and repair is not None and repair_count == 0:
            repair_count = 1
            try:
                draft = self.parser.parse(repair(self._repair_prompt(question, raw, "citation_provenance")))
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
                return self._result(GroundingStatus.SAFE_REFUSAL, None, chunks, repair_count, invalid)
            invalid = self._invalid_citations(draft, chunks, snapshot)
        if invalid:
            return self._result(GroundingStatus.SAFE_REFUSAL, draft, chunks, repair_count, invalid)
        if draft.insufficient_evidence or not chunks:
            return self._result(GroundingStatus.INSUFFICIENT_EVIDENCE, draft, chunks, repair_count, [])
        if judge is None:
            return self._result(GroundingStatus.SEMANTIC_UNVERIFIED, draft, chunks, repair_count, [])
        cited = self._cited_chunks(draft, chunks)
        if any(not judge(claim, cited) for claim in draft.claims):
            return self._result(GroundingStatus.SAFE_REFUSAL, draft, chunks, repair_count, [])
        return self._result(GroundingStatus.SUPPORTED, draft, chunks, repair_count, [])

    def _result(
        self,
        status: GroundingStatus,
        draft: GroundedAnswerDraft | None,
        chunks: Sequence[RetrievedChunk],
        repair_count: int,
        invalid: list[str],
    ) -> GroundingEvaluation:
        if draft is None or status in {GroundingStatus.SAFE_REFUSAL, GroundingStatus.VALIDATION_ERROR}:
            return GroundingEvaluation(status, draft, [], [], repair_count, invalid)
        chunk_map = {(chunk.chunk_id, chunk.document_id): chunk for chunk in chunks}
        public: list[PublicCitation] = []
        referenced: list[RetrievedChunk] = []
        for index, ref in enumerate(draft.citations, start=1):
            chunk = chunk_map.get((ref.chunk_id, ref.document_id))
            if chunk is None:
                continue
            public.append(
                PublicCitation(
                    citation_id=f"c{index}",
                    title=chunk.source_title,
                    source_type=str(chunk.metadata.get("source_type", "unknown")),
                    excerpt=chunk.content[:500],
                    citation_label=chunk.citation_label,
                    source_title=chunk.source_title,
                    source_url=chunk.source_url,
                )
            )
            referenced.append(chunk)
        return GroundingEvaluation(status, draft, public, referenced, repair_count, invalid)

    @staticmethod
    def _invalid_citations(
        draft: GroundedAnswerDraft,
        chunks: Sequence[RetrievedChunk],
        snapshot: RetrievalEvidenceSnapshot,
    ) -> list[str]:
        allowed = {(item.chunk_id, item.document_id, item.content_hash) for item in snapshot.selected_context}
        actual = {
            (chunk.chunk_id, chunk.document_id): content_hash(chunk.content)
            for chunk in chunks
        }
        top_level = {(ref.chunk_id, ref.document_id) for ref in draft.citations}
        invalid: list[str] = []
        for ref in draft.citations:
            if (ref.chunk_id, ref.document_id) not in {(chunk_id, document_id) for chunk_id, document_id, _ in allowed}:
                invalid.append(ref.chunk_id)
            elif (ref.chunk_id, ref.document_id, actual.get((ref.chunk_id, ref.document_id))) not in allowed:
                invalid.append(ref.chunk_id)
        for claim in draft.claims:
            for ref in claim.citation_refs:
                if (ref.chunk_id, ref.document_id) not in top_level:
                    invalid.append(ref.chunk_id)
        return list(dict.fromkeys(invalid))

    @staticmethod
    def _cited_chunks(draft: GroundedAnswerDraft, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
        references = {(ref.chunk_id, ref.document_id) for claim in draft.claims for ref in claim.citation_refs}
        return [chunk for chunk in chunks if (chunk.chunk_id, chunk.document_id) in references]

    @staticmethod
    def _repair_prompt(question: str, raw: str, error_type: str) -> str:
        return (
            "Return only a complete JSON GroundedAnswerDraft. "
            f"Repair error: {error_type}. Question: {question}. "
            "Use only the citation IDs already present in the allowed evidence. "
            f"Original draft: {raw[:4000]}"
        )
