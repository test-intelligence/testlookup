"""The canonical pass-rate rule, in one place.

``passed / executed``, where ``executed = passed + failed + broken``. A skipped
test was never run, so it is neither a pass nor a fail and does not belong in
the denominator.

This exists because the rule was restated in six places and two of them
disagreed. ``app.services.ingestion._update_run_aggregates`` is the single
source of truth for both the file and live ingest paths and gets this right;
it is pinned by ``tests/regression/test_pass_rate_excludes_skipped.py``. Both
demo seeders still divided by ``total``, the formula the product had already
abandoned:

* ``backend/scripts/seed_data.py`` -- 28 of 294 seeded runs stored a pass rate
  the application itself calls wrong, understated by up to 8.47 percentage
  points. Every diverging run had at least one skip, and no run without skips
  diverged, which is what identified the denominator as the cause.
* ``backend/scripts/seed_dev_data.py`` -- same expression, same defect.

Seeded rows are the baseline a developer or evaluator reads first, so a wrong
number here is not cosmetic: it is what hides a real regression.
TL-2026-09-18-01-005 -- every ingested run carrying a NULL duration -- stayed
invisible for exactly this reason, because the seeder filled the column that
ingestion had stopped writing.

This is deliberately NOT the same question as which *population* a rate is
computed over. ``/reports/summary`` reports per-unique-test while ``/runs``
reports per-execution, and that divergence is a recorded product decision --
see the comment in ``summary_report_service`` and do not collapse the two here.
This module only fixes the denominator's treatment of skips.
"""

from __future__ import annotations

__all__ = ["canonical_pass_rate", "executed_count"]


def executed_count(passed: int, failed: int, broken: int) -> int:
    """Tests that actually ran. Skipped is excluded by construction."""
    return int(passed) + int(failed) + int(broken)


def canonical_pass_rate(
    passed: int, failed: int, broken: int = 0, *, ndigits: int = 2
) -> float:
    """Percentage of executed tests that passed, rounded to ``ndigits``.

    Returns ``0.0`` when nothing executed. A run of nothing but skips has no
    pass rate to report; it must not read as 100%, which would turn "we tested
    nothing" into "everything passed".
    """
    executed = executed_count(passed, failed, broken)
    if executed <= 0:
        return 0.0
    return round(passed / executed * 100, ndigits)
