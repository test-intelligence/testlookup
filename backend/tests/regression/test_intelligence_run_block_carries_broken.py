"""Regression guard: the run-intelligence ``run`` block must carry
``broken_tests``.

The defect
----------
``/runs/{id}/intelligence`` returned a ``run`` summary with ``total_tests``,
``passed_tests``, ``failed_tests``, ``skipped_tests`` and ``pass_rate`` — and
no ``broken_tests``. On a ground-truth run of 10 tests (4 passed, 4 FAILED,
1 BROKEN, 1 skipped) the consumer saw::

    total=10  passed=4  failed=4  skipped=1     -> 9 of 10 accounted for

The missing test was the BROKEN one. The same run on ``/runs/{id}`` DOES
carry ``broken_tests: 1``, so two surfaces described one run differently.

Why it was invisible
--------------------
``runIntelligenceService.ts`` declares ``broken_tests: number`` — **required**
— so the frontend type asserted the field was always sent. TypeScript cannot
check a runtime payload, and ``RunIntelligencePage`` read it defensively as
``run.broken_tests ?? 0``. Its otherwise-correct
``failed_tests + broken_tests`` therefore evaluated to the FAILED-only count,
and the page's pass rate came out 40.0% where the payload's own ``pass_rate``
said 44.44.

A field the declared contract promises and the consumer already reads must
actually be sent. This guard pins the run block against the ``TestRun``
columns rather than against a hand-written list, so a future column that the
other run contracts expose cannot silently go missing here.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.regression


def _run_summary_source() -> str:
    """The literal text of the ``run_summary`` dict.

    Scans to the closing brace ON ITS OWN LINE rather than to the first ``}``
    in the text. The first version did the latter and was truncated by a
    ``{id}`` inside an explanatory comment three lines in — a guard broken by
    prose it was standing next to.
    """
    from app.services import run_intelligence_service

    src = inspect.getsource(run_intelligence_service)
    start = src.index("run_summary = {")
    lines = src[start:].splitlines()
    collected: list[str] = []
    for line in lines:
        collected.append(line)
        if line.strip() == "}" and len(collected) > 1:
            break
    else:  # pragma: no cover - only if the dict is never closed
        raise AssertionError("run_summary dict has no closing brace on its own line")
    return "\n".join(collected)


def test_the_run_block_includes_broken_tests():
    assert '"broken_tests"' in _run_summary_source(), (
        "the intelligence run block omits broken_tests, so its consumer's "
        "`failed_tests + broken_tests` silently reads the FAILED-only count"
    )


def test_the_run_block_carries_every_outcome_column():
    """All four outcome counts, or the totals cannot be reconciled.

    total - passed - failed - broken - skipped must be able to reach zero;
    dropping any one of them makes the block unaddable by construction.
    """
    src = _run_summary_source()
    for column in ("total_tests", "passed_tests", "failed_tests",
                   "broken_tests", "skipped_tests"):
        assert f'"{column}"' in src, f"run block is missing {column}"


def test_the_guard_is_reading_the_real_block():
    """Fail-open check.

    If ``run_summary = {`` is ever renamed, the slice above would return
    something unrelated and the assertions could pass on nothing. Anchor on
    a field that must always be present.
    """
    src = _run_summary_source()
    assert '"build_number"' in src and '"pass_rate"' in src, (
        "the run_summary slice no longer looks like the run block; the "
        "assertions above would be checking the wrong text"
    )


def test_it_matches_the_outcome_columns_the_runs_contract_exposes():
    """The two run contracts must not disagree about which columns exist.

    ``/runs/{id}`` serialises the ORM row, so every outcome column on
    ``TestRun`` is available there. The intelligence block is hand-written,
    which is exactly how it fell behind.
    """
    from app.models.postgres import TestRun

    src = _run_summary_source()
    outcome_columns = [
        c for c in TestRun.__table__.columns.keys()
        if c.endswith("_tests")
    ]
    assert outcome_columns, "no *_tests columns found on TestRun — guard is blind"
    missing = [c for c in outcome_columns if f'"{c}"' not in src]
    assert not missing, (
        f"the intelligence run block omits outcome columns {missing} that "
        "the /runs contract exposes for the same run"
    )
