from __future__ import annotations

from hashlib import sha256
from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlock, DocumentBlockType

from .config import HybridChunkPolicy
from .domain import (
    ChunkCandidate,
    HybridChunkingDiagnostics,
    HybridChunkingResult,
    SemanticSegment,
    StructuralRegion,
)
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

    def chunk(self, blocks: Sequence[DocumentBlock], *, document_id: str) -> HybridChunkingResult:
        regions = self.structure_chunker.build_regions(blocks)
        candidates: list[ChunkCandidate] = []
        semantic_regions = 0
        adaptive_threshold_regions = 0
        candidate_boundaries = 0
        selected_boundaries = 0
        relation_adjusted_boundaries = 0
        for region in regions:
            segmentation = self.semantic_chunker.split_with_trace(region)
            segments = list(segmentation.segments)
            if region.region_type == "text":
                semantic_regions += 1
            trace = segmentation.trace.boundaries
            candidate_boundaries += len(trace)
            selected_boundaries += sum(boundary.selected for boundary in trace)
            relation_adjusted_boundaries += sum(boundary.continuation_score > 0 for boundary in trace)
            adaptive_threshold_regions += int(any(
                boundary.adaptive_threshold is not None for boundary in trace
            ))
            if region.region_type in {"code", "table"}:
                segments = [SemanticSegment(
                    units=segment.units,
                    boundary_before=segment.boundary_before,
                    boundary_after=segment.boundary_after,
                ) for segment in segments]
            for candidate in self.size_guard.apply(segments):
                candidates.append(_with_metadata(candidate, region=region, policy=self.policy, document_id=document_id))
        return HybridChunkingResult(
            chunks=tuple(candidates),
            diagnostics=HybridChunkingDiagnostics(
                semantic_regions=semantic_regions,
                adaptive_threshold_regions=adaptive_threshold_regions,
                candidate_boundaries=candidate_boundaries,
                selected_boundaries=selected_boundaries,
                relation_adjusted_boundaries=relation_adjusted_boundaries,
                tiny_merges=sum(
                    candidate.metadata.get("size_guard", {}).get("action") == "tiny_merge"
                    for candidate in candidates
                ),
                hard_fallbacks=sum(
                    candidate.metadata.get("size_guard", {}).get("action") == "hard_fallback"
                    for candidate in candidates
                ),
            ),
        )


def _with_metadata(candidate: ChunkCandidate, *, region: StructuralRegion, policy: HybridChunkPolicy, document_id: str) -> ChunkCandidate:
    source_units = [unit for unit in region.units if unit.unit_id in set(candidate.source_unit_ids)]
    raw = {unit.unit_id: unit.metadata for unit in source_units}
    provenance_values = {
        key: sorted({str(raw[unit.unit_id][key]) for unit in source_units if raw[unit.unit_id].get(key) is not None})
        for key in ("file_type", "processing_mode", "source_element", "source_format")
    }
    location_kind = _source_location_kind(source_units)
    source_spans = [
        {
            "source_locator": _source_locator(
                document_id=document_id,
                location_kind=location_kind,
                page_number=unit.page_number,
                block_index=unit.block_index,
            ),
            "page": unit.page_number if location_kind in {"page", "slide"} else None,
            "block_index": unit.block_index,
            "char_start": raw[unit.unit_id].get("source_char_start"),
            "char_end": raw[unit.unit_id].get("source_char_end"),
        }
        for unit in source_units
    ]
    boundaries = []
    for boundary in candidate.boundaries:
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
        "page_start": candidate.page_start if location_kind in {"page", "slide"} else None,
        "page_end": candidate.page_end if location_kind in {"page", "slide"} else None,
        "source_block_indexes": [unit.block_index for unit in source_units],
        "source_spans": source_spans,
        "source_unit_ids": list(candidate.source_unit_ids),
        "source_provenance": provenance_values,
        "file_type": _single_provenance_value(provenance_values["file_type"]),
        "processing_mode": _single_provenance_value(provenance_values["processing_mode"]),
        "source_element": _single_provenance_value(provenance_values["source_element"]),
        "source_format": _single_provenance_value(provenance_values["source_format"]),
        "source_location_kind": location_kind,
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
        boundaries=candidate.boundaries,
    )


def _source_location_kind(units: Sequence) -> str | None:
    file_types = {str(unit.metadata.get("file_type", "")) for unit in units}
    if len(file_types) != 1:
        return None
    return {
        "pdf": "page",
        "pptx": "slide",
        "image": "image",
        "text": "text",
    }.get(file_types.pop())


def _single_provenance_value(values: Sequence[str]) -> str | None:
    return values[0] if len(values) == 1 else None


def _source_locator(
    *,
    document_id: str,
    location_kind: str | None,
    page_number: int,
    block_index: int,
) -> str:
    if location_kind in {"page", "slide"}:
        return f"{document_id}:{location_kind}:{page_number}:block:{block_index}"
    if location_kind in {"image", "text"}:
        return f"{document_id}:{location_kind}:block:{block_index}"
    return f"{document_id}:block:{block_index}"
