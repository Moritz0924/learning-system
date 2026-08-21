from __future__ import annotations

from backend.app.domain.rag.chunking.v3.threshold import AdaptiveThresholdPolicy


def test_adaptive_threshold_uses_median_mad_and_strict_comparison() -> None:
    policy = AdaptiveThresholdPolicy(min_samples=5, mad_multiplier=1.5)

    threshold = policy.threshold([0.1, 0.2, 0.3, 0.4, 0.9])

    assert threshold is not None
    assert threshold > 0.1
    assert policy.select(0.1, threshold) is False
    assert policy.select(0.9, threshold) is True


def test_adaptive_threshold_returns_none_for_short_or_zero_mad_distribution() -> None:
    policy = AdaptiveThresholdPolicy(min_samples=5)

    assert policy.threshold([0.1, 0.2, 0.3, 0.4]) is None
    assert policy.threshold([0.2] * 5) is None
