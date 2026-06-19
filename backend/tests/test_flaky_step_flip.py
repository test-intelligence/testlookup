"""FLK-P6 slice 2 regression — cross-run step-flip computation (pure, no-DB).

Proves the computation FLK-P5 deferred until per-run step retention (#199)
existed: matching steps across runs by ordinal, counting PASSED<->FAILED
transitions, classifying direction (regression/recovery), bridging
status-less runs, the <2-run guard, status/enum normalisation, and the
never-raise invariant.
"""
from __future__ import annotations

from app.services.flaky_step_flip import (
    StepFlipReport,
    compute_step_flips,
)


def _run(run_id: str, *steps: tuple) -> dict:
    """A per-run window entry; each ``step`` is ``(ordinal, name, status)``."""
    return {
        "run_id": run_id,
        "steps": [
            {"ordinal": o, "name": n, "status": s} for (o, n, s) in steps
        ],
    }


class TestComputeStepFlips:
    def test_pass_then_fail_is_one_regression_flip(self):
        report = compute_step_flips([
            _run("r1", (0, "open cart", "PASSED"), (1, "click", "PASSED")),
            _run("r2", (0, "open cart", "PASSED"), (1, "click", "FAILED")),
        ])
        assert report.has_step_flip is True
        assert report.runs_analyzed == 2
        assert report.total_flips == 1
        assert len(report.flips) == 1
        flip = report.flips[0]
        assert flip.ordinal == 1
        assert flip.from_status == "PASSED"
        assert flip.to_status == "FAILED"
        assert flip.from_run_id == "r1"
        assert flip.to_run_id == "r2"
        assert flip.direction == "regression"
        assert report.flipping_steps[0].step_name == "click"
        assert report.flipping_steps[0].last_status == "FAILED"

    def test_fail_then_pass_is_recovery(self):
        report = compute_step_flips([
            _run("r1", (1, "click", "FAILED")),
            _run("r2", (1, "click", "PASSED")),
        ])
        assert report.flips[0].direction == "recovery"
        assert report.flips[0].from_status == "FAILED"
        assert report.flips[0].to_status == "PASSED"

    def test_oscillating_step_counts_each_transition(self):
        report = compute_step_flips([
            _run("r1", (1, "click", "PASSED")),
            _run("r2", (1, "click", "FAILED")),
            _run("r3", (1, "click", "PASSED")),
            _run("r4", (1, "click", "FAILED")),
        ])
        assert report.total_flips == 3
        summ = report.flipping_steps[0]
        assert summ.flip_count == 3
        assert summ.runs_observed == 4
        assert summ.is_flaky is True
        assert "flipped PASSED<->FAILED 3x" in report.summary

    def test_stable_step_has_no_flip(self):
        report = compute_step_flips([
            _run("r1", (0, "open cart", "PASSED")),
            _run("r2", (0, "open cart", "PASSED")),
            _run("r3", (0, "open cart", "PASSED")),
        ])
        assert report.has_step_flip is False
        assert report.total_flips == 0
        assert report.flipping_steps == ()
        assert "No cross-run step-flip across 3 runs" in report.summary

    def test_broken_normalises_to_failed_so_passed_to_broken_flips(self):
        report = compute_step_flips([
            _run("r1", (1, "click", "PASSED")),
            _run("r2", (1, "click", "BROKEN")),
        ])
        assert report.total_flips == 1
        assert report.flips[0].to_status == "FAILED"
        # FAILED -> BROKEN is the same normalised state: NOT a flip.
        report2 = compute_step_flips([
            _run("r1", (1, "click", "FAILED")),
            _run("r2", (1, "click", "BROKEN")),
        ])
        assert report2.total_flips == 0

    def test_skipped_runs_are_bridged_not_flipped(self):
        # PASSED, SKIPPED (no signal), FAILED -> exactly one flip across the
        # bridged sequence, and the SKIPPED run contributes no transition.
        report = compute_step_flips([
            _run("r1", (1, "click", "PASSED")),
            _run("r2", (1, "click", "SKIPPED")),
            _run("r3", (1, "click", "FAILED")),
        ])
        assert report.total_flips == 1
        assert report.flips[0].from_run_id == "r1"
        assert report.flips[0].to_run_id == "r3"
        assert report.flipping_steps[0].runs_observed == 2

    def test_enum_prefixed_status_is_normalised(self):
        report = compute_step_flips([
            _run("r1", (1, "click", "LaunchStatus.PASSED")),
            _run("r2", (1, "click", "LaunchStatus.FAILED")),
        ])
        assert report.total_flips == 1

    def test_distinct_ordinals_tracked_independently(self):
        report = compute_step_flips([
            _run("r1", (0, "a", "PASSED"), (1, "b", "PASSED")),
            _run("r2", (0, "a", "FAILED"), (1, "b", "PASSED")),
            _run("r3", (0, "a", "FAILED"), (1, "b", "FAILED")),
        ])
        # step 0 flips once (r1->r2), step 1 flips once (r2->r3).
        assert report.total_flips == 2
        ordinals = {s.ordinal for s in report.flipping_steps}
        assert ordinals == {0, 1}

    def test_flipping_steps_sorted_by_flip_count_desc(self):
        report = compute_step_flips([
            _run("r1", (0, "a", "PASSED"), (1, "b", "PASSED")),
            _run("r2", (0, "a", "FAILED"), (1, "b", "FAILED")),
            _run("r3", (0, "a", "PASSED"), (1, "b", "FAILED")),
            _run("r4", (0, "a", "FAILED"), (1, "b", "FAILED")),
        ])
        # step 0 flips 3x, step 1 flips 1x -> step 0 first.
        assert [s.ordinal for s in report.flipping_steps] == [0, 1]
        assert report.flipping_steps[0].flip_count == 3

    def test_latest_name_wins_for_display(self):
        report = compute_step_flips([
            _run("r1", (1, "old name", "PASSED")),
            _run("r2", (1, "new name", "FAILED")),
        ])
        assert report.flips[0].step_name == "new name"
        assert report.flipping_steps[0].step_name == "new name"


class TestGuardsAndDefensiveness:
    def test_single_run_reports_insufficient_history(self):
        report = compute_step_flips([_run("r1", (1, "click", "PASSED"))])
        assert report.has_step_flip is False
        assert report.runs_analyzed == 1
        assert report.total_flips == 0
        assert "need >=2 runs" in report.summary

    def test_empty_window(self):
        report = compute_step_flips([])
        assert isinstance(report, StepFlipReport)
        assert report.runs_analyzed == 0
        assert report.has_step_flip is False

    def test_malformed_rows_do_not_raise(self):
        report = compute_step_flips([
            {"run_id": "r1", "steps": "not-a-list"},
            {"run_id": "r2", "steps": [{"ordinal": "bad", "status": "PASSED"}]},
            {"run_id": "r3"},
            None,  # type: ignore[list-item]
            {"run_id": "r4", "steps": [{"ordinal": 1, "status": "PASSED"}]},
            {"run_id": "r5", "steps": [{"ordinal": 1, "status": "FAILED"}]},
        ])
        # Only ordinal 1 in r4/r5 yields a comparable pair -> one flip; the
        # malformed entries contribute nothing and nothing raises.
        assert report.total_flips == 1
        assert report.flips[0].from_run_id == "r4"
        assert report.flips[0].to_run_id == "r5"

    def test_to_dict_is_json_shaped(self):
        report = compute_step_flips([
            _run("r1", (1, "click", "PASSED")),
            _run("r2", (1, "click", "FAILED")),
        ])
        d = report.to_dict()
        assert d["has_step_flip"] is True
        assert d["total_flips"] == 1
        assert isinstance(d["flips"], list)
        assert d["flips"][0]["direction"] == "regression"
        assert isinstance(d["flipping_steps"], list)
        assert d["flipping_steps"][0]["ordinal"] == 1
