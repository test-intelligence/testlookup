"""Regression guard: the decision report must state its own analysis coverage.

The defect
----------
``gap_detection_agent`` runs on every deep workflow. It classifies each failed
test into five ``GapReason`` buckets and computes ``coverage_ratio`` and
``integrity_ok``. All of that was contract-validated, threaded through the
pipeline, and parked at ``quality_review.gap_report`` -- where **no consumer
read any of it**: not this markdown, not ``report_status``, not
``requires_human_review``, not the frontend, not MCP, not the CLI.

Because ``report_status`` is ``degraded`` only when a *specialist stage* is
missing or the persisted payload was truncated, a run where every specialist
succeeded but only 1 of 50 failures was ever analysed produced a report that
read exactly like one resting on all 50. The release recommendation was the
same sentence; the evidence behind it was 2% of the failure set.

What is guarded
---------------
That the coverage the pipeline already measured reaches the report a human
reads, and reaches it *honestly* in three cases that are easy to conflate:

* uncovered failures are named with both numbers, so the reader can judge;
* a failed integrity check reports coverage as UNKNOWN rather than printing
  the counts that just failed to reconcile;
* an absent ``gap_report`` says "not recorded", never a fabricated ``0/0`` --
  reports predating the field carry none, and claiming full coverage for them
  would be the same class of lie in the other direction.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.regression

from app.agents.decision_report_agent import _coverage_line  # noqa: E402


def _gap(**kw):
    base = dict(
        failed_count=50, analyzed_count=50, skipped_count=0, errored_count=0,
        integrity_ok=True, inconclusive_count=0, no_evidence_count=0,
    )
    base.update(kw)
    return {"gap_report": base}


def test_it_names_both_numbers_when_failures_went_unanalysed():
    line = _coverage_line(_gap(analyzed_count=1, skipped_count=49))
    assert "1" in line and "50" in line, line
    assert "49" in line, line
    assert "never analysed" in line.lower(), (
        "the reader is not told that failures were skipped: " + line
    )


def test_errored_failures_are_not_counted_as_analysed():
    line = _coverage_line(_gap(analyzed_count=40, skipped_count=0, errored_count=10))
    assert "10" in line, line
    assert "never analysed" in line.lower(), line


def test_a_failed_integrity_check_reports_coverage_as_unknown():
    """The agent says its own counts do not reconcile. Printing them as fact
    would launder an arithmetic failure into a coverage claim."""
    line = _coverage_line(_gap(integrity_ok=False))
    assert "unknown" in line.lower() or "not reconcile" in line.lower(), line


def test_full_coverage_says_so_without_a_gap_clause():
    line = _coverage_line(_gap())
    assert "50/50" in line, line
    assert "never analysed" not in line.lower(), line


def test_absent_gap_data_is_not_reported_as_full_coverage():
    for quality in ({}, {"gap_report": None}, {"gap_report": {}}):
        line = _coverage_line(quality)
        assert "not recorded" in line.lower(), (quality, line)
        assert "0/0" not in line, (quality, line)


def test_malformed_counts_degrade_instead_of_raising():
    """This runs inside report generation; an exception here loses the report."""
    line = _coverage_line({"gap_report": {"failed_count": "ten", "analyzed_count": 1}})
    assert "not recorded" in line.lower(), line


def test_the_markdown_actually_carries_the_line():
    """A helper nothing calls guards nothing -- the class that produced this
    defect in the first place."""
    import inspect
    from app.agents import decision_report_agent as mod

    source = inspect.getsource(mod)
    assert "_coverage_line(quality)" in source, (
        "_coverage_line is never called from the markdown builder, so the "
        "coverage it computes reaches no reader -- exactly the defect this "
        "guards against"
    )
