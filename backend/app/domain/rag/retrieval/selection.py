from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .domain import FusedCandidate


@dataclass(frozen=True, slots=True)
class ContextSelectionConfig:
    max_chunks: int = 5
    char_budget: int = 6_000
    separator: str = "\n\n"
    diversity_first: bool = True
    deduplicate_content: bool = True
    allow_neighbor_chunks: bool = False
    max_overlap_ratio: float | None = 0.8

    def __post_init__(self) -> None:
        if self.max_chunks <= 0:
            raise ValueError("max_chunks must be positive")
        if self.char_budget <= 0:
            raise ValueError("char_budget must be positive")
        if self.max_overlap_ratio is not None and not (
            0.0 <= self.max_overlap_ratio <= 1.0
        ):
            raise ValueError("max_overlap_ratio must be between zero and one")


@dataclass(frozen=True, slots=True)
class ContextSelector:
    config: ContextSelectionConfig = field(default_factory=ContextSelectionConfig)

    def select(
        self, candidates: Sequence[FusedCandidate]
    ) -> tuple[FusedCandidate, ...]:
        selected: list[FusedCandidate] = []
        selected_ids: set[str] = set()
        for candidate in self._selection_order(candidates):
            if len(selected) >= self.config.max_chunks:
                break
            if candidate.chunk_id in selected_ids:
                continue
            if self.config.deduplicate_content and any(
                _same_content(candidate, existing) for existing in selected
            ):
                continue
            if not self.config.allow_neighbor_chunks and any(
                _are_neighbors(candidate, existing) for existing in selected
            ):
                continue
            if self.config.max_overlap_ratio is not None and any(
                _boundary_overlap_ratio(candidate.content, existing.content)
                > self.config.max_overlap_ratio
                for existing in selected
            ):
                continue
            added_chars = len(candidate.content)
            if selected:
                added_chars += len(self.config.separator)
            if self.context_char_count(selected) + added_chars > self.config.char_budget:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.chunk_id)
        return tuple(selected)

    def context_char_count(self, candidates: Sequence[FusedCandidate]) -> int:
        if not candidates:
            return 0
        return sum(len(candidate.content) for candidate in candidates) + len(
            self.config.separator
        ) * (len(candidates) - 1)

    def _selection_order(
        self, candidates: Sequence[FusedCandidate]
    ) -> tuple[FusedCandidate, ...]:
        ordered = tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    candidate.reranked_rank
                    if candidate.reranked_rank is not None
                    else candidate.fused_rank,
                    candidate.chunk_id,
                ),
            )
        )
        if not self.config.diversity_first:
            return ordered
        first_by_document: list[FusedCandidate] = []
        remaining: list[FusedCandidate] = []
        seen_documents: set[str] = set()
        for candidate in ordered:
            if candidate.document_id in seen_documents:
                remaining.append(candidate)
            else:
                seen_documents.add(candidate.document_id)
                first_by_document.append(candidate)
        return (*first_by_document, *remaining)


def _same_content(left: FusedCandidate, right: FusedCandidate) -> bool:
    left_hash = left.metadata.get("content_hash")
    right_hash = right.metadata.get("content_hash")
    if left_hash and right_hash and left_hash == right_hash:
        return True
    return _normalized_content(left.content) == _normalized_content(right.content)


def _are_neighbors(left: FusedCandidate, right: FusedCandidate) -> bool:
    if left.document_id != right.document_id:
        return False
    left_links = _neighbor_links(left.metadata)
    right_links = _neighbor_links(right.metadata)
    if right.chunk_id in left_links or left.chunk_id in right_links:
        return True
    left_index = _chunk_index(left.metadata)
    right_index = _chunk_index(right.metadata)
    return (
        left_index is not None
        and right_index is not None
        and abs(left_index - right_index) == 1
    )


def _neighbor_links(metadata: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        value
        for key in ("previous_chunk_id", "next_chunk_id")
        if isinstance((value := metadata.get(key)), str) and value
    )


def _chunk_index(metadata: Mapping[str, Any]) -> int | None:
    value = metadata.get("chunk_index")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _boundary_overlap_ratio(left: str, right: str) -> float:
    left_normalized = _normalized_content(left)
    right_normalized = _normalized_content(right)
    shortest = min(len(left_normalized), len(right_normalized))
    if shortest == 0:
        return 0.0
    for size in range(shortest, 0, -1):
        if left_normalized[-size:] == right_normalized[:size] or right_normalized[
            -size:
        ] == left_normalized[:size]:
            return size / shortest
    return 0.0


def _normalized_content(value: str) -> str:
    return " ".join(value.casefold().split())
