"""Runtime factual evidence contracts shared by Teacher and Grounding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .t3_contracts import content_hash

if TYPE_CHECKING:
    from adaptive_tutor.phase2.schemas import RetrievedChunk


EvidenceSourceType = Literal["rag", "tool"]


class EvidenceInvariantError(RuntimeError):
    """Raised when runtime evidence provenance becomes internally inconsistent."""


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=512)
    source_type: EvidenceSourceType
    content: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    citation_label: str = Field(min_length=1)
    source_title: str | None = None
    source_url: str | None = None
    trusted_level: int = Field(ge=0, le=5)
    document_id: str | None = None
    chunk_id: str | None = None
    tool_name: str | None = None
    tool_call_fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provenance(self) -> EvidenceItem:
        if self.source_type == "rag":
            if not self.document_id or not self.chunk_id:
                raise ValueError("RAG evidence requires document_id and chunk_id")
            if self.tool_name is not None or self.tool_call_fingerprint is not None:
                raise ValueError("RAG evidence cannot contain tool provenance")
        else:
            if not self.tool_name:
                raise ValueError("Tool evidence requires tool_name")
            if self.document_id is not None or self.chunk_id is not None:
                raise ValueError("Tool evidence cannot contain RAG provenance")
        return self


def rag_evidence_id(*, document_id: str, chunk_id: str) -> str:
    return f"rag:{document_id}:{chunk_id}"


def tool_evidence_id(*, tool_name: str, source_url: str, content_hash: str) -> str:
    digest = sha256(f"{source_url}\n{content_hash}".encode("utf-8")).hexdigest()[:24]
    return f"tool:{tool_name}:{digest}"


def evidence_from_retrieved_chunk(chunk: RetrievedChunk) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=rag_evidence_id(document_id=chunk.document_id, chunk_id=chunk.chunk_id),
        source_type="rag",
        content=chunk.content,
        content_hash=content_hash(chunk.content),
        citation_label=chunk.citation_label,
        source_title=chunk.source_title,
        source_url=chunk.source_url,
        trusted_level=chunk.trusted_level,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        metadata=_copy_metadata(chunk.metadata),
    )


def _copy_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {deepcopy(key): _copy_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_metadata(item) for item in value)
    return deepcopy(value)


def merge_evidence_items(
    existing: list[EvidenceItem],
    incoming: list[EvidenceItem] | tuple[EvidenceItem, ...],
) -> list[EvidenceItem]:
    merged = list(existing)
    by_id = {item.evidence_id: item for item in merged}
    for item in incoming:
        previous = by_id.get(item.evidence_id)
        if previous is None:
            merged.append(item)
            by_id[item.evidence_id] = item
        elif previous.content_hash != item.content_hash:
            raise EvidenceInvariantError(f"evidence hash conflict: {item.evidence_id}")
    return merged


class EvidenceSelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int = Field(default=20, ge=1, le=100)
    max_total_chars: int = Field(default=32_000, ge=1000)


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvidenceItem, ...]
    skipped_by_item_budget: int = Field(default=0, ge=0)
    skipped_by_char_budget: int = Field(default=0, ge=0)


def select_evidence_items(
    evidence: list[EvidenceItem],
    *,
    policy: EvidenceSelectionPolicy | None = None,
) -> EvidenceSelection:
    policy = policy or EvidenceSelectionPolicy()
    selected: list[EvidenceItem] = []
    total_chars = 0
    skipped_by_item_budget = 0
    skipped_by_char_budget = 0
    for index, item in enumerate(evidence):
        if len(selected) >= policy.max_items:
            skipped_by_item_budget = len(evidence) - index
            break
        if total_chars + len(item.content) > policy.max_total_chars:
            skipped_by_char_budget += 1
            continue
        selected.append(item)
        total_chars += len(item.content)
    return EvidenceSelection(
        items=tuple(selected),
        skipped_by_item_budget=skipped_by_item_budget,
        skipped_by_char_budget=skipped_by_char_budget,
    )


class EvidenceSnapshotItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    content_hash: str
    source_type: EvidenceSourceType


class EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    run_id: str
    retrieval_run_id: str
    selected_context: tuple[EvidenceSnapshotItem, ...]


def build_evidence_snapshot(
    *,
    run_id: str,
    retrieval_run_id: str,
    evidence: list[EvidenceItem] | tuple[EvidenceItem, ...],
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=f"snapshot-{uuid4()}",
        run_id=run_id,
        retrieval_run_id=retrieval_run_id,
        selected_context=tuple(
            EvidenceSnapshotItem(
                evidence_id=item.evidence_id,
                content_hash=item.content_hash,
                source_type=item.source_type,
            )
            for item in evidence
        ),
    )


def evidence_to_llm_context(
    evidence: Sequence[EvidenceItem],
) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": item.evidence_id,
            "source_type": item.source_type,
            "citation_label": item.citation_label,
            "source_title": item.source_title,
            "source_url": item.source_url,
            "trusted_level": item.trusted_level,
            "content": item.content,
        }
        for item in evidence
    ]
