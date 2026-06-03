"""Regression pins for the suite_sync batching refactor
(perf/suite-sync-batch-membership).

``sync_suite_membership`` previously called ``_sync_one_suite`` per suite, and
each call issued 3 SELECTs (managed cases, existing memberships, deleted bucket)
— ``1 + 3N`` queries on the ingestion critical path. The three lookups are now
batched across ALL suites in one query each (``4`` total, constant in N), with
``_sync_one_suite`` doing no DB reads.

These tests pin:
  1. The round-trip count is constant (4) regardless of suite count.
  2. Behavior is preserved across a multi-suite run: additions, unchanged rows,
     and deletions (with the membership moved to the ``<suite>-deleted`` bucket).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


def _tc(suite, fp, name, cls="Cls"):
    return SimpleNamespace(
        suite_name=suite, test_fingerprint=fp, test_name=name, class_name=cls,
    )


def _sm(fp, suite, name, cls="Cls", status="active", last_seen=None):
    return SimpleNamespace(
        id=uuid.uuid4(), test_fingerprint=fp, suite_name=suite, test_name=name,
        class_name=cls, status=status, last_seen_run_id=last_seen,
        review_tag=None, deleted_at_run_id=None,
    )


def _fake_db(*, run_cases, managed=None, existing=None, deleted=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=[
        _Res(run_cases),          # 1. run test cases
        _Res(managed or []),      # 2. managed cases (batched)
        _Res(existing or []),     # 3. existing memberships (batched)
        _Res(deleted or []),      # 4. deleted-bucket memberships (batched)
    ])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_query_count_is_constant_regardless_of_suite_count():
    from app.services.suite_sync_service import sync_suite_membership

    # 3 suites, each one new test → previously 1 + 3*3 = 10 queries; now 4.
    run_cases = [
        _tc("A", "a1", "testA1"),
        _tc("B", "b1", "testB1"),
        _tc("C", "c1", "testC1"),
    ]
    db = _fake_db(run_cases=run_cases)

    summaries = await sync_suite_membership(db, uuid.uuid4(), uuid.uuid4())

    assert db.execute.await_count == 4          # constant, NOT 1 + 3N
    assert sum(s["added_count"] for s in summaries) == 3
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_multi_suite_add_unchanged_and_delete_preserved():
    from app.models.postgres import SuiteMembership, SuiteMembershipEvent, TestCaseAuditLog
    from app.services.suite_sync_service import sync_suite_membership

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    other_run = uuid.uuid4()

    run_cases = [
        _tc("Smoke", "s1", "testS1"),     # new → added
        _tc("Reg", "r1", "testR1"),       # existing + matches → unchanged
    ]
    existing = [
        _sm("r1", "Reg", "testR1", status="active", last_seen=other_run),  # unchanged
        _sm("r2", "Reg", "testR2", status="active", last_seen=other_run),  # absent → deleted
    ]
    db = _fake_db(run_cases=run_cases, existing=existing)

    summaries = await sync_suite_membership(db, project_id, run_id)

    assert db.execute.await_count == 4
    by_suite = {s["suite_name"]: s for s in summaries}
    assert by_suite["Smoke"]["added_count"] == 1
    assert by_suite["Reg"]["unchanged_count"] == 1
    assert by_suite["Reg"]["deleted_count"] == 1

    # r1 stamped with this run, still active; r2 moved to the deleted bucket.
    r1 = next(m for m in existing if m.test_fingerprint == "r1")
    r2 = next(m for m in existing if m.test_fingerprint == "r2")
    assert r1.last_seen_run_id == run_id and r1.status == "active"
    assert r2.status == "needs_review" and r2.suite_name == "Reg-deleted"

    added = db.add.call_args_list
    new_members = [c.args[0] for c in added if isinstance(c.args[0], SuiteMembership)]
    events = [c.args[0] for c in added if isinstance(c.args[0], SuiteMembershipEvent)]
    audits = [c.args[0] for c in added if isinstance(c.args[0], TestCaseAuditLog)]
    assert len(new_members) == 1                       # only Smoke/s1
    assert new_members[0].test_fingerprint == "s1"
    assert {e.event_type for e in events} == {"added", "deleted"}
    assert len(audits) == 1                            # deletion audit row
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_run_returns_no_summaries_and_no_commit():
    from app.services.suite_sync_service import sync_suite_membership

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Res([])),  # no test cases in the run
        add=MagicMock(), commit=AsyncMock(), rollback=AsyncMock(),
    )
    summaries = await sync_suite_membership(db, uuid.uuid4(), uuid.uuid4())
    assert summaries == []
    assert db.execute.await_count == 1   # only the run-cases fetch
    db.commit.assert_not_awaited()
