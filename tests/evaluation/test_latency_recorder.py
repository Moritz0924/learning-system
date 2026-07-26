from __future__ import annotations


def test_latency_summary_uses_interpolated_percentiles_and_population_std() -> None:
    from evals.runner.timing import latency_summary

    summary = latency_summary([1, 2, 3, 4, 100])

    assert summary["mean"] == 22
    assert summary["p50"] == 3
    assert summary["p95"] == 80.79999999999998
    assert summary["min"] == 1
    assert summary["max"] == 100
    assert summary["count"] == 5
    assert summary["std"] > 0


def test_latency_summary_handles_empty_and_singleton_samples() -> None:
    from evals.runner.timing import latency_summary

    assert latency_summary([])["count"] == 0
    singleton = latency_summary([7.5])
    assert singleton["p50"] == singleton["p95"] == 7.5
    assert singleton["std"] == 0
