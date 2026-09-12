"""E7.2: one retry policy for pipeline runs."""
from __future__ import annotations

import random

import pytest

from app.services.retry_policy import NON_RETRYABLE, RetryPolicy, pipeline_retry_policy


def test_defaults_match_requirement_8():
    p = RetryPolicy()
    assert p.max_attempts == 5
    assert (p.base_seconds, p.cap_seconds, p.jitter) == (30.0, 600.0, 0.2)


@pytest.mark.parametrize("attempt,raw", [(1, 30), (2, 60), (3, 120), (4, 240), (5, 480), (6, 600), (9, 600)])
def test_raw_delay_doubles_then_caps(attempt, raw):
    assert RetryPolicy().raw_delay(attempt) == raw


@pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5])
def test_jittered_delay_stays_inside_the_symmetric_band(attempt):
    p = RetryPolicy()
    lo, hi = p.bounds(attempt)
    rng = random.Random(1234)
    samples = [p.delay(attempt, rng=rng) for _ in range(500)]
    assert min(samples) >= lo - 1e-9 and max(samples) <= hi + 1e-9
    # symmetric: values fall on both sides of the raw delay (the old helper never went below it)
    raw = p.raw_delay(attempt)
    assert any(s < raw for s in samples) and any(s > raw for s in samples)


def test_can_retry_stops_at_max_attempts():
    p = RetryPolicy(max_attempts=5)
    assert all(p.can_retry(n) for n in (1, 2, 3, 4))
    assert p.can_retry(5) is False
    assert p.can_retry(6) is False


@pytest.mark.parametrize("code", sorted(NON_RETRYABLE))
def test_non_retryable_codes_are_never_retried(code):
    assert RetryPolicy().is_retryable(code) is False


@pytest.mark.parametrize("code", ["model_unavailable", "timeout", "tool_error", "lease_expired", None, "unknown"])
def test_transient_codes_are_retried(code):
    assert RetryPolicy().is_retryable(code) is True


def test_with_max_attempts_only_tightens():
    p = RetryPolicy(max_attempts=5)
    assert p.with_max_attempts(3).max_attempts == 3
    assert p.with_max_attempts(50, ceiling=10).max_attempts == 10
    assert p.with_max_attempts(0).max_attempts == 1


def test_invalid_policy_is_rejected():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(jitter=1.0)
    with pytest.raises(ValueError):
        RetryPolicy(base_seconds=0)


def test_pipeline_policy_reads_settings_and_clamps_to_ceiling(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "AGENT_PIPELINE_MAX_ATTEMPTS", 7, raising=False)
    monkeypatch.setattr(config.settings, "AGENT_MAX_ATTEMPTS_CEILING", 6, raising=False)
    assert pipeline_retry_policy().max_attempts == 6
    # a per-row value may tighten, never loosen past the ceiling
    assert pipeline_retry_policy(max_attempts=2).max_attempts == 2
    assert pipeline_retry_policy(max_attempts=99).max_attempts == 6


def test_backoff_helper_delegates_to_the_policy(monkeypatch):
    from app.worker import tasks

    seen = []
    monkeypatch.setattr(random, "random", lambda: 0.5)  # midpoint => exactly the raw delay
    for celery_retries, expected in [(0, 30), (1, 60), (2, 120), (5, 600)]:
        seen.append(tasks._exponential_backoff(celery_retries))
        assert seen[-1] == expected, (celery_retries, seen[-1])
    assert tasks._exponential_backoff(0, base=10, cap=60) == 10
