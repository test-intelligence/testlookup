"""D-0004 against real Postgres: ``attribute_run`` names what each failing test did last time.

A project's first failure used to read "Not a new failure -- this test was
already failing" (D-0004). The fix makes ``attribute_run`` read every row of
the project's previous run and hand ``compose`` a ``previous_outcome``. The
pure pieces are tested in ``tests/regression/test_phase4_attribution_verdict.py``;
this module drives the real ``attribute_run`` (and the ``GET
/runs/{id}/attribution`` route body) on rows written by the real ingest path
(``create_run_from_payload`` + ``ingest_test_results``), so a regression that
stops passing the outcome, or goes back to reading only PASSED rows, fails here.

Project P, three runs an hour apart:

* run 1: ``test_a`` FAILED, ``test_c`` SKIPPED (the project's first run);
* run 2: ``test_a`` FAILED, ``test_b`` FAILED (absent from run 1),
  ``test_c`` FAILED (skipped in run 1), ``test_d`` PASSED;
* run 3: ``test_a`` FAILED, ``test_d`` FAILED (passed in run 2).

Project Q holds one run older than P's run 1 and one between P's runs 1 and 2,
both with every test PASSED: if the previous-run lookup lost its project scope,
P's first run would have a "previous run" and run 2's tests would read "passed".

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips only without
it. Writes two throwaway projects and removes them (the cleanup raises if a row
is left behind).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ALREADY_FAILING = "already failing"
CLASS_NAME = "tests.checkout.CheckoutTest"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _cleanup(sessions, project_ids: list[uuid.UUID]) -> None:
    """Delete every seeded project (runs and cases cascade) and prove nothing is left."""
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
        raise AssertionError(f"attribution baseline cleanup left rows behind: {leaked}")


async def _ingest_run(sessions, project_id, label, created_at, cases) -> uuid.UUID:
    """One run through the real ingest path, committed on its own."""
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )

    async with sessions.begin() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=label,
            ingestion_source="sdk",
            reuse_existing=False,
        )
        results = [
            {
                "test_name": name,
                "class_name": CLASS_NAME,
                "status": status,
                "duration_ms": 100,
                "suite_name": "checkout",
                **({"error_message": f"{name} failed"} if status == "FAILED" else {}),
            }
            for name, status in cases
        ]
        assert await ingest_test_results(db, run, results) == len(cases)
        # The previous run is chosen by created_at; space the runs an hour apart
        # so the order is the scenario's, not the commit timing's.
        run.created_at = created_at
        return run.id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.models.postgres import Project

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    p, q = uuid.uuid4(), uuid.uuid4()
    base = datetime.now(timezone.utc) - timedelta(hours=6)
    try:
        async with sessions.begin() as db:
            db.add_all(
                [
                    Project(
                        id=p,
                        name=f"d0004-p-{tag}",
                        slug=f"d0004-p-{tag}",
                        is_active=True,
                        description="throwaway D-0004 attribution baseline",
                    ),
                    Project(
                        id=q,
                        name=f"d0004-q-{tag}",
                        slug=f"d0004-q-{tag}",
                        is_active=True,
                        description="throwaway D-0004 other project",
                    ),
                ]
            )
        every_green = [
            ("test_a", "PASSED"),
            ("test_b", "PASSED"),
            ("test_c", "PASSED"),
            ("test_d", "PASSED"),
        ]
        await _ingest_run(
            sessions, q, f"q-old-{tag}", base - timedelta(hours=1), every_green
        )
        runs = {
            "run1": await _ingest_run(
                sessions,
                p,
                f"p-1-{tag}",
                base,
                [("test_a", "FAILED"), ("test_c", "SKIPPED")],
            ),
        }
        await _ingest_run(
            sessions, q, f"q-mid-{tag}", base + timedelta(minutes=30), every_green
        )
        runs["run2"] = await _ingest_run(
            sessions,
            p,
            f"p-2-{tag}",
            base + timedelta(hours=1),
            [
                ("test_a", "FAILED"),
                ("test_b", "FAILED"),
                ("test_c", "FAILED"),
                ("test_d", "PASSED"),
            ],
        )
        runs["run3"] = await _ingest_run(
            sessions,
            p,
            f"p-3-{tag}",
            base + timedelta(hours=2),
            [("test_a", "FAILED"), ("test_d", "FAILED")],
        )
        yield sessions, p, q, runs
    finally:
        await _cleanup(sessions, [p, q])
        await engine.dispose()


async def _attribution(sessions, run_id) -> dict:
    """The ``GET /runs/{id}/attribution`` body, from the real route function."""
    from app.routers.runs import run_attribution

    async with sessions() as db:
        return await run_attribution(run_id, db, None)


def _by_test(body: dict) -> dict[str, dict]:
    items = {item["test_name"]: item for item in body["items"]}
    assert len(items) == len(body["items"]), f"a test is listed twice: {body['items']}"
    return items


async def test_the_seed_went_through_ingest_with_real_fingerprints(world):
    """Precondition: the rows exist, with the upload path's fingerprints."""
    from app.models.postgres import TestCase, TestRun
    from app.services.ingestion import make_test_fingerprint

    sessions, p, _q, runs = world
    async with sessions() as db:
        rows = (
            await db.execute(
                select(
                    TestRun.id,
                    TestCase.test_name,
                    TestCase.status,
                    TestCase.test_fingerprint,
                )
                .join(TestCase, TestCase.test_run_id == TestRun.id)
                .where(TestRun.project_id == p)
            )
        ).all()
        run_count = (
            await db.execute(
                select(func.count()).select_from(TestRun).where(TestRun.project_id == p)
            )
        ).scalar_one()
    assert run_count == 3
    assert len(rows) == 8
    assert {row.test_fingerprint for row in rows} == {
        make_test_fingerprint(name, CLASS_NAME)
        for name in ("test_a", "test_b", "test_c", "test_d")
    }
    assert {
        (row.id, row.test_name, str(row.status))
        for row in rows
        if row.id == runs["run1"]
    } == {
        (runs["run1"], "test_a", "FAILED"),
        (runs["run1"], "test_c", "SKIPPED"),
    }


async def test_a_projects_first_failure_is_not_called_already_failing(world):
    sessions, _p, _q, runs = world
    body = await _attribution(sessions, runs["run1"])

    assert body["total"] == 1
    item = _by_test(body)["test_a"]
    # The defect's own words first: a first run is never "already failing".
    assert ALREADY_FAILING not in item["rationale"], item["rationale"]
    assert "first recorded run in this project" in item["rationale"], item["rationale"]
    assert item["inputs"]["previous_outcome"] == "no_previous_run", item["inputs"]
    assert item["inputs"]["is_new_failure"] is False
    assert item["inputs"]["last_green_run_id"] is None
    assert item["verdict"] == "UNCERTAIN"


async def test_the_second_run_names_each_tests_previous_outcome(world):
    sessions, _p, _q, runs = world
    body = await _attribution(sessions, runs["run2"])

    assert body["total"] == 3, body["items"]
    items = _by_test(body)
    assert set(items) == {"test_a", "test_b", "test_c"}
    outcomes = {
        name: item["inputs"]["previous_outcome"] for name, item in items.items()
    }
    assert outcomes == {
        "test_a": "failed",
        "test_b": "not_in_previous_run",
        "test_c": "skipped",
    }

    # Only the test that really failed last time is "already failing".
    assert ALREADY_FAILING in items["test_a"]["rationale"]
    assert "did not run in the previous run" in items["test_b"]["rationale"]
    assert ALREADY_FAILING not in items["test_b"]["rationale"]
    assert "skipped in the previous run" in items["test_c"]["rationale"]
    assert ALREADY_FAILING not in items["test_c"]["rationale"]
    for name, item in items.items():
        assert item["inputs"]["last_green_run_id"] is None, (name, item["inputs"])
        assert item["inputs"]["is_new_failure"] is False, (name, item["inputs"])


async def test_a_test_that_passed_last_time_is_a_new_failure_with_its_last_green_run(
    world,
):
    sessions, _p, _q, runs = world
    body = await _attribution(sessions, runs["run3"])

    assert body["total"] == 2
    items = _by_test(body)
    assert items["test_d"]["inputs"]["previous_outcome"] == "passed"
    assert items["test_d"]["inputs"]["is_new_failure"] is True
    assert items["test_d"]["inputs"]["last_green_run_id"] == str(runs["run2"])
    assert items["test_a"]["inputs"]["previous_outcome"] == "failed"
    assert items["test_a"]["inputs"]["last_green_run_id"] is None
    assert ALREADY_FAILING in items["test_a"]["rationale"]
