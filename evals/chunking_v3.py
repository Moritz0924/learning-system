from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


class ChunkingVariant:
    A = "A"
    P = "P"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


VARIANTS = (ChunkingVariant.A, ChunkingVariant.P, ChunkingVariant.B, ChunkingVariant.C, ChunkingVariant.D, ChunkingVariant.E)


@dataclass(frozen=True)
class EvidenceAnchor:
    anchor_id: str
    document_id: str
    page_or_slide: int | None
    normalized_text: str
    normalized_text_sha256: str
    char_start: int | None
    char_end: int | None
    source_locator: str

    @classmethod
    def create(
        cls,
        *,
        anchor_id: str,
        document_id: str,
        text: str,
        page_or_slide: int | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        source_locator: str,
    ) -> "EvidenceAnchor":
        normalized = normalize_evidence_text(text)
        return cls(
            anchor_id=anchor_id,
            document_id=document_id,
            page_or_slide=page_or_slide,
            normalized_text=normalized,
            normalized_text_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            char_start=char_start,
            char_end=char_end,
            source_locator=source_locator,
        )


@dataclass(frozen=True)
class ChunkingDocument:
    document_id: str
    filename: str
    split: str
    source_type: str
    source_sha256: str


@dataclass(frozen=True)
class ChunkingQuery:
    query_id: str
    document_id: str
    split: str
    query: str
    gold_evidence_anchors: tuple[str, ...]


@dataclass(frozen=True)
class ChunkingDataset:
    dataset_version: str
    documents: tuple[ChunkingDocument, ...]
    queries: tuple[ChunkingQuery, ...]
    anchors: tuple[EvidenceAnchor, ...]
    topic_boundaries: Mapping[str, tuple[tuple[str, str], ...]]
    dataset_hash: str
    gold_hash: str


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    content: str
    token_count: int
    covered_anchor_ids: tuple[str, ...]


def normalize_evidence_text(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())


def map_chunk_to_anchors(
    *,
    document_id: str,
    content: str,
    metadata: Mapping[str, object],
    anchors: Sequence[EvidenceAnchor],
) -> tuple[str, ...]:
    normalized_chunk = normalize_evidence_text(content)
    covered: list[str] = []
    for anchor in anchors:
        if anchor.document_id != document_id:
            continue
        if anchor.normalized_text and anchor.normalized_text in normalized_chunk:
            covered.append(anchor.anchor_id)
            continue
        for span in metadata.get("source_spans", ()) or ():
            if not isinstance(span, Mapping):
                continue
            if (
                span.get("page", span.get("page_start")) == anchor.page_or_slide
                and _spans_overlap(
                    span.get("char_start"), span.get("char_end"),
                    anchor.char_start, anchor.char_end,
                )
            ):
                covered.append(anchor.anchor_id)
                break
    return tuple(sorted(set(covered)))


def score_ranked_chunks(
    *,
    query: ChunkingQuery,
    ranked: Sequence[RetrievedChunk],
    anchors_by_id: Mapping[str, EvidenceAnchor],
    cutoffs: Sequence[int] = (1, 3, 5, 10),
    token_budgets: Sequence[int] = (512, 1024, 2048),
) -> dict[str, object]:
    gold = set(query.gold_evidence_anchors)
    fixed_k: dict[str, dict[str, float]] = {}
    for cutoff in cutoffs:
        selected = list(ranked[:cutoff])
        fixed_k[str(cutoff)] = _retrieval_metrics(selected, gold, anchors_by_id)
    budget_metrics: dict[str, dict[str, float]] = {}
    for budget in token_budgets:
        selected: list[RetrievedChunk] = []
        used = 0
        for chunk in ranked:
            if selected and used + chunk.token_count > budget:
                break
            selected.append(chunk)
            used += chunk.token_count
            if used >= budget:
                break
        budget_metrics[str(budget)] = _retrieval_metrics(selected, gold, anchors_by_id)
    return {"fixed_k": fixed_k, "fixed_token_budget": budget_metrics}


def _retrieval_metrics(
    selected: Sequence[RetrievedChunk],
    gold: set[str],
    anchors_by_id: Mapping[str, EvidenceAnchor],
) -> dict[str, float]:
    covered: set[str] = set()
    first_rank: int | None = None
    total_tokens = 0
    evidence_tokens = 0
    for rank, chunk in enumerate(selected, start=1):
        total_tokens += max(0, chunk.token_count)
        matched = set(chunk.covered_anchor_ids) & gold
        if matched and first_rank is None:
            first_rank = rank
        covered.update(matched)
        evidence_tokens += sum(len(anchors_by_id[anchor_id].normalized_text.split()) for anchor_id in matched)
    recall = len(covered) / len(gold) if gold else 0.0
    dcg = sum(
        1.0 / math.log2(rank + 2)
        for rank, chunk in enumerate(selected)
        if set(chunk.covered_anchor_ids) & gold
    )
    ideal_count = min(len(gold), len(selected))
    idcg = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_count))
    return {
        "evidence_recall": recall,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "ndcg": dcg / idcg if idcg else 0.0,
        "context_density": evidence_tokens / total_tokens if total_tokens else 0.0,
        "retrieved_tokens": float(total_tokens),
    }


def paired_bootstrap(
    deltas: Sequence[float],
    *,
    resamples: int = 1000,
    seed: int = 0,
) -> dict[str, float | int]:
    if not deltas:
        return {"mean_delta": 0.0, "ci95_low": 0.0, "ci95_high": 0.0, "resamples": resamples}
    rng = random.Random(seed)
    values = list(deltas)
    means = [sum(rng.choice(values) for _ in values) / len(values) for _ in range(resamples)]
    means.sort()
    low_index = max(0, int(0.025 * resamples) - 1)
    high_index = min(len(means) - 1, int(0.975 * resamples))
    return {
        "mean_delta": sum(values) / len(values),
        "ci95_low": means[low_index],
        "ci95_high": means[high_index],
        "resamples": resamples,
    }


def validate_document_split(documents: Sequence[ChunkingDocument], queries: Sequence[ChunkingQuery]) -> list[str]:
    errors: list[str] = []
    split_by_document = {document.document_id: document.split for document in documents}
    if len(documents) != len(split_by_document):
        errors.append("document ids must be unique")
    for query in queries:
        document_split = split_by_document.get(query.document_id)
        if document_split is None:
            errors.append(f"query {query.query_id} references unknown document")
        elif document_split != query.split:
            errors.append(f"query {query.query_id} crosses document split")
    return errors


def _spans_overlap(
    left_start: object,
    left_end: object,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    if not all(isinstance(value, int) for value in (left_start, left_end, right_start, right_end)):
        return False
    return max(left_start, right_start) < min(left_end, right_end)


def canonical_dataset_hash(documents: Sequence[ChunkingDocument], queries: Sequence[ChunkingQuery]) -> str:
    payload = {
        "documents": [document.__dict__ for document in documents],
        "queries": [query.__dict__ for query in queries],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def canonical_gold_hash(anchors: Sequence[EvidenceAnchor], boundaries: Mapping[str, Sequence[tuple[str, str]]]) -> str:
    payload = {
        "anchors": [anchor.__dict__ for anchor in anchors],
        "topic_boundaries": {key: sorted(map(list, values)) for key, values in sorted(boundaries.items())},
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
