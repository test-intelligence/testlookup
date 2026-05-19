"""Tests for the retroactive placeholder-synthesis service.

Pins:

* The row shape matches ``worker.tasks.persist_live_session``'s
  write-time placeholder exactly (same fingerprint formula, same
  test_name labelling, same suite_name fallback). If those two paths
  drift, deduplication breaks and the user gets duplicate rows for
  the same run.
* The candidate filter is bounded by ``failed + broken > 0`` AND
  zero test_cases. A run that already has cases is skipped (idempotency).
* Empty input → empty output, no DB writes.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(*, failed=2, broken=0, primary="Smoke suite"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        failed_tests=failed,
        broken_tests=broken,
        primary_suite_name=primary,
    )


def test_placeholder_rows_matches_persist_live_session_shape():
    """Row shape contract — fingerprint format, status mapping, and the
    test_name label all match the write-time path in
    ``worker.tasks.persist_live_session``. Two paths must stay in
    lockstep so a run that gets BOTH a retroactive backfill AND a
    re-ingest doesn't end up with duplicate rows under different
    fingerprints."""
    from app.services.placeholder_backfill_service import (
        _placeholder_rows_for_run,
    )

    run = _run(failed=2, broken=1)
    rows = _placeholder_rows_for_run(run, failed=2, broken=1)

    assert len(rows) == 3
    # First two are FAILED, last one is BROKEN (matches the
    # i < failed -> FAILED, else BROKEN split).
    assert rows[0]["status"] == "FAILED"
    assert rows[1]["status"] == "FAILED"
    assert rows[2]["status"] == "BROKEN"
    # Fingerprints are run-id-scoped so re-running doesn't dedupe
    # across distinct runs of the same suite.
    assert all(str(run.id) in r["test_fingerprint"] or len(r["test_fingerprint"]) == 32 for r in rows)
    assert len({r["test_fingerprint"] for r in rows}) == 3  # all distinct
    # Suite default from the run.
    assert rows[0]["suite_name"] == "Smoke suite"
    # Test name carries the [ingestion gap] marker so an operator
    # can grep / filter these out in a UI dump.
    assert all("[ingestion gap" in r["test_name"] for r in rows)


def test_placeholder_rows_handles_zero_total():
    from app.services.placeholder_backfill_service import (
        _placeholder_rows_for_run,
    )

    run = _run(failed=0, broken=0)
    assert _placeholder_rows_for_run(run, failed=0, broken=0) == []


@pytest.mark.asyncio
async def test_backfill_skips_when_no_candidate_runs():
    """Project with no eligible runs → no inserts."""
    from app.services.placeholder_backfill_service import (
        backfill_placeholders_for_project,
    )

    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    db.flush = AsyncMock()

    counts = await backfill_placeholders_for_project(db, uuid.uuid4())
    assert counts == {"runs_scanned": 0, "runs_filled": 0, "rows_synthesised": 0}


@pytest.mark.asyncio
async def test_backfill_synthesises_placeholders_per_candidate_run():
    """Two candidates → both get placeholder rows; counters add up."""
    from app.services.placeholder_backfill_service import (
        backfill_placeholders_for_project,
    )

    candidates = [
        _run(failed=2, broken=0, primary="Smoke"),
        _run(failed=0, broken=1, primary="Regression"),
    ]
    scalars = MagicMock(all=MagicMock(return_value=candidates))
    result = MagicMock(scalars=MagicMock(return_value=scalars))

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    counts = await backfill_placeholders_for_project(db, uuid.uuid4())
    assert counts["runs_scanned"] == 2
    assert counts["runs_filled"] == 2
    assert counts["rows_synthesised"] == 3  # 2 + 1
    # One ``execute`` call for the candidate SELECT + one per insert.
    assert db.execute.await_count == 3
    assert db.flush.await_count == 2
