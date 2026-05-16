"""Regression test for the trend ``date`` field wire format (2026-05-16).

User reported that ``/coverage`` Run-cadence heatmap was empty for the 7d
and 14d windows even when run data existed. Root cause: ``get_trend_data``
returned ``"date": row.day.strftime("%b %d")`` which yields ``"May 16"``.
Every downstream frontend consumer keys lookups by an ISO date pulled from
``new Date().toISOString().slice(0, 10)`` — ``"2026-05-16"`` — so the key
never matched and the heatmap rendered all-empty cells. The OverviewPage's
``${date}T00:00:00Z`` timestamp build also silently produced ``NaN``.

The fix returns ISO ``%Y-%m-%d``. These tests pin the wire format so a
future refactor cannot regress all three consumers at once
(`CoveragePage.CadenceHeatmap`, `TrendsPage.buildCadenceCells`,
`OverviewPage.ExecutionTrendChart`).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _execute_returning(rows: list) -> AsyncMock:
    """Build a fake ``db.execute`` whose return supports ``.fetchall()``."""
    res = MagicMock()
    res.fetchall = MagicMock(return_value=rows)
    return AsyncMock(return_value=res)


@pytest.mark.asyncio
async def test_trend_date_is_iso_yyyy_mm_dd() -> None:
    """A single trend point must come back with date ``"2026-05-16"``
    (10 chars, ISO) rather than the legacy locale-formatted ``"May 16"``."""
    from app.services.metrics_service import get_trend_data

    row = SimpleNamespace(
        day=datetime(2026, 5, 16, tzinfo=timezone.utc),
        passed=10, failed=2, skipped=1, broken=0, total=13, pass_rate=83.3,
    )
    db = AsyncMock()
    db.execute = _execute_returning([row])

    out = await get_trend_data(db, project_id="p1", days=7)

    assert len(out) == 1
    assert out[0]["date"] == "2026-05-16"
    assert _ISO_DATE_RE.match(out[0]["date"]), (
        f"trend date must be ISO yyyy-mm-dd; got {out[0]['date']!r}"
    )


@pytest.mark.asyncio
async def test_trend_dates_are_iso_for_every_row() -> None:
    """A multi-day trend window must return ISO dates for every point,
    not just the first. Pins the format across all rows in case a future
    refactor branches on ``len(rows) == 1`` or similar."""
    from app.services.metrics_service import get_trend_data

    rows = [
        SimpleNamespace(
            day=datetime(2026, 5, d, tzinfo=timezone.utc),
            passed=1, failed=0, skipped=0, broken=0, total=1, pass_rate=100.0,
        )
        for d in (10, 11, 12, 13, 14, 15, 16)
    ]
    db = AsyncMock()
    db.execute = _execute_returning(rows)

    out = await get_trend_data(db, project_id="p1", days=7)

    assert len(out) == 7
    for point in out:
        assert _ISO_DATE_RE.match(point["date"]), (
            f"row date {point['date']!r} is not ISO yyyy-mm-dd"
        )
    assert [p["date"] for p in out] == [
        "2026-05-10", "2026-05-11", "2026-05-12",
        "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16",
    ]
