"""``compute_suite_trend``'s raw SQL against real Postgres: exact day counts per effective suite.

The suite trend chart (Test Management, and its exports) reads
``suite_history_service.compute_suite_trend``. Every other test patches it
out, and the function turns any SQL error into ``[]`` -- a broken query reads
as "no runs", not as an error. So this module asserts real, non-empty numbers.

Project P, suite names ``api`` and ``ui``:

* ``R1`` (two days ago) -- an upload through the real ingest path: three
  ``ui`` rows (PASSED, PASSED, SKIPPED) and two ``api`` rows (PASSED, FAILED);
  ``_update_run_aggregates`` makes ``ui`` its primary suite.
* ``R2`` (yesterday) -- a live-stream style run whose primary suite is ``api``
  and whose rows carry no suite (PASSED, PASSED, FAILED), plus one ``api``
  SKIPPED row and one ``ui`` BROKEN row. NULL-suite rows count only under the
  primary suite. (The live path writes NULL-suite rows; ``ingest_test_results``
  would fill in the project's default suite, so these rows are written as the
  live persister writes them.)
* ``R4`` (ten days ago) -- an ``api`` upload outside a three-day window.

Project Q has an ``api`` upload yesterday with five PASSED rows: never counted
for P.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips only without
it. Writes two throwaway projects and removes them (the cleanup raises if a row
is left behind).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ZERO = {
    "run_count": 0,
    "total_tests": 0,
    "passed_count": 0,
    "failed_count": 0,
    "skipped_count": 0,
    "broken_count": 0,
}


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _noon(days_ago: int) -> datetime:
    """Noon UTC ``days_ago`` days back: well inside its day bucket at any hour the test runs."""
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


async def _cleanup(sessions, project_ids: list[uuid.UUID]) -> None:
    """Delete the seeded projects (runs and cases cascade) and prove nothing is left."""
    async with sessions.begin() as db:
        await db.execute(
            text("DELETE FROM projects WHERE id = ANY(:ids)"), {"ids": project_ids}
        )
    async with sessions() as db:
        left = {
            table: (await db.execute(text(sql), {"ids": project_ids})).scalar_one()
            for table, sql in (
                ("projects", "SELECT count(*) FROM projects WHERE id = ANY(:ids)"),
                (
                    "test_runs",
                    "SELECT count(*) FROM test_runs WHERE project_id = ANY(:ids)",
                ),
                (
                    "test_cases",
                    "SELECT count(*) FROM test_cases tc LEFT JOIN test_runs tr ON tr.id = tc.test_run_id "
                    "WHERE tr.id IS NULL OR tr.project_id = ANY(:ids)",
                ),
            )
        }
    leaked = {table: count for table, count in left.items() if count}
    if leaked:
        raise AssertionError(f"suite trend cleanup left rows behind: {leaked}")


async def _upload(sessions, project_id, label, created_at, cases) -> uuid.UUID:
    """An upload through the real ingest path and its aggregate step."""
    from app.services.ingestion import _update_run_aggregates
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )

    async with sessions.begin() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=label,
            ingestion_source="upload",
            reuse_existing=False,
        )
        results = [
            {
                "test_name": name,
                "class_name": f"tests.{suite}.Spec",
                "suite_name": suite,
                "status": status,
                "duration_ms": 50,
            }
            for suite, name, status in cases
        ]
        assert await ingest_test_results(db, run, results) == len(cases)
        await _update_run_aggregates(db, run.id)
        run.created_at = created_at
        return run.id


async def _live_style(
    sessions, project_id, label, created_at, primary, cases
) -> uuid.UUID:
    """A run as the live persister leaves it: a primary suite, rows that may carry none."""
    from app.models.postgres import TestCase
    from app.services.ingestion import make_test_fingerprint
    from app.services.ingestion_pipeline import create_run_from_payload

    async with sessions.begin() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=label,
            ingestion_source="live",
            reuse_existing=False,
        )
        run.primary_suite_name = primary
        run.created_at = created_at
        for suite, name, status in cases:
            db.add(
                TestCase(
                    id=uuid.uuid4(),
                    test_run_id=run.id,
                    test_name=name,
                    class_name="tests.live.Spec",
                    test_fingerprint=make_test_fingerprint(name, "tests.live.Spec"),
                    suite_name=suite,
                    status=status,
                    duration_ms=50,
                    created_at=created_at,
                )
            )
        return run.id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.models.postgres import Project

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    p, q = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions.begin() as db:
            db.add_all(
                [
                    Project(
                        id=p,
                        name=f"suitetrend-p-{tag}",
                        slug=f"suitetrend-p-{tag}",
                        is_active=True,
                        description="throwaway suite trend",
                    ),
                    Project(
                        id=q,
                        name=f"suitetrend-q-{tag}",
                        slug=f"suitetrend-q-{tag}",
                        is_active=True,
                        description="throwaway suite trend, other project",
                    ),
                ]
            )
        r1 = await _upload(
            sessions,
            p,
            f"r1-{tag}",
            _noon(2),
            [
                ("ui", "test_login", "PASSED"),
                ("ui", "test_logout", "PASSED"),
                ("ui", "test_profile", "SKIPPED"),
                ("api", "test_orders", "PASSED"),
                ("api", "test_refunds", "FAILED"),
            ],
        )
        r2 = await _live_style(
            sessions,
            p,
            f"r2-{tag}",
            _noon(1),
            "api",
            [
                (None, "test_health", "PASSED"),
                (None, "test_ping", "PASSED"),
                (None, "test_quota", "FAILED"),
                ("api", "test_orders", "SKIPPED"),
                ("ui", "test_banner", "BROKEN"),
            ],
        )
        await _upload(
            sessions, p, f"r4-{tag}", _noon(10), [("api", "test_orders", "FAILED")]
        )
        await _upload(
            sessions,
            q,
            f"q-{tag}",
            _noon(1),
            [("api", f"test_q{i}", "PASSED") for i in range(5)],
        )

        async with sessions() as db:
            primaries = dict(
                (
                    await db.execute(
                        text(
                            "SELECT id, primary_suite_name FROM test_runs WHERE id = ANY(:ids)"
                        ),
                        {"ids": [r1, r2]},
                    )
                ).all()
            )
        # Precondition: the ingest path gave R1 the suite most of its rows are in.
        assert primaries == {r1: "ui", r2: "api"}, primaries
        yield sessions, p, q
    finally:
        await _cleanup(sessions, [p, q])
        await engine.dispose()


async def _trend(sessions, project_id, suite, days=3):
    from app.services.suite_history_service import compute_suite_trend

    async with sessions() as db:
        return await compute_suite_trend(db, project_id, suite, days=days)


def _by_date(points: list[dict]) -> dict[str, dict]:
    return {
        point["date"]: {k: v for k, v in point.items() if k != "date"}
        for point in points
    }


def _expected(two_days_ago: dict, yesterday: dict) -> dict[str, dict]:
    today = datetime.now(timezone.utc).date()
    return {
        (today - timedelta(days=3)).isoformat(): ZERO,
        (today - timedelta(days=2)).isoformat(): {**ZERO, **two_days_ago},
        (today - timedelta(days=1)).isoformat(): {**ZERO, **yesterday},
        today.isoformat(): ZERO,
    }


async def test_the_api_trend_counts_each_days_api_rows_and_the_primary_suites_unlabelled_rows(
    world,
):
    sessions, p, _q = world
    points = await _trend(sessions, p, "api")

    assert sum(point["run_count"] for point in points) > 0, (
        "the query returned no runs (an SQL error reads as [])"
    )
    assert [point["date"] for point in points] == sorted(_expected({}, {})), (
        "one point per day, in order"
    )
    assert _by_date(points) == _expected(
        # R1: only its two api rows; its primary suite is ui.
        {"run_count": 1, "total_tests": 2, "passed_count": 1, "failed_count": 1},
        # R2: primary api, so its three unlabelled rows count, plus its api row; not its ui row.
        {
            "run_count": 1,
            "total_tests": 4,
            "passed_count": 2,
            "failed_count": 1,
            "skipped_count": 1,
        },
    )


async def test_unlabelled_rows_never_count_under_a_suite_that_is_not_the_primary(world):
    sessions, p, _q = world
    points = await _trend(sessions, p, "ui")

    assert _by_date(points) == _expected(
        # R1 is a ui run: its three ui rows.
        {"run_count": 1, "total_tests": 3, "passed_count": 2, "skipped_count": 1},
        # R2's ui row only; R2's unlabelled rows belong to api.
        {"run_count": 1, "total_tests": 1, "broken_count": 1},
    )


async def test_the_suite_name_matches_case_and_space_insensitively(world):
    sessions, p, _q = world
    assert await _trend(sessions, p, "  API ") == await _trend(sessions, p, "api")


async def test_another_projects_run_is_never_counted(world):
    sessions, _p, q = world
    points = await _trend(sessions, q, "api")
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    assert _by_date(points)[yesterday] == {
        **ZERO,
        "run_count": 1,
        "total_tests": 5,
        "passed_count": 5,
    }
    assert sum(point["run_count"] for point in points) == 1


async def test_a_longer_window_reaches_the_older_run(world):
    sessions, p, _q = world
    points = _by_date(await _trend(sessions, p, "api", days=12))
    ten_days_ago = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()

    assert len(points) == 13
    assert points[ten_days_ago] == {
        **ZERO,
        "run_count": 1,
        "total_tests": 1,
        "failed_count": 1,
    }
    assert sum(point["run_count"] for point in points.values()) == 3
