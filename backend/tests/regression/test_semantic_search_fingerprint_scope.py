"""Regression: semantic_search failure_count leaked cross-tenant via fingerprint.

Bug pinned (review/semantic-search-service, 2026-06-02):

``semantic_search`` computes ``failure_count`` by outer-joining a second
``TestCase`` alias on ``test_fingerprint`` only. ``make_test_fingerprint`` is
``sha256(f"{class}::{name}")[:16]`` — NOT project-salted — and ``TestCase`` has
no project_id column, so a same-named test in another tenant was counted into
the failure_count, inflating the number shown in results and skewing hybrid
ranking (failure_count feeds ``compute_hybrid_score`` + ``build_match_reasons``).

Fix: join a ``TestRun`` alias for the history rows and scope the filtered count
to the matched row's own project (``history_run.project_id == TestRun.project_id``).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import semantic_search as svc  # noqa: E402


class _Res:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


class _CapturingDB:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        return _Res(rows=[])


@pytest.mark.asyncio
async def test_failure_count_is_project_scoped():
    tc_id = uuid.uuid4()

    fake_collection = MagicMock()
    fake_collection.query = MagicMock(
        return_value={
            "ids": [[str(tc_id)]],
            "distances": [[0.2]],
            "metadatas": [[{"created_at": ""}]],
        }
    )

    db = _CapturingDB()
    with patch.object(
        svc, "_get_or_create_collection", AsyncMock(return_value=fake_collection)
    ):
        items, total, pages = await svc.semantic_search(
            db, "login failure", page=1, size=20, project_id=str(uuid.uuid4()),
        )

    # One PG query was issued (the failure_count detail fetch).
    assert db.sql, "no PG query executed"
    pg_sql = db.sql[0]
    # The filtered count must correlate the history run's project to the matched
    # row's project — proving the fingerprint join is no longer global.
    assert "FILTER (WHERE" in pg_sql
    assert "test_runs_1.project_id = test_runs.project_id" in pg_sql
    # No rows came back from the (empty) DB → empty result, not a 500.
    assert (items, total, pages) == ([], 0, 0)


# ── hybrid_search must not use the injected session concurrently ─────────────
#
# Bug pinned (same review): hybrid_search scheduled semantic_search with
# asyncio.create_task while awaiting search_test_cases_query — both touch the
# SAME injected AsyncSession, so two db.execute() calls could overlap on one
# session → SQLAlchemy "another operation is in progress" (intermittent). Fix:
# run them sequentially on the shared session.


class _ConcurrencyGuardDB:
    """Records the peak number of overlapping execute() calls."""

    def __init__(self):
        self._active = 0
        self.max_concurrent = 0

    async def execute(self, *a, **k):
        import asyncio

        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        await asyncio.sleep(0)  # yield so any concurrent coroutine can interleave
        self._active -= 1
        return _Res(rows=[])


@pytest.mark.asyncio
async def test_hybrid_search_does_not_use_session_concurrently():
    import app.services.search_service as search_service

    async def fake_semantic(db, *a, **k):
        await db.execute("semantic")
        return [], 0, 0

    async def fake_keyword(db, *a, **k):
        await db.execute("keyword")
        return [], 0, 0

    db = _ConcurrencyGuardDB()
    with patch.object(svc, "semantic_search", fake_semantic), \
         patch.object(search_service, "search_test_cases_query", fake_keyword):
        items, total, pages = await svc.hybrid_search(
            db, "q", page=1, size=10, project_id=str(uuid.uuid4()),
        )

    assert db.max_concurrent == 1, (
        f"hybrid_search ran {db.max_concurrent} concurrent db.execute() calls on "
        "the shared session — they must be sequential"
    )
    assert (items, total, pages) == ([], 0, 0)
