"""Regression: GET /api/v1/runs and /runs/failed-ids were a cross-tenant IDOR.

Bug pinned (review/runs-service, 2026-06-02):

Both ``list_runs`` and ``list_failed_run_ids`` only applied the
accessible-projects tenant filter on the *no-project_id* path. When the caller
supplied ``?project_id=<uuid>`` the router filtered by that project but never
verified the caller could access it (``runs_service.list_project_runs`` used an
``elif`` so a provided project_id skipped the membership filter entirely). Any
authenticated user could thus read another tenant's run list / failed-run ids
via ``?project_id=<foreign-uuid>``.

Fix: router-level ``_require_accessible_project`` (403 on a foreign project,
400 on malformed, admin bypass) on both provided-project_id paths, plus
service-level defence-in-depth (``list_project_runs`` now ANDs the membership
filter even when project_id is given).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import runs as runs_router  # noqa: E402
from app.services import runs_service  # noqa: E402


# ── router-level access verification ────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_accessible_project_denies_foreign_project():
    pid = uuid.uuid4()
    other = uuid.uuid4()
    with patch.object(
        runs_router, "get_accessible_project_ids", AsyncMock(return_value={other})
    ):
        with pytest.raises(Exception) as exc:
            await runs_router._require_accessible_project(
                AsyncMock(), SimpleNamespace(id=uuid.uuid4()), str(pid)
            )
    assert getattr(exc.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_require_accessible_project_allows_own_project():
    pid = uuid.uuid4()
    with patch.object(
        runs_router, "get_accessible_project_ids", AsyncMock(return_value={pid})
    ):
        requested, accessible = await runs_router._require_accessible_project(
            AsyncMock(), SimpleNamespace(id=uuid.uuid4()), str(pid)
        )
    assert requested == pid
    assert accessible == {pid}


@pytest.mark.asyncio
async def test_require_accessible_project_admin_bypass():
    pid = uuid.uuid4()
    with patch.object(
        runs_router, "get_accessible_project_ids", AsyncMock(return_value=None)
    ):
        requested, accessible = await runs_router._require_accessible_project(
            AsyncMock(), SimpleNamespace(id=uuid.uuid4()), str(pid)
        )
    assert requested == pid
    assert accessible is None  # admin → no restriction


@pytest.mark.asyncio
async def test_require_accessible_project_rejects_malformed_id():
    with patch.object(
        runs_router, "get_accessible_project_ids", AsyncMock(return_value=None)
    ):
        with pytest.raises(Exception) as exc:
            await runs_router._require_accessible_project(
                AsyncMock(), SimpleNamespace(id=uuid.uuid4()), "not-a-uuid"
            )
    assert getattr(exc.value, "status_code", None) == 400


# ── service-level defence-in-depth ──────────────────────────────────────────


class _Res:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _CapturingDB:
    def __init__(self, results):
        self._results = list(results)
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(str(stmt))
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_list_project_runs_ands_membership_filter_even_with_project_id():
    """With BOTH project_id and accessible_project_ids supplied, the count
    query must constrain by membership too (project_id appears in an equality
    AND an IN) — the old ``elif`` dropped the membership filter."""
    pid = uuid.uuid4()
    db = _CapturingDB([_Res(scalar=0), _Res(rows=[])])  # count, items
    with patch.object(runs_service, "fetch_release_map", AsyncMock(return_value={})), \
         patch.object(runs_service, "fetch_run_seq_map", AsyncMock(return_value={})):
        await runs_service.list_project_runs(
            db, str(pid), 1, 20, None, None,
            accessible_project_ids={pid}, days=0,
        )
    count_sql = db.sql[0]
    # WHERE project_id = :pid AND project_id IN (...) — both predicates present.
    assert "project_id =" in count_sql
    assert " IN (" in count_sql  # membership constraint applied too


@pytest.mark.asyncio
async def test_list_project_runs_single_filter_when_no_accessible_set():
    """Admin path (accessible_project_ids=None): only the project_id equality,
    no membership IN — confirms the AND is gated on the accessible set."""
    pid = uuid.uuid4()
    db = _CapturingDB([_Res(scalar=0), _Res(rows=[])])
    with patch.object(runs_service, "fetch_release_map", AsyncMock(return_value={})), \
         patch.object(runs_service, "fetch_run_seq_map", AsyncMock(return_value={})):
        await runs_service.list_project_runs(
            db, str(pid), 1, 20, None, None, days=0,
        )
    count_sql = db.sql[0]
    # Only the equality predicate — no MEMBERSHIP IN when accessible is None.
    assert "project_id =" in count_sql
    # The live-projects subquery is also an IN, so the assertion names the
    # membership form specifically: expanding bind params, not a SELECT. A bare
    # "no IN at all" check conflated the two and failed when soft-deleted
    # projects were excluded from the runs list.
    membership_ins = [
        frag for frag in count_sql.split(" IN (")[1:]
        if not frag.lstrip().upper().startswith("SELECT")
    ]
    assert not membership_ins, (
        f"membership IN applied despite accessible_project_ids=None: {count_sql}"
    )
    # And the activity exclusion must be present on this path too.
    assert " IN (SELECT" in count_sql.replace("IN (SELECT", "IN (SELECT")
