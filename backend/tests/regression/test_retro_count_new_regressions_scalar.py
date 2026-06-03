"""Regression pin for the Phase AUTO perf fix (auto/perf-20260603-2212).

``retro_digest_service._count_new_regressions`` materialised the distinct
regressed fingerprints (`SELECT DISTINCT test_fingerprint ...`) and then
``len({row[0] ... if row[0]})`` in Python. The second query is now a SQL
``COUNT(DISTINCT test_fingerprint)`` consumed via ``.scalar()`` — no row
transfer / Python dedup. Behavior is identical: ``prev_passed_fps`` (the IN-list)
already holds only truthy fingerprints and COUNT(DISTINCT) skips NULL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


@pytest.mark.asyncio
async def test_count_new_regressions_uses_scalar_count():
    from app.services import retro_digest_service as svc

    # 1st execute → prev-passed fingerprints (None/"" dropped by `if row[0]`),
    # 2nd execute → the COUNT(DISTINCT) scalar for current-week regressions.
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Rows([("fpA",), ("fpB",), (None,), ("",)]),
        _Scalar(1),
    ]))

    result = await svc._count_new_regressions(db, uuid.uuid4(), datetime.now(timezone.utc))

    assert result == 1
    assert db.execute.await_count == 2  # prev-passed fetch + one scalar count


@pytest.mark.asyncio
async def test_count_new_regressions_short_circuits_when_no_prev_passed():
    from app.services import retro_digest_service as svc

    # All prev-week fingerprints are falsy → empty set → early return 0; the
    # current-week count query is never issued.
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Rows([(None,), ("",)])]))

    result = await svc._count_new_regressions(db, uuid.uuid4(), datetime.now(timezone.utc))

    assert result == 0
    assert db.execute.await_count == 1  # second (count) query skipped
