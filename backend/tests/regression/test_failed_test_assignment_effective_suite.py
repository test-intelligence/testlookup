"""Regression pins for the failed_test_assignment_service review
(review/failed-test-assignment).

Fix: owner resolution keyed only on ``tc.suite_name`` and mapped a NULL
``suite_name`` to the project's DEFAULT suite. Live-stream runs from older SDKs
leave ``tc.suite_name`` NULL but carry the real label on
``TestRun.primary_suite_name`` — so a FAILED test in such a run was assigned via
the default-suite owner / QA-lead pool, bypassing the explicit
``TestSuiteOwner`` configured for the run's actual suite. The resolver now folds
``primary_suite_name`` into the suite lookup (the recurring effective-suite
pattern: consider BOTH tc.suite_name AND tr.primary_suite_name).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression


# ── pure helpers ────────────────────────────────────────────────────────────

def test_pick_pool_member_is_deterministic_and_salted():
    from app.services.failed_test_assignment_service import _pick_pool_member

    pool = [uuid.uuid4() for _ in range(4)]
    a = _pick_pool_member(pool, "fp-123", salt="projA")
    assert a == _pick_pool_member(pool, "fp-123", salt="projA")  # stable
    assert a in pool
    # empty pool → None; no fingerprint → first member (legacy rows)
    assert _pick_pool_member([], "fp") is None
    assert _pick_pool_member(pool, None) == pool[0]
    # salt changes the bucket independently (at least one fingerprint differs)
    differing = any(
        _pick_pool_member(pool, f"fp-{i}", "A") != _pick_pool_member(pool, f"fp-{i}", "B")
        for i in range(20)
    )
    assert differing


def test_actionable_status_values():
    from app.services.failed_test_assignment_service import (
        ACTIONABLE_STATUSES,
        _actionable_status_values,
    )

    assert _actionable_status_values() == ACTIONABLE_STATUSES
    assert set(_actionable_status_values()) == {"FAILED", "BROKEN"}


# ── end-to-end resolver: effective-suite via primary_suite_name ─────────────

class _R:
    def __init__(self, all_=None, first_=None, scalar_=None):
        self._all = all_ or []
        self._first = first_
        self._scalar = scalar_

    def all(self):
        return self._all

    def first(self):
        return self._first

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
    """Scripts SELECT results in order; records UPDATE statements separately."""

    def __init__(self, selects):
        self._selects = list(selects)
        self._i = 0
        self.updates = []

    async def execute(self, stmt, *a, **k):
        if getattr(stmt, "is_update", False):
            self.updates.append(stmt)
            return _R()
        r = self._selects[self._i]
        self._i += 1
        return r


@pytest.mark.asyncio
async def test_failure_with_null_suite_uses_run_primary_suite_owner():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    suite_owner = uuid.uuid4()

    # failure: NULL suite_name (live-stream old SDK), but the run's
    # primary_suite_name = "Smoke", which has an explicit TestSuiteOwner.
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name=None,
        assigned_to_user_id=None, test_fingerprint="fp-1",
    )]
    selects = [
        _R(all_=failures),                                   # 1. failures
        _R(first_=(None, None)),                             # 2. project default/manager
        _R(all_=[]),                                         # 3. QA-lead/admin pool (empty)
        _R(scalar_="Smoke"),                                 # 4. run.primary_suite_name (the fix)
        _R(all_=[SimpleNamespace(suite_name="Smoke", owner_user_id=suite_owner)]),  # 5. owner rows
    ]
    db = _DB(selects)

    counts = await assign_failed_tests_to_suite_owners(db, project_id, run_id)

    # Assigned via the Smoke suite owner — NOT left unassigned, and the
    # default-suite path was never taken (no get_or_create_default_suite call,
    # which would have needed an extra unscripted SELECT).
    assert counts == {"assigned": 1, "already_assigned": 0, "unassigned": 0}
    assert len(db.updates) == 1


@pytest.mark.asyncio
async def test_already_assigned_rows_are_preserved():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke",
        assigned_to_user_id=uuid.uuid4(),  # human reassignment already present
        test_fingerprint="fp-1",
    )]
    selects = [
        _R(all_=failures),               # failures
        _R(first_=(None, None)),         # project fields
        _R(all_=[]),                     # pool
        _R(scalar_=None),                # run.primary_suite_name
        _R(all_=[]),                     # owner rows (suite_names_in_run = {"Smoke"})
    ]
    db = _DB(selects)

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())

    assert counts == {"assigned": 0, "already_assigned": 1, "unassigned": 0}
    assert db.updates == []  # never overwrites an existing assignment
