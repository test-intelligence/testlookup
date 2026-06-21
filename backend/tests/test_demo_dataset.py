"""Adoption regression — the demo dataset generator (slice 2).

Pins the synthetic history that makes a fresh `make quickstart` show populated
dashboards/flaky-coach/trends/failures: valid IngestPayload shape, a real date
spread, and each embedded pattern (stable / flaky / regression / product-bug /
perf) being detectable downstream. Pure — no DB.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.schemas import IngestPayload, IngestTestResult
from app.services.demo_dataset import generate_demo_runs

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _runs(n=14):
    return generate_demo_runs(runs=n, now=_NOW)


def _status_series(runs, test_name):
    """Status per run for a test, oldest→newest."""
    out = []
    for r in runs:
        m = next((x for x in r.results if x["test_name"] == test_name), None)
        out.append(m["status"] if m else None)
    return out


def test_payloads_validate_against_ingest_schema():
    for r in _runs():
        # validates project_id/build_number/results envelope
        IngestPayload(**r.to_payload("11111111-1111-1111-1111-111111111111"))
        assert r.results
        for res in r.results:           # raw dicts → validate each field
            IngestTestResult(**res)


def test_runs_span_the_window_oldest_to_newest():
    runs = _runs(14)
    times = [r.started_at for r in runs]
    assert times == sorted(times), "runs must be oldest→newest"
    span_days = (times[-1] - times[0]).total_seconds() / 86400
    assert 25 <= span_days <= 31, span_days
    assert times[-1] <= _NOW


def test_build_numbers_unique():
    runs = _runs()
    builds = [r.build_number for r in runs]
    assert len(set(builds)) == len(builds)


def test_flaky_test_alternates_pass_and_fail():
    s = _status_series(_runs(14), "test_checkout_completes")
    assert s.count("PASSED") >= 3 and s.count("FAILED") >= 3, s


def test_flaky_failures_have_varied_error_signatures():
    runs = _runs(14)
    msgs = {
        x["error_message"]
        for r in runs for x in r.results
        if x["test_name"] == "test_checkout_completes" and x["status"] == "FAILED"
    }
    assert len(msgs) >= 2, "environmental flake should show varied error signatures"


def test_regression_is_clean_then_failing():
    s = _status_series(_runs(14), "test_payment_capture")
    assert s[0] == "PASSED", "should be clean in the oldest run"
    assert s[-1] == "FAILED", "should be failing in the latest run"
    assert "PASSED" in s and s.count("FAILED") >= 2


def test_product_bug_fails_every_run():
    s = _status_series(_runs(14), "test_legacy_discount_calc")
    assert set(s) == {"FAILED"}


def test_stable_tests_always_pass():
    s = _status_series(_runs(14), "test_login_with_valid_credentials")
    assert set(s) == {"PASSED"}


def test_failed_results_carry_error_messages():
    for r in _runs():
        for x in r.results:
            if x["status"] in ("FAILED", "BROKEN"):
                assert x.get("error_message"), x["test_name"]


def test_perf_test_duration_spikes_in_recent_runs():
    runs = _runs(14)
    def dur(run):
        return next(x["duration_ms"] for x in run.results if x["test_name"] == "test_report_export")
    early = dur(runs[0])
    latest = dur(runs[-1])
    assert latest > early * 2, (early, latest)


def test_pass_rate_is_realistic_each_run():
    for r in _runs():
        passed = sum(1 for x in r.results if x["status"] == "PASSED")
        assert passed / len(r.results) >= 0.5, "demo should not look all-red"


def test_test_identity_stable_across_runs():
    runs = _runs(14)
    def sig(run):
        return {(x["test_name"], x["suite_name"]) for x in run.results}
    first = sig(runs[0])
    assert all(sig(r) == first for r in runs), "same tests every run (stable fingerprints)"


def test_deterministic():
    a = generate_demo_runs(runs=10, now=_NOW)
    b = generate_demo_runs(runs=10, now=_NOW)
    assert [r.to_payload("p") for r in a] == [r.to_payload("p") for r in b]


def test_runs_clamped_to_minimum():
    assert len(generate_demo_runs(runs=2, now=_NOW)) >= 6
