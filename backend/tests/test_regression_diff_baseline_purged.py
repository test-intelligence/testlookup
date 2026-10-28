"""W22a F-7: the run's Changes tab says when the retention purge removed its baseline.

``compute_regression_diff`` reads THIS run's ``run_baselines`` row once,
before it chooses a comparison baseline, and BOTH of its answers (a baseline
found, or none) carry ``baseline_purged``: ``{"build_number", "purged_at"}``
when ``baseline_purged_at`` is stamped, else ``None``. Read-only.

The session double answers the diff's own reads from a small world and
records every statement, so the tests also pin the read's shape (one
statement on ``run_baselines`` keyed by the run, filtered on a stamped row)
and that nothing is written.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import commit_attribution_service, regression_diff_service


class _Rows:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._rows)


class _Session:
    """Answers the diff's reads; ``baseline_rows`` is what the run's
    ``run_baselines`` read returns (already filtered on a stamped row)."""

    def __init__(self, *, run, baseline, baseline_rows):
        self.run, self.baseline, self.baseline_rows = run, baseline, baseline_rows
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.added: list = []

    def add(self, obj):  # pragma: no cover - a write would fail the test below
        self.added.append(obj)

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.statements.append(sql)
        self.params.append(dict(compiled.params))
        if "FROM run_baselines" in sql:
            return _Rows(rows=self.baseline_rows)
        if "FROM test_runs" in sql and "test_runs.pass_rate >=" in sql:
            return _Rows(self.baseline)
        if "FROM test_cases" in sql:
            return _Rows(rows=[])
        raise AssertionError(f"unexpected statement: {sql}")


@pytest.fixture(autouse=True)
def _no_commit_range(monkeypatch):
    """The stored commit range and its status are W10's; stub them flat."""

    async def get_commit_range(_db, _run):
        return {"commits": []}

    async def status(_db, _run, _serialized):
        return SimpleNamespace(model_dump=lambda mode=None: {"reason": "no_commits"})

    monkeypatch.setattr(commit_attribution_service, "get_commit_range", get_commit_range)
    monkeypatch.setattr(regression_diff_service, "commit_range_status", status)


def _run():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), pass_rate=80.0, created_at=now, build_number="4480",
    )


def _baseline():
    return SimpleNamespace(id=uuid.uuid4(), pass_rate=100.0, build_number="4475")


def _stamp():
    # Built inside the test (never a module constant): the purge date.
    return datetime.now(timezone.utc) - timedelta(days=3)


@pytest.mark.asyncio
async def test_baseline_found_answer_carries_the_purged_fact():
    run, purged_at = _run(), _stamp()
    db = _Session(run=run, baseline=_baseline(), baseline_rows=[("4471", purged_at)])

    diff = await regression_diff_service.compute_regression_diff(run, db)

    assert diff["baseline_available"] is True
    assert diff["baseline_purged"] == {"build_number": "4471", "purged_at": purged_at.isoformat()}
    assert db.added == []


@pytest.mark.asyncio
async def test_no_baseline_answer_carries_the_purged_fact():
    run, purged_at = _run(), _stamp()
    db = _Session(run=run, baseline=None, baseline_rows=[("4471", purged_at)])

    diff = await regression_diff_service.compute_regression_diff(run, db)

    assert diff["baseline_available"] is False
    assert diff["baseline_purged"] == {"build_number": "4471", "purged_at": purged_at.isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False], ids=["baseline-found", "no-baseline"])
async def test_both_answers_say_null_when_nothing_was_purged(found):
    run = _run()
    db = _Session(run=run, baseline=_baseline() if found else None, baseline_rows=[])

    diff = await regression_diff_service.compute_regression_diff(run, db)

    assert diff["baseline_available"] is found
    assert "baseline_purged" in diff
    assert diff["baseline_purged"] is None


@pytest.mark.asyncio
async def test_a_purged_baseline_without_a_build_number_reads_null_build():
    run, purged_at = _run(), _stamp()
    db = _Session(run=run, baseline=None, baseline_rows=[(None, purged_at)])

    diff = await regression_diff_service.compute_regression_diff(run, db)

    assert diff["baseline_purged"] == {"build_number": None, "purged_at": purged_at.isoformat()}


@pytest.mark.asyncio
async def test_the_read_is_one_statement_on_this_runs_stamped_row_before_the_baseline_choice():
    run = _run()
    db = _Session(run=run, baseline=_baseline(), baseline_rows=[])

    await regression_diff_service.compute_regression_diff(run, db)

    reads = [i for i, sql in enumerate(db.statements) if "FROM run_baselines" in sql]
    assert len(reads) == 1
    sql, params = db.statements[reads[0]], db.params[reads[0]]
    assert "run_baselines.run_id = " in sql
    assert "run_baselines.baseline_purged_at IS NOT NULL" in sql
    assert params["run_id_1"] == run.id
    choice = next(i for i, s in enumerate(db.statements) if "test_runs.pass_rate >=" in s)
    assert reads[0] < choice
