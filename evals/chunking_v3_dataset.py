from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .chunking_v3 import (
    ChunkingDataset,
    ChunkingDocument,
    ChunkingQuery,
    EvidenceAnchor,
    canonical_dataset_hash,
    canonical_gold_hash,
)


@dataclass(frozen=True)
class FixtureBundle:
    dataset: ChunkingDataset
    sources: dict[str, str]


def build_fixture_bundle() -> FixtureBundle:
    documents: list[ChunkingDocument] = []
    queries: list[ChunkingQuery] = []
    anchors: list[EvidenceAnchor] = []
    sources: dict[str, str] = {}
    source_types = (
        ["markdown"] * 10
        + ["pdf"] * 10
        + ["pptx"] * 5
        + ["text"] * 5
    )
    for number, source_type in enumerate(source_types, start=1):
        document_id = f"hybrid-v3-doc-{number:03d}"
        split = "development" if number <= 20 else "test"
        extension = {"markdown": "md", "pdf": "pdf", "pptx": "pptx", "text": "txt"}[source_type]
        filename = f"{document_id}.{extension}"
        topic = _topic(number)
        evidence = f"Canonical evidence {number}: {topic}."
        source = (
            f"# Lesson {number}\n\n"
            f"{evidence}\n\n"
            f"This neighboring explanation describes the same topic with additional context.\n\n"
            f"A separate topic discusses evaluation boundaries and should remain distinguishable."
        )
        sources[document_id] = source
        source_hash = hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        documents.append(ChunkingDocument(document_id, filename, split, source_type, source_hash))
        anchor_id = f"anchor-{number:03d}"
        anchors.append(EvidenceAnchor.create(
            anchor_id=anchor_id,
            document_id=document_id,
            text=evidence,
            source_locator=f"{filename}:canonical:1",
            page_or_slide=1,
            char_start=0,
            char_end=len(evidence),
        ))
        queries.append(ChunkingQuery(
            query_id=f"q-{number:03d}",
            document_id=document_id,
            split=split,
            query=f"What is the canonical evidence for {topic}?",
            gold_evidence_anchors=(anchor_id,),
        ))
    dataset_hash = canonical_dataset_hash(documents, queries)
    gold_hash = canonical_gold_hash(anchors, {})
    dataset = ChunkingDataset(
        dataset_version="chunking-v3-v1",
        documents=tuple(documents),
        queries=tuple(queries),
        anchors=tuple(anchors),
        topic_boundaries={},
        dataset_hash=dataset_hash,
        gold_hash=gold_hash,
    )
    return FixtureBundle(dataset=dataset, sources=sources)


def _topic(number: int) -> str:
    topics = (
        "retrieval grounding", "semantic similarity", "document structure",
        "token budgets", "table preservation", "code boundaries",
        "citation provenance", "batch embedding", "adaptive thresholds",
        "adjacent continuation",
    )
    return topics[(number - 1) % len(topics)]

