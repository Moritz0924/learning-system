"""High-resolution latency aggregation for evaluation reports."""
from __future__ import annotations

import statistics
from collections.abc import Sequence


def percentile(sorted_values: Sequence[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight)


def latency_summary(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "std": 0.0, "count": 0}
    ordered = sorted(float(value) for value in values)
    return {
        "mean": statistics.mean(ordered),
        "p50": percentile(ordered, 50),
        "p95": percentile(ordered, 95),
        "min": ordered[0],
        "max": ordered[-1],
        "std": statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
        "count": len(ordered),
    }
