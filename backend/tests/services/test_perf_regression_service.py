"""
Unit tests for ``services.perf_regression_service`` — pure Welford
update + spike detection. No DB.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.services import perf_regression_service as svc


def _empty_baseline() -> SimpleNamespace:
    return SimpleNamespace(
        sample_count=0,
        mean_ms=0.0,
        m2=0.0,
        stddev_ms=0.0,
        p95_ms=None,
        last_observed_ms=None,
        last_observed_at=None,
    )


# ── _welford_update ────────────────────────────────────────────────────────


def test_welford_single_observation():
    b = _empty_baseline()
    svc._welford_update(b, 100)
    assert b.sample_count == 1
    assert b.mean_ms == 100.0
    assert b.m2 == 0.0
    assert b.stddev_ms == 0.0


def test_welford_matches_numpy_std_over_small_series():
    import statistics
    b = _empty_baseline()
    samples = [100, 110, 95, 120, 105, 98, 115, 102, 108, 99]
    for s in samples:
        svc._welford_update(b, s)
    expected_mean = statistics.mean(samples)
    expected_stddev = statistics.stdev(samples)  # sample stddev, n-1
    assert b.sample_count == len(samples)
    assert b.mean_ms == pytest.approx(expected_mean)
    assert b.stddev_ms == pytest.approx(expected_stddev, rel=1e-6)


def test_welford_ignores_non_positive_observations():
    b = _empty_baseline()
    svc._welford_update(b, 0)
    svc._welford_update(b, -5)
    svc._welford_update(b, None)  # type: ignore[arg-type]
    assert b.sample_count == 0
    assert b.mean_ms == 0.0


def test_welford_approximated_p95_uses_one_sided_normal():
    b = _empty_baseline()
    # Same samples as the numpy test, expectations lifted from there.
    samples = [100, 110, 95, 120, 105, 98, 115, 102, 108, 99]
    for s in samples:
        svc._welford_update(b, s)
    expected_p95 = b.mean_ms + 1.645 * b.stddev_ms
    assert b.p95_ms == pytest.approx(expected_p95)


# ── is_spike ──────────────────────────────────────────────────────────────


def test_is_spike_requires_minimum_sample_count():
    b = _empty_baseline()
    b.sample_count = 5
    b.mean_ms = 100
    b.stddev_ms = 10
    # Obvious outlier but we don't trust the baseline yet.
    assert svc.is_spike(b, 500) is False


def test_is_spike_fires_past_three_sigma_with_enough_samples():
    b = _empty_baseline()
    b.sample_count = 20
    b.mean_ms = 100
    b.stddev_ms = 10
    # mean + 3σ = 130, so 135 qualifies.
    assert svc.is_spike(b, 135) is True
    assert svc.is_spike(b, 125) is False


def test_is_spike_handles_zero_stddev():
    """Zero variance baseline — every observation is not a spike because
    the denominator would be zero. Avoid divide-by-zero false positives."""
    b = _empty_baseline()
    b.sample_count = 20
    b.mean_ms = 100
    b.stddev_ms = 0
    assert svc.is_spike(b, 1000) is False


def test_is_spike_rejects_non_positive_observation():
    b = _empty_baseline()
    b.sample_count = 20
    b.mean_ms = 100
    b.stddev_ms = 10
    assert svc.is_spike(b, 0) is False
    assert svc.is_spike(b, -1) is False


def test_is_spike_custom_sigma_threshold():
    b = _empty_baseline()
    b.sample_count = 20
    b.mean_ms = 100
    b.stddev_ms = 10
    # Same observation at 120: 2σ — fires at 2σ threshold, not at 3σ.
    assert svc.is_spike(b, 120, sigma=2.0) is True
    assert svc.is_spike(b, 120, sigma=3.0) is False
