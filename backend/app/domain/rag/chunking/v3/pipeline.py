from __future__ import annotations

from hashlib import sha256
from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlock, DocumentBlockType

from .config import HybridChunkPolicy
from .domain import ChunkCandidate, SemanticSegment, StructuralRegion
from .semantic import SemanticChunker
from .size_guard import SizeGuard
from .structure import StructureAwareChunker


class HybridChunkingPipeline:
    def __init__(
        self,
        *,
        structure_chunker: StructureAwareChunker,
        semantic_chunker: SemanticChunker,
        size_guard: SizeGuard,
        policy: HybridChunkPolicy,
    ) -> None:
        self.structure_chunker = structure_chunker
        self.semantic_chunker = semantic_chunker
        self.size_guard = size_guard
        self.policy = policy

    def chunk(self, blocks: Sequence[DocumentBlock], *, document_id: str) -> list[ChunkCandidate]:
        regions = self.structure_chunker.build_regions(blocks)
        candidates: list[ChunkCandidate] = []
        for region in regions:
            segments = self.semantic_chunker.split(region)
            if region.region_type in {"code", "table"}:
                segments = [SemanticSegment(units=segment.units, boundaries=segment.boundaries) for segment in segments]
            for candidate in self.size_guard.apply(segments):
                candidates.append(_with_metadata(candidate, region=region, policy=self.policy, document_id=document_id))
        return candidates


def _with_metadata(candidate: ChunkCandidate, *, region: StructuralRegion, policy: HybridChunkPolicy, document_id: str) -> ChunkCandidate:
    source_units = [unit for unit in region.units if unit.unit_id in set(candidate.source_unit_ids)]
    raw = {unit.unit_id: unit.metadata for unit in source_units}
    source_spans = [
        {
            "source_locator": f"{document_id}:page:{unit.page_number}:block:{unit.block_index}",
            "page": unit.page_number,
            "block_index": unit.block_index,
            "char_start": raw[unit.unit_id].get("source_char_start"),
            "char_end": raw[unit.unit_id].get("source_char_end"),
        }
        for unit in source_units
    ]
    boundaries = []
    for boundary in ():
        boundaries.append({
            "left_unit_id": boundary.left_unit_id,
            "right_unit_id": boundary.right_unit_id,
            "local_similarity": boundary.local_similarity,
            "adjacent_similarity": boundary.adjacent_similarity,
            "continuation_score": boundary.continuation_score,
            "continuation_reasons": list(boundary.continuation_reasons),
            "boundary_score": boundary.boundary_score,
            "adaptive_threshold": boundary.adaptive_threshold,
            "selected": boundary.selected,
        })
    metadata = {
        **dict(candidate.metadata),
        "chunk_schema_version": "v3",
        "chunking_strategy": "hybrid_structure_semantic_size_v3",
        "chunking_policy_version": policy.policy_version,
        "heading_path": list(candidate.heading_path),
        "page_start": candidate.page_start,
        "page_end": candidate.page_end,
        "source_block_indexes": [unit.block_index for unit in source_units],
        "source_spans": source_spans,
        "source_unit_ids": list(candidate.source_unit_ids),
        "structure": {
            "region_id": region.region_id,
            "boundary_before": region.boundary_before.value,
            "heading_prefix_in_content": bool(candidate.heading_path and region.region_type == "text"),
        },
        "semantic": {
            "window_size": policy.semantic.window_size,
            "boundaries": boundaries,
        },
        "content_hash": sha256(candidate.content.encode("utf-8")).hexdigest(),
    }
    return candidate.__class__(
        content=candidate.content,
        chunk_type=candidate.chunk_type,
        heading_path=candidate.heading_path,
        source_unit_ids=candidate.source_unit_ids,
        page_start=candidate.page_start,
        page_end=candidate.page_end,
        metadata=metadata,
    )
