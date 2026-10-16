"""The Runs toolbar's filters and ``ai_recommendation`` on real PostgreSQL (W07 FAIL-05).

* ``q``: contains on build / branch / CI job, prefix on the commit hash, with
  ``%`` ``_`` ``\\`` matched literally;
* ``branches``: exact names, OR;
* ``recommendation``: the column AND a servable snapshot; ``none`` the rest;
* parity: for the ``CASES`` payloads saved through ``save_snapshot``, the runs
  ``recommendation=X`` lists are exactly those whose batch item gives X;
* the column's writers (``save_snapshot`` / ``invalidate`` / ``stage_invalidate``
  set or clear it, ``mark_stale`` keeps it, none moves ``updated_at``; the
  pending-candidates return writes nothing);
* ``total`` equals the rows for every combination (one shared filter list);
* ``list_run_branches``: newest last run first, all time, ``total`` beyond the cap.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import itertools
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import intelligence_snapshot_service as snapshots
from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION
from app.services.run_verdict import RECOMMENDATION_FILTER_VALUES, ai_recommendation_of
from app.services.run_verdict_service import batch_verdict_inputs
from app.services.runs_service import list_project_runs, list_run_branches
from tests.test_run_verdict import CASES

pytest.importorskip("asyncpg")

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def projects(engine):
    created: list[uuid.UUID] = []

    async def make() -> uuid.UUID:
        pid = uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, 'w07a-filters', :s, true)"),
                {"id": pid, "s": f"w07a-f-{pid.hex}"},
            )
        created.append(pid)
        return pid

    yield make
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
                           {"ids": [str(i) for i in created]})


async def _run(engine, project_id, build, *, branch="feature/x", job="job-x", commit="fff000", rec=None,
               days_ago=0.0) -> uuid.UUID:
    rid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, branch, commit_hash, status, "
            "ingestion_source, ai_recommendation, created_at, updated_at) "
            "VALUES (:id, :p, :b, :j, :br, :c, 'FAILED', 'unknown', :rec, :at, :at)"
        ), {"id": rid, "p": project_id, "b": build, "j": job, "br": branch, "c": commit, "rec": rec,
            "at": NOW - timedelta(days=days_ago)})
    return rid


async def _snapshot(engine, run_id, payload, *, version=CURRENT_SCHEMA_VERSION) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO run_intelligence_snapshots (id, run_id, schema_version, payload, fallback_used, stale) "
            "VALUES (gen_random_uuid(), :r, :v, CAST(:p AS json), false, false)"
        ), {"r": run_id, "v": version, "p": json.dumps(payload)})


async def _list(engine, project_id, **toolbar):
    async with AsyncSession(engine) as db:
        items, total, _ = await list_project_runs(db, str(project_id), 1, 500, days=None, **toolbar)
    return {item["build_number"] for item in items}, total


async def test_q_contains_on_build_branch_and_job_with_like_characters_literal(engine, projects):
    p = await projects()
    for build in ("1234", "build-1234-x", "123", "50%", "500", "a_b", "ab", "back\\slash", "backslash"):
        await _run(engine, p, build)
    assert (await _list(engine, p, q="1234"))[0] == {"1234", "build-1234-x"}
    assert (await _list(engine, p, q="50%"))[0] == {"50%"}
    assert (await _list(engine, p, q="_"))[0] == {"a_b"}
    assert (await _list(engine, p, q="k\\s"))[0] == {"back\\slash"}
    assert (await _list(engine, p, q="BUILD-1234"))[0] == {"build-1234-x"}, "case-insensitive"


async def test_q_reads_branch_and_job_and_a_commit_prefix(engine, projects):
    p = await projects()
    await _run(engine, p, "b1", branch="release/2.4")
    await _run(engine, p, "b2", job="nightly-payments")
    await _run(engine, p, "b3", commit="ab12f00d")
    await _run(engine, p, "b4", commit="00ab12ff")
    assert (await _list(engine, p, q="2.4"))[0] == {"b1"}
    assert (await _list(engine, p, q="payments"))[0] == {"b2"}
    assert (await _list(engine, p, q="ab12"))[0] == {"b3"}, "the commit is a PREFIX match"


async def test_branches_are_exact_and_ored(engine, projects):
    p = await projects()
    await _run(engine, p, "m", branch="main")
    await _run(engine, p, "r", branch="release/1.2")
    await _run(engine, p, "M", branch="Main")
    await _run(engine, p, "o", branch="mainline")
    assert (await _list(engine, p, branches=("main", "release/1.2")))[0] == {"m", "r"}


async def test_a_recommendation_needs_a_servable_snapshot(engine, projects):
    p = await projects()
    current = await _run(engine, p, "current", rec="NO_GO")
    await _snapshot(engine, current, CASES[3]["full"])
    old = await _run(engine, p, "old", rec="NO_GO")
    await _snapshot(engine, old, CASES[3]["full"], version=CURRENT_SCHEMA_VERSION - 1)
    await _run(engine, p, "no-snapshot", rec="NO_GO")  # a column without its snapshot is no verdict
    plain = await _run(engine, p, "plain")
    assert plain
    assert await _list(engine, p, recommendation="NO_GO") == ({"current"}, 1)
    assert await _list(engine, p, recommendation="none") == ({"old", "no-snapshot", "plain"}, 3)
    assert await _list(engine, p, recommendation="GO") == (set(), 0)


async def test_the_filter_lists_exactly_the_runs_whose_pill_says_so(engine, projects):
    p = await projects()
    ids = []
    for index, case in enumerate(CASES):
        rid = await _run(engine, p, f"case-{index}")
        async with AsyncSession(engine) as db:
            await snapshots.save_snapshot(db, rid, case["full"])
        ids.append(rid)
    async with AsyncSession(engine) as db:
        batch = await batch_verdict_inputs(db, ids, accessible={p})
    by_pill: dict[str | None, set[str]] = {}
    for item, index in zip(batch["items"], range(len(CASES))):
        by_pill.setdefault(ai_recommendation_of(item["verdict_inputs"]), set()).add(f"case-{index}")
    for value in ("GO", "CONDITIONAL_GO", "NO_GO"):
        assert (await _list(engine, p, recommendation=value))[0] == by_pill.get(value, set()), value
    assert (await _list(engine, p, recommendation="none"))[0] == by_pill.get(None, set())
    # And the column equals the case table's expectation, row by row.
    async with engine.connect() as conn:
        stored = dict((await conn.execute(text(
            "SELECT build_number, ai_recommendation FROM test_runs WHERE project_id = :p"
        ), {"p": p})).all())
    assert stored == {f"case-{i}": case["recommendation"] for i, case in enumerate(CASES)}


async def _column(engine, run_id) -> tuple:
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT ai_recommendation, updated_at FROM test_runs WHERE id = :r"
        ), {"r": run_id})).one()


async def test_the_writers_set_and_clear_it_without_moving_updated_at(engine, projects):
    p = await projects()
    rid = await _run(engine, p, "writers", days_ago=3)
    _, before = await _column(engine, rid)

    async with AsyncSession(engine) as db:
        await snapshots.save_snapshot(db, rid, CASES[0]["full"])
    assert await _column(engine, rid) == ("GO", before)

    async with AsyncSession(engine) as db:
        assert await snapshots.mark_stale(db, rid) is True
        await db.commit()
    assert await _column(engine, rid) == ("GO", before), "mark_stale keeps it (a stale snapshot is served)"

    async with AsyncSession(engine) as db:
        assert await snapshots.invalidate(db, rid) is True
    assert await _column(engine, rid) == (None, before)

    async with AsyncSession(engine) as db:
        await snapshots.save_snapshot(db, rid, CASES[3]["full"])
    assert await _column(engine, rid) == ("NO_GO", before)
    async with AsyncSession(engine) as db:
        assert await snapshots.stage_invalidate(db, rid) is True
        await db.rollback()
    assert await _column(engine, rid) == ("NO_GO", before), "stage-only: a rolled-back caller keeps both"
    async with AsyncSession(engine) as db:
        assert await snapshots.stage_invalidate(db, rid) is True
        await db.commit()
    assert await _column(engine, rid) == (None, before)

    async with AsyncSession(engine) as db:
        await snapshots.save_snapshot(db, rid, {**CASES[0]["full"], "defect_candidates_pending": True})
    assert await _column(engine, rid) == (None, before), "a pending payload writes nothing"
    async with engine.connect() as conn:
        count = (await conn.execute(text(
            "SELECT count(*) FROM run_intelligence_snapshots WHERE run_id = :r"
        ), {"r": rid})).scalar_one()
    assert count == 0


async def test_total_counts_the_rows_for_every_combination(engine, projects):
    p = await projects()
    specs = [
        ("1001", "main", "NO_GO"), ("1002", "main", "GO"), ("1003", "release/1.2", "NO_GO"),
        ("2001", "main", None), ("2002", "dev", "GO"),
    ]
    for build, branch, rec in specs:
        rid = await _run(engine, p, build, branch=branch, rec=rec)
        if rec:
            await _snapshot(engine, rid, CASES[0]["full"])
    for q, branches, recommendation in itertools.product(
        (None, "100", "2"), ((), ("main",), ("main", "dev")), (None, *RECOMMENDATION_FILTER_VALUES),
    ):
        builds, total = await _list(engine, p, q=q, branches=branches, recommendation=recommendation)
        assert total == len(builds), (q, branches, recommendation)


async def test_branches_newest_first_all_time_and_counted_beyond_the_cap(engine, projects):
    p = await projects()
    await _run(engine, p, "old-main", branch="main", days_ago=40)
    await _run(engine, p, "new-main", branch="main", days_ago=1)
    await _run(engine, p, "rel", branch="release/2.4", days_ago=2)
    await _run(engine, p, "blank", branch="   ", days_ago=0)
    await _run(engine, p, "ancient", branch="legacy", days_ago=100)
    async with AsyncSession(engine) as db:
        window = await list_run_branches(db, p, days=30)
        forever = await list_run_branches(db, p, days=None)
    assert window["items"] == [
        {"branch": "main", "runs": 1, "last_run_at": window["items"][0]["last_run_at"]},
        {"branch": "release/2.4", "runs": 1, "last_run_at": window["items"][1]["last_run_at"]},
    ]
    assert (window["total"], window["truncated"], window["window_days"]) == (2, False, 30)
    assert [(i["branch"], i["runs"]) for i in forever["items"]] == [("main", 2), ("release/2.4", 1), ("legacy", 1)]
    assert forever["window_days"] is None

    many = await projects()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, jenkins_job, branch, status, ingestion_source, "
            "created_at) SELECT gen_random_uuid(), :p, 'b' || g, 'w07a', 'branch-' || lpad(g::text, 3, '0'), "
            "'PASSED', 'unknown', now() - make_interval(mins => g) FROM generate_series(1, 120) AS g"
        ), {"p": many})
    async with AsyncSession(engine) as db:
        capped = await list_run_branches(db, many, days=None)
    assert len(capped["items"]) == 100 and capped["truncated"] is True and capped["total"] == 120
    assert capped["items"][0]["branch"] == "branch-001"
