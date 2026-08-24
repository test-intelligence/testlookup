"""Regression guard: a run changing underneath the pipeline is noticed (F-8).

The finding
-----------
Ten capability specs declare ``RunEvidenceBundleV1`` as their input, but the
bundle is built **only in the terminal stage**. No specialist ever receives one.
Instead ``anomaly_agent``, ``change_ownership_agent``, ``flaky_sentinel_agent``
and ``regression_watchman`` each ``select(TestRun)`` again, in their own
sessions, at their own point in the run — 49 independent sessions across the
agent package. If the row moves mid-pipeline (a concurrent finalize, an
aggregate repair, a status update) two specialists reason over different numbers
and the decision report combines them with nothing noticing.

What this does, and does not, do
--------------------------------
It does **not** fix the architecture. The real fix is to build the bundle once
and thread it through every specialist — ten agents, dozens of query sites.
Before paying for that it is worth knowing whether the drift actually happens.
Ingestion already reads the authoritative row; this fingerprints what it saw and
the terminal stage re-reads and compares.

What is guarded
---------------
* the fingerprint covers the counts and failed-test set every specialist reasons
  over, and ignores ordering, which is not a change;
* identical inputs produce no flag — a noisy signal gets switched off;
* the flag is a **warning**, never an error: an error-severity flag on this
  bundle is what stopped 24 decision reports from publishing earlier today;
* a probe that could not run reports **unverified**, not "clean" — a broken
  instrument must not issue a clean bill of health;
* the fingerprint survives contract validation, which silently strips
  undeclared fields.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.models.agent_contracts import (  # noqa: E402
    IngestionAgentOutput,
    validate_agent_contract,
)
from app.services.run_evidence_bundle import build_run_metric_snapshot  # noqa: E402
from app.services.run_input_fingerprint import (  # noqa: E402
    DRIFT_FLAG_CODE,
    PROBE_FAILED_FLAG_CODE,
    describe_drift,
    run_input_fingerprint,
)

_RUN = {
    "total_tests": 40,
    "passed_tests": 35,
    "failed_tests": 5,
    "skipped_tests": 0,
    "broken_tests": 0,
    "unknown_tests": 0,
    "status": "COMPLETED",
}


# ── The fingerprint tracks what matters, and ignores what does not ───────────


def test_identical_inputs_fingerprint_identically():
    assert run_input_fingerprint(_RUN, ["a", "b"]) == run_input_fingerprint(_RUN, ["a", "b"])


def test_reordering_the_failed_set_is_not_a_change():
    """Two reads of the same set in a different order are the same set.
    Flagging that would be noise, and a noisy signal gets switched off."""
    assert run_input_fingerprint(_RUN, ["a", "b"]) == run_input_fingerprint(_RUN, ["b", "a"])


def test_a_changed_count_changes_the_fingerprint():
    moved = {**_RUN, "failed_tests": 6}

    assert run_input_fingerprint(moved, ["a"]) != run_input_fingerprint(_RUN, ["a"])


def test_a_changed_failed_set_changes_the_fingerprint():
    assert run_input_fingerprint(_RUN, ["a"]) != run_input_fingerprint(_RUN, ["a", "c"])


def test_a_changed_status_changes_the_fingerprint():
    moved = {**_RUN, "status": "RUNNING"}

    assert run_input_fingerprint(moved, []) != run_input_fingerprint(_RUN, [])


def test_a_missing_run_does_not_explode():
    assert isinstance(run_input_fingerprint(None, None), str)


# ── Drift is reported, stability is not ──────────────────────────────────────


def test_no_drift_produces_no_flag():
    fp = run_input_fingerprint(_RUN, ["a"])

    assert describe_drift(fp, fp) is None


def test_drift_produces_a_warning_not_an_error():
    """An error-severity flag here fails metric_data_quality and blocks
    publication. That is exactly what took 24 reports down earlier today, before
    anyone knew the real rate. Measure first, escalate on evidence."""
    flag = describe_drift(run_input_fingerprint(_RUN, ["a"]), run_input_fingerprint(_RUN, ["b"]))

    assert flag is not None
    assert flag["code"] == DRIFT_FLAG_CODE
    assert flag["severity"] == "warning", "an error here blocks the decision report"


def test_an_absent_fingerprint_is_not_treated_as_drift():
    """A run from before this shipped must not be flagged."""
    assert describe_drift("", "abc") is None
    assert describe_drift("abc", "") is None


# ── A broken probe says so ───────────────────────────────────────────────────


def test_the_probe_failure_code_is_distinct_from_drift():
    """A probe that could not look must not read as 'no drift found'."""
    assert PROBE_FAILED_FLAG_CODE != DRIFT_FLAG_CODE


# ── The flag reaches the bundle ──────────────────────────────────────────────


def _state(**extra):
    return {
        "test_run_id": "11111111-1111-1111-1111-111111111111",
        "test_run_data": dict(_RUN),
        "failed_test_ids": ["a", "b", "c", "d", "e"],
        **extra,
    }


def test_the_drift_flag_appears_in_the_metric_snapshot():
    snapshot = build_run_metric_snapshot(_state(run_input_drift={
        "code": DRIFT_FLAG_CODE, "severity": "warning", "detail": "counts moved",
    }))
    codes = [f["code"] for f in snapshot["quality_flags"]]

    assert DRIFT_FLAG_CODE in codes


def test_a_clean_run_carries_no_drift_flag():
    snapshot = build_run_metric_snapshot(_state())
    codes = [f["code"] for f in snapshot["quality_flags"]]

    assert DRIFT_FLAG_CODE not in codes
    assert PROBE_FAILED_FLAG_CODE not in codes


def test_a_malformed_drift_value_is_ignored():
    snapshot = build_run_metric_snapshot(_state(run_input_drift="not-a-dict"))

    assert isinstance(snapshot["quality_flags"], list)


# ── The fingerprint survives the contract validator ──────────────────────────


def test_the_fingerprint_is_declared_on_the_ingestion_contract():
    """An undeclared key is silently stripped -- that is how a previous field
    reached no consumer at all while every test stayed green."""
    out = validate_agent_contract(
        IngestionAgentOutput,
        {"test_run_data": dict(_RUN), "run_input_fingerprint": "abc123"},
        agent_name="IngestionAgent",
    )

    assert out.get("run_input_fingerprint") == "abc123"
