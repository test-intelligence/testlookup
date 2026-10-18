"""W28 TCM-02: deprecated and archived cases leave the counts, not the listings.

* The duplicate review queue (its page, ``total`` and ``open_count``) leaves out
  a pair one of whose cases was retired after detection: read-time, so a
  reinstated case brings its pair back.
* Both evidence-gap kinds (``never_executed``, ``automation_vanished``) leave
  retired cases out: retiring a case is not a gap in its evidence.
* The Suites tab's manual count leaves them out (raw SQL, same statement count).
* A suite's own case listing keeps them, and its manual rows carry
  ``lifecycle_status`` so Link Suite can tell Active from the rest (the row's
  ``status`` prefers the last execution's result).

The PostgreSQL proof is ``tests/integration/test_retired_cases_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import ManagedTestCase  # noqa: E402
from app.routers import duplicates as duplicates_router  # noqa: E402
from app.services import test_suite_service  # noqa: E402
from app.services.test_case_lifecycle_service import RETIRED_STATES  # noqa: E402
from tests.test_duplicates_router import _FakeSession, _candidate, _case  # noqa: E402


class _RecordingSession(_FakeSession):
    """The duplicates router's fake, keeping every statement's text."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.texts: list[str] = []

    async def execute(self, stmt):
        self.texts.append(str(stmt).lower())
        return await super().execute(stmt)


def test_retired_states_are_the_two_retired_lifecycle_states():
    assert tuple(RETIRED_STATES) == ("deprecated", "archived")


# ── The duplicate queue ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("retired_status", ["deprecated", "archived"])
async def test_a_pair_with_a_retired_case_leaves_the_queue_and_both_counts(retired_status):
    project_id = uuid.uuid4()
    kept_a = _case(project_id, title="Login works", cid=uuid.uuid4())
    kept_b = _case(project_id, title="Login functions", cid=uuid.uuid4())
    live_pair = _candidate(project_id, kept_a.id, kept_b.id, score=0.95)
    retired = _case(project_id, title="Logout works", status=retired_status, cid=uuid.uuid4())
    partner = _case(project_id, title="Logout functions", cid=uuid.uuid4())
    retired_pair = _candidate(project_id, retired.id, partner.id, score=0.97)
    db = _RecordingSession(
        candidates=[live_pair, retired_pair],
        cases=[kept_a, kept_b, retired, partner],
    )

    resp = await duplicates_router.list_duplicate_candidates(
        project_id=project_id, band=None, status_filter="open", page=1, size=50,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )

    assert [item.id for item in resp.items] == [live_pair.id]
    assert resp.total == 1
    assert resp.open_count == 1
    # The total, the open count and the page each carry the retired-case EXISTS
    # (the case refs read that follows does not need it).
    queue_statements = [t for t in db.texts if "from duplicate_test_case_candidates" in t]
    assert len(queue_statements) == 3
    for text in queue_statements:
        assert "not (exists (select managed_test_cases.id" in text
        assert "managed_test_cases.status in" in text
        assert " or " not in text  # one correlated EXISTS, no OR across tables


@pytest.mark.asyncio
async def test_a_reinstated_case_brings_its_pair_back():
    project_id = uuid.uuid4()
    a = _case(project_id, status="deprecated", cid=uuid.uuid4())
    b = _case(project_id, cid=uuid.uuid4())
    pair = _candidate(project_id, a.id, b.id)
    db = _RecordingSession(candidates=[pair], cases=[a, b])
    first = await duplicates_router.list_duplicate_candidates(
        project_id=project_id, band=None, status_filter="open", page=1, size=50,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert (first.total, first.open_count, first.items) == (0, 0, [])

    a.status = "draft"  # what Reinstate leaves (the stored pair is untouched)
    db = _RecordingSession(candidates=[pair], cases=[a, b])
    again = await duplicates_router.list_duplicate_candidates(
        project_id=project_id, band=None, status_filter="open", page=1, size=50,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert (again.total, again.open_count) == (1, 1)
    assert [item.id for item in again.items] == [pair.id]


def test_the_retired_pair_filter_correlates_on_the_candidate_row():
    stmt = (
        duplicates_router.select(duplicates_router.DuplicateTestCaseCandidate)
        .where(~duplicates_router._pair_has_retired_case())
    )
    compiled = str(stmt.compile()).lower()
    assert "managed_test_cases.id in (duplicate_test_case_candidates.case_a_id, " \
        "duplicate_test_case_candidates.case_b_id)" in compiled
    # Correlated: the candidates table is not repeated inside the EXISTS.
    assert compiled.count("from duplicate_test_case_candidates") == 1


# ── The evidence gaps ────────────────────────────────────────────────────────


class _Result:
    def __init__(self, *, scalar=None, rows=None, scalars=None):
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars or []

    def scalar(self):
        return self._scalar

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))

    def fetchall(self):
        return list(self._rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["never_executed", "automation_vanished"])
async def test_both_evidence_gap_kinds_leave_retired_cases_out(kind):
    project_id = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(scalar=0), _Result(rows=[])])
    )

    rows, total = await test_suite_service.list_test_case_evidence_gaps(
        db, [project_id], kind=kind, page=1, size=25
    )

    assert (rows, total) == ([], 0)
    assert db.execute.await_count == 2  # still a count and a page
    for call in db.execute.await_args_list:
        compiled = call.args[0].compile()
        text = str(compiled)
        assert "managed_test_cases.status NOT IN" in text
        assert "managed_test_cases.project_id IN" in text
        retired_values = [
            value for value in compiled.params.values()
            if isinstance(value, (list, tuple)) and set(value) == set(RETIRED_STATES)
        ]
        assert retired_values, f"no RETIRED_STATES bind in {compiled.params}"


@pytest.mark.asyncio
async def test_an_all_projects_evidence_gap_read_leaves_retired_cases_out_too():
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(scalar=0), _Result(rows=[])])
    )
    await test_suite_service.list_test_case_evidence_gaps(db, None, kind="never_executed")
    assert "managed_test_cases.status NOT IN" in str(
        db.execute.await_args_list[1].args[0].compile()
    )


# ── The Suites tab's manual count and a suite's case listing ─────────────────


class _NoopSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_the_suites_manual_count_leaves_retired_cases_out(monkeypatch):
    from app.routers.test_management_exports import list_test_suites
    import app.services.suite_review_service as suite_reviews

    async def _no_owners(_db, _project_id, _names):
        return {}

    monkeypatch.setattr(suite_reviews, "list_suite_owners", _no_owners)
    manual = [SimpleNamespace(
        suite_name="Checkout", test_count=2, passed_count=0, failed_count=0, last_run_at=None,
    )]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(rows=[]), _Result(rows=[]), _Result(rows=manual)]),
        rollback=AsyncMock(),
        begin_nested=lambda: _NoopSavepoint(),
    )

    result = await list_test_suites(
        project_id=uuid.uuid4(), db=db,
        current_user=SimpleNamespace(id=uuid.uuid4(), role="ADMIN"),
    )

    # The manual count is still the third statement (the scripted order of
    # tests/test_test_management_suites_unique_count.py holds).
    manual_sql = " ".join(str(db.execute.await_args_list[2].args[0]).split())
    assert "FROM managed_test_cases" in manual_sql
    assert "AND status NOT IN ('deprecated', 'archived')" in manual_sql
    assert {s["suite_name"]: s["test_count"] for s in result} == {"Checkout": 2}


@pytest.mark.asyncio
async def test_a_suites_manual_rows_carry_lifecycle_status_and_retired_rows_stay_listed(monkeypatch):
    from app.core import deps
    from app.routers.test_management_exports import get_suite_test_cases

    async def _scope(_db, _user, requested):
        return (uuid.UUID(requested) if requested else None), None

    monkeypatch.setattr(deps, "resolve_project_scope", _scope)
    project_id = uuid.uuid4()
    executed = ManagedTestCase(
        id=uuid.uuid4(), project_id=project_id, title="Pay by card", suite_name="Checkout",
        status="active", last_execution_status="FAILED", feature_area=None,
    )
    retired = ManagedTestCase(
        id=uuid.uuid4(), project_id=project_id, title="Pay by cheque", suite_name="Checkout",
        status="deprecated", last_execution_status=None, feature_area=None,
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Result(scalar=0),                       # automation total
        _Result(rows=[]),                        # automation page
        _Result(scalar=2),                       # manual total
        _Result(scalars=[executed, retired]),    # manual page
    ]))

    page = await get_suite_test_cases(
        suite_name="Checkout", project_id=project_id, page=1, size=25, limit=None,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4(), role="ADMIN"),
    )

    by_title = {item["test_name"]: item for item in page["items"]}
    assert by_title["Pay by card"]["status"] == "FAILED"  # unchanged: the last result first
    assert by_title["Pay by card"]["lifecycle_status"] == "active"
    assert by_title["Pay by cheque"]["lifecycle_status"] == "deprecated"  # listed, not counted
    assert page["total"] == 2
