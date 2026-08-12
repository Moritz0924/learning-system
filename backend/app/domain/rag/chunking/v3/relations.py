from __future__ import annotations

import re
from dataclasses import dataclass

from .domain import SemanticUnit


_MARKERS = (
    "因此", "所以", "此外", "同时", "另一方面", "例如", "具体来说", "首先", "其次", "最后", "上述",
    "therefore", "however", "moreover", "for example", "specifically", "first", "second", "finally",
)
_TERMINAL_PUNCTUATION = ".!?。！？"


class AdjacentRelationChecker:
    def check(self, left: SemanticUnit, right: SemanticUnit) -> "AdjacentRelationResult":
        reasons: list[str] = []
        left_text = left.text.rstrip()
        right_text = right.text.lstrip()
        lowered = right_text.casefold()
        if any(lowered.startswith(marker.casefold()) for marker in _MARKERS):
            reasons.append("discourse_continuation")
        if left_text.endswith(":") or left_text.endswith(("：", "如下")):
            reasons.append("colon_explanation")
        if re.match(r"^(?:\d+[.)]|[①②③④⑤⑥⑦⑧⑨]|[-*•])\s*", right_text):
            reasons.append("ordered_list" if right_text[:1].isdigit() else "unordered_list")
        if left.heading_path == right.heading_path:
            reasons.append("same_heading_path")
        unfinished = bool(left_text and left_text[-1] not in _TERMINAL_PUNCTUATION)
        if unfinished:
            reasons.append("syntactic_continuation")
        if left.page_end != right.page_start and unfinished:
            reasons.append("cross_page_continuation")
        score = min(1.0, 0.18 * len(reasons))
        return AdjacentRelationResult(continuation_score=score, reasons=tuple(reasons))


@dataclass(frozen=True)
class AdjacentRelationResult:
    continuation_score: float
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.continuation_score <= 1:
            raise ValueError("continuation_score must be between 0 and 1")
