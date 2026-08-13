from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from backend.app.domain.rag.chunking.v3.ports import TokenCounterPort
from backend.app.services.token_counting import TiktokenTokenCounter


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
    language: str = "en"
    template_family: str | None = None


@dataclass(frozen=True)
class ChunkingQuery:
    query_id: str
    document_id: str
    split: str
    query: str
    gold_evidence_anchors: tuple[str, ...]
    query_type: str = "single_evidence"


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
    token_counter: TokenCounterPort | None = None,
) -> dict[str, object]:
    counter = token_counter or TiktokenTokenCounter()
    gold = set(query.gold_evidence_anchors)
    fixed_k: dict[str, dict[str, float]] = {}
    for cutoff in cutoffs:
        selected = list(ranked[:cutoff])
        fixed_k[str(cutoff)] = _retrieval_metrics(selected, gold, anchors_by_id, token_counter=counter)
    budget_metrics: dict[str, dict[str, float]] = {}
    for budget in token_budgets:
        selected: list[RetrievedChunk] = []
        used = 0
        for chunk in ranked:
            if used + chunk.token_count > budget:
                break
            selected.append(chunk)
            used += chunk.token_count
        budget_metrics[str(budget)] = _retrieval_metrics(
            selected,
            gold,
            anchors_by_id,
            token_counter=counter,
        )
    return {"fixed_k": fixed_k, "fixed_token_budget": budget_metrics}


def _retrieval_metrics(
    selected: Sequence[RetrievedChunk],
    gold: set[str],
    anchors_by_id: Mapping[str, EvidenceAnchor],
    *,
    token_counter: TokenCounterPort,
) -> dict[str, float]:
    covered: set[str] = set()
    first_rank: int | None = None
    total_tokens = 0
    evidence_tokens = 0
    for rank, chunk in enumerate(selected, start=1):
        total_tokens += max(0, chunk.token_count)
        matched = set(chunk.covered_anchor_ids) & gold
        newly_covered = matched - covered
        if matched and first_rank is None:
            first_rank = rank
        covered.update(newly_covered)
        evidence_tokens += sum(
            token_counter.count(anchors_by_id[anchor_id].normalized_text)
            for anchor_id in newly_covered
        )
    recall = len(covered) / len(gold) if gold else 0.0
    first_hit_ranks: dict[str, int] = {}
    for rank, chunk in enumerate(selected, start=1):
        for anchor_id in set(chunk.covered_anchor_ids) & gold:
            first_hit_ranks.setdefault(anchor_id, rank)
    dcg = sum(1.0 / math.log2(rank + 1) for rank in first_hit_ranks.values())
    ideal_count = min(len(gold), len(selected))
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    evidence_ndcg = dcg / idcg if idcg else 0.0
    return {
        "hit": 1.0 if covered else 0.0,
        "evidence_recall": recall,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "evidence_ndcg": evidence_ndcg,
        "ndcg": evidence_ndcg,
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


DEFAULT_TEMPLATE_LEAKAGE_THRESHOLD = 0.82


def validate_template_leakage(
    documents: Sequence[ChunkingDocument],
    sources: Mapping[str, str],
    *,
    threshold: float = DEFAULT_TEMPLATE_LEAKAGE_THRESHOLD,
) -> list[str]:
    """Reject exact and calibrated lexical Dev/Test template leakage."""
    if not 0.0 < threshold <= 1.0:
        raise ValueError("template leakage threshold must be in (0, 1]")
    errors: list[str] = []
    documents_by_id = {document.document_id: document for document in documents}
    if set(documents_by_id) != set(sources):
        errors.append("source document ids must exactly match dataset documents")
        return errors
    family_splits: dict[str, set[str]] = {}
    document_fingerprints: dict[str, list[str]] = {}
    paragraph_fingerprints: dict[str, set[str]] = {}
    normalized_sources: dict[str, str] = {}
    for document in documents:
        normalized = normalize_evidence_text(sources[document.document_id])
        normalized_sources[document.document_id] = normalized
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        document_fingerprints.setdefault(fingerprint, []).append(document.document_id)
        if document.template_family:
            family_splits.setdefault(document.template_family, set()).add(document.split)
        for paragraph in _normalized_paragraphs(sources[document.document_id]):
            fingerprint = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
            paragraph_fingerprints.setdefault(fingerprint, set()).add(document.document_id)
    for family, splits in sorted(family_splits.items()):
        if len(splits) > 1:
            errors.append(f"template family crosses Dev/Test split: {family}")
    for ids in document_fingerprints.values():
        splits = {documents_by_id[document_id].split for document_id in ids}
        if len(splits) > 1:
            errors.append(f"normalized document fingerprint crosses Dev/Test split: {sorted(ids)}")
    for ids in paragraph_fingerprints.values():
        splits = {documents_by_id[document_id].split for document_id in ids}
        if len(splits) > 1:
            errors.append(f"paragraph fingerprint crosses Dev/Test split: {sorted(ids)}")
    development = [item for item in documents if item.split == "development"]
    test = [item for item in documents if item.split == "test"]
    for left in development:
        for right in test:
            similarity = _lexical_similarity(
                normalized_sources[left.document_id],
                normalized_sources[right.document_id],
            )
            if similarity >= threshold:
                errors.append(
                    "cross-split lexical similarity "
                    f"{similarity:.3f} exceeds calibrated threshold {threshold:.3f}: "
                    f"{left.document_id} vs {right.document_id}"
                )
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


def _normalized_paragraphs(text: str) -> tuple[str, ...]:
    return tuple(
        normalized
        for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
        if (normalized := normalize_evidence_text(paragraph))
    )


def _lexical_similarity(left: str, right: str) -> float:
    left_tokens = set(left.casefold().split())
    right_tokens = set(right.casefold().split())
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


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
