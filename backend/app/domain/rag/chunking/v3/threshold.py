from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence


@dataclass(frozen=True)
class AdaptiveThresholdPolicy:
    min_samples: int = 5
    mad_multiplier: float = 1.5

    def threshold(self, scores: Sequence[float]) -> float | None:
        if len(scores) < self.min_samples:
            return None
        center = median(scores)
        mad = median([abs(score - center) for score in scores])
        if mad == 0:
            return None
        return center + 1.4826 * mad * self.mad_multiplier

    @staticmethod
    def select(score: float, threshold: float | None) -> bool:
        return threshold is not None and score > threshold

