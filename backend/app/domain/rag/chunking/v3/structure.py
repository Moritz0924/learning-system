from __future__ import annotations

from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlock, DocumentBlockType

from .domain import BoundaryStrength, StructuralRegion, StructuralUnit


class StructureAwareChunker:
    def build_regions(self, blocks: Sequence[DocumentBlock]) -> list[StructuralRegion]:
        heading_stack: list[str] = []
        regions: list[StructuralRegion] = []
        current_units: list[StructuralUnit] = []
        current_heading_path: tuple[str, ...] = ()
        current_type = "text"
        next_boundary = BoundaryStrength.SOFT

        def flush(after: BoundaryStrength) -> None:
            nonlocal current_units, current_heading_path, current_type, next_boundary
            if not current_units:
                return
            region_number = len(regions) + 1
            regions.append(StructuralRegion(
                region_id=f"region-{region_number}",
                heading_path=current_heading_path,
                units=tuple(current_units),
                region_type=current_type,
                boundary_before=next_boundary,
                boundary_after=after,
            ))
            current_units = []
            current_heading_path = ()
            current_type = "text"
            next_boundary = BoundaryStrength.SOFT

        for block in blocks:
            if block.block_type is DocumentBlockType.HEADING:
                level = block.heading_level or 1
                heading_stack[:] = heading_stack[: level - 1]
                heading_stack.append(block.text.strip())
                if current_units:
                    flush(BoundaryStrength.HARD)
                    next_boundary = BoundaryStrength.HARD
                continue

            block_type = _region_type(block.block_type)
            heading_path = tuple(heading_stack)
            if block_type in {"code", "table"}:
                flush(BoundaryStrength.HARD)
                next_boundary = BoundaryStrength.HARD
                unit = _unit(block, heading_path, len(regions) + 1, 1)
                regions.append(StructuralRegion(
                    region_id=f"region-{len(regions) + 1}",
                    heading_path=heading_path,
                    units=(unit,),
                    region_type=block_type,
                    boundary_before=next_boundary,
                    boundary_after=BoundaryStrength.HARD,
                ))
                next_boundary = BoundaryStrength.HARD
                continue

            if current_units and current_heading_path != heading_path:
                flush(BoundaryStrength.HARD)
                next_boundary = BoundaryStrength.HARD
            current_heading_path = heading_path
            current_type = block_type
            current_units.append(_unit(
                block, heading_path, len(regions) + 1, len(current_units) + 1,
            ))

        flush(BoundaryStrength.SOFT)
        return regions


def _region_type(block_type: DocumentBlockType) -> str:
    if block_type is DocumentBlockType.CODE:
        return "code"
    if block_type is DocumentBlockType.TABLE:
        return "table"
    return "text"


def _unit(block: DocumentBlock, heading_path: tuple[str, ...], region_number: int, unit_number: int) -> StructuralUnit:
    bbox = block.bbox
    return StructuralUnit(
        unit_id=f"unit-{region_number}-{unit_number}",
        text=block.text,
        block_type=block.block_type,
        page_number=block.page_number,
        block_index=block.block_index,
        heading_path=heading_path,
        bbox=(bbox.x0, bbox.y0, bbox.x1, bbox.y1) if bbox else None,
        reading_order=block.reading_order if block.reading_order is not None else block.block_index,
        structure_confidence=block.structure_confidence,
        metadata=block.model_dump(mode="json"),
    )


__all__ = ["StructureAwareChunker"]
