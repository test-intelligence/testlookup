"""D-17: the global search's test-case adapter returns one hit per logical test.

It returned one hit per EXECUTION (``entity_id = test_cases.id``, newest
first, limit 50), so a test that ran in 52 runs filled the page on its own and
the result count meant executions. The keyword path already de-duplicated on
``(project, test_fingerprint)``; the global adapter now does the same over its
two bounded halves, keeps each test's newest execution, and says how many runs
of the window ran it. The live-database proof is
``tests/integration/test_global_search_filters_postgres.py`` (``test_one_test_run_three_times_is_one_hit_at_its_newest_run``).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import global_search_service as gs

PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _ScriptedDB:
    """Answers each ``execute`` with the next scripted row list, recording the statements."""

    def __init__(self, *answers):
        self.statements = []
        self._answers = list(answers)

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return _Result(self._answers.pop(0) if self._answers else [])


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.psycopg2.dialect(), compile_kwargs={"literal_binds": True})).lower()


def _case(name: str, fingerprint: str, run_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), test_name=name, suite_name="Checkout", status="FAILED",
        test_run_id=run_id, test_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_the_hits_are_distinct_per_project_and_fingerprint_newest_first():
    db = _ScriptedDB([])
    await gs._search_test_cases(db, "checkout", None, None)
    [stmt] = db.statements  # no rows -> no count statement
    sql = _sql(stmt)
    assert "distinct on (test_runs.project_id, test_cases.test_fingerprint)" in sql
    assert "order by test_runs.project_id, test_cases.test_fingerprint, test_cases.created_at desc" in sql
    # The page is still the newest first, bounded.
    assert sql.rstrip().endswith("order by test_cases.created_at desc \n limit 50")


@pytest.mark.asyncio
async def test_each_hit_says_how_many_runs_ran_the_test():
    newest_run = uuid.uuid4()
    pays = _case("test_pays", "fp-pays", newest_run)
    once = _case("test_once", "fp-once", newest_run)
    db = _ScriptedDB(
        [(pays, PROJECT), (once, PROJECT)],
        [(PROJECT, "fp-pays", 3), (PROJECT, "fp-once", 1)],
    )
    hits = await gs._search_test_cases(db, "checkout", None, None)

    assert [h["title"] for h in hits] == ["test_pays", "test_once"]
    assert hits[0]["entity_id"] == str(pays.id)
    assert hits[0]["navigation_url"] == f"/runs/{newest_run}/tests/{pays.id}"
    assert hits[0]["metadata"]["executions"] == 3
    assert hits[0]["subtitle"] == "Checkout · FAILED · 3 runs"
    assert hits[1]["metadata"]["executions"] == 1
    assert hits[1]["subtitle"] == "Checkout · FAILED"

    # The count is scoped to the page's own (project, fingerprint) pairs.
    count_sql = _sql(db.statements[1])
    assert "count(*)" in count_sql
    assert "(test_runs.project_id, test_cases.test_fingerprint) in" in count_sql
    assert "group by test_runs.project_id, test_cases.test_fingerprint" in count_sql
