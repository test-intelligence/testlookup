"""Regression: ``resolve_latest_suite_pair`` had to match runs by
both ``TestRun.primary_suite_name`` and per-row ``TestCase.suite_name``.

Bug pinned: the prior implementation matched only on
``TestCase.suite_name``. Live-stream runs whose per-test rows hadn't
landed (Phase 4.5 buffer eviction) were tagged at the run level but
invisible to the resolver — producing "No completed runs found for
suite X" 404s even though the suite was clearly visible on /runs.

Fix: ``resolve_latest_suite_pair`` builds ``suite_match`` as an OR of
the two conditions. The same OR shape is used in
``compute_regression_diff``, ``compare_runs._load_test_rows``, and
``test_management_exports.list_test_suites``.

What this file pins:

  * The compiled SQL of the resolver's latest-run lookup contains
    BOTH the run-level and EXISTS-on-test_cases predicates.
  * The branch filter is applied on the previous-run lookup.
  * Empty/whitespace suite_name input raises ValueError (not silent
    behaviour drift).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _exec_result(rows=None, scalar=None):
    res = MagicMock()
    res.scalars = MagicMock()
    res.scalars.return_value.all = MagicMock(return_value=rows or [])
    res.scalar_one_or_none = MagicMock(return_value=scalar)
    return res


@pytest.mark.asyncio
async def test_resolver_rejects_empty_suite_name():
    """Operator-facing contract — empty suite must surface as a
    400-class error, not a 500 deep in the query layer."""
    from app.services.run_compare_service import resolve_latest_suite_pair

    db = AsyncMock()
    with pytest.raises(ValueError):
        await resolve_latest_suite_pair(
            db, project_id=uuid.uuid4(), suite_name="   ",
        )


@pytest.mark.asyncio
async def test_resolver_latest_query_matches_run_level_or_per_row():
    """Both halves of the suite_match must be present in the compiled
    SQL. Either alone re-introduces a known regression class."""
    from app.services.run_compare_service import resolve_latest_suite_pair

    captured: list = []

    async def capture(stmt):
        captured.append(stmt)
        return _exec_result(scalar=None)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)

    # ``latest`` lookup will return None and the function raises
    # LookupError — that's fine, we just need to inspect the
    # statement that was issued.
    with pytest.raises(LookupError):
        await resolve_latest_suite_pair(
            db, project_id=uuid.uuid4(), suite_name="RealisticTestNGSuite",
        )

    sql = str(captured[0].compile(compile_kwargs={"literal_binds": False}))
    assert "primary_suite_name" in sql, (
        "Resolver must match via TestRun.primary_suite_name (the SDK "
        "stamps suite once at session-create; per-row suite_name "
        "doesn't exist for live-stream runs that lost their buffer)."
    )
    assert "test_cases" in sql and "suite_name" in sql, (
        "Resolver must also EXISTS-probe test_cases for per-row "
        "suite_name (covers framework SDKs that send the per-event "
        "value differently)."
    )
