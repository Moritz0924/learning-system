"""Shared metric aggregation helpers for evaluation summaries."""
from __future__ import annotations

from collections.abc import Iterable

from evals.runner.timing import latency_summary


def mean_or_none(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else None


def rate_with_denominator(values: Iterable[bool | float]) -> tuple[float | None, int]:
    materialized = [float(value) for value in values]
    return mean_or_none(materialized), len(materialized)


__all__ = ["latency_summary", "mean_or_none", "rate_with_denominator"]
