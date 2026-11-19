"""``GET /runs/failed-ids`` against real Postgres: which FAILED runs the "re-analyse" fan-out picks.

``list_failed_run_ids`` feeds the "Trigger all FAILED across pages" shortcut,
which fires one agent pipeline per id. Until now it was covered by source and
parameter checks only. Here the route function runs on rows written by the real
ingest path (``create_run_from_payload`` + ``ingest_test_results`` +
``_update_run_aggregates``, which grades each run), in project P:

* FAILED runs with builds ``2``, ``1.10``, ``1.9``, ``1.8`` (its pipeline is
  ``pending``, created 3 h ago), ``1.7`` (a pipeline finished 10 minutes ago),
  ``1.6`` (a pipeline finished 3 h ago) and ``1.5`` (primary suite ``api``, one
  case in suite ``web``), a PASSED run ``3``, and a FAILED run ``0.1`` from 40
  days ago;
* project Q (active) and project D (soft-deleted) hold one FAILED run each.

Asserted: the exact ids in natural build order (``1.10`` above ``1.9``),
``count`` and ``truncated`` at a limit; ``only_pending`` drops the run whose
pipeline is still in progress and the one inside the 2 h dedup window; the
suite filter matches a run by its primary suite or by any case's suite,
case- and space-insensitively; the day window; and without a project, Q's run
is offered but D's never is.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips only without
it. Writes three throwaway projects and removes them (the cleanup raises if a
row is left behind).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

#: In natural build order, newest first: what the fan-out must receive for P.
P_FAILED = ["2", "1.10", "1.9", "1.8", "1.7", "1.6", "1.5"]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _cleanup(sessions, project_ids: list[uuid.UUID]) -> None:
    """Delete the seeded projects (runs, cases, pipelines cascade); prove nothing is left."""
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
                    "agent_pipeline_runs",
                    "SELECT count(*) FROM agent_pipeline_runs p LEFT JOIN test_runs tr ON tr.id = p.test_run_id "
                    "WHERE tr.id IS NULL OR tr.project_id = ANY(:ids)",
                ),
            )
        }
    leaked = {table: count for table, count in left.items() if count}
    if leaked:
        raise AssertionError(f"failed-run-ids cleanup left rows behind: {leaked}")


async def _run(sessions, project_id, build, created_at, cases) -> uuid.UUID:
    from app.services.ingestion import _update_run_aggregates
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )

    async with sessions.begin() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=build,
            ingestion_source="sdk",
            reuse_existing=False,
        )
        results = [
            {
                "test_name": name,
                "class_name": f"tests.{suite}.Spec",
                "suite_name": suite,
                "status": status,
            }
            for suite, name, status in cases
        ]
        assert await ingest_test_results(db, run, results) == len(cases)
        await _update_run_aggregates(db, run.id)
        run.created_at = created_at
        return run.id


async def _pipeline(sessions, run_id, status: str, created_at) -> None:
    from app.models.postgres import AgentPipelineRun

    async with sessions.begin() as db:
        db.add(
            AgentPipelineRun(test_run_id=run_id, status=status, created_at=created_at)
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.models.postgres import Project

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    p, q, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    failing = [
        ("api", "test_orders", "FAILED"),
        ("api", "test_refunds", "PASSED"),
        ("api", "test_cart", "PASSED"),
    ]
    try:
        async with sessions.begin() as db:
            for pid, label, active in ((p, "p", True), (q, "q", True), (d, "d", False)):
                db.add(
                    Project(
                        id=pid,
                        name=f"failedids-{label}-{tag}",
                        slug=f"failedids-{label}-{tag}",
                        is_active=active,
                        description="throwaway failed-run-ids",
                    )
                )
        ids: dict[str, uuid.UUID] = {}
        # Created out of build order on purpose: the order must come from the build label.
        for minutes_ago, build in zip((70, 10, 60, 50, 40, 30, 20), P_FAILED):
            cases = (
                failing
                if build != "1.5"
                else [*failing, ("web", "test_banner", "PASSED")]
            )
            ids[build] = await _run(
                sessions, p, build, now - timedelta(minutes=minutes_ago), cases
            )
        ids["3"] = await _run(
            sessions,
            p,
            "3",
            now - timedelta(minutes=5),
            [("api", "test_orders", "PASSED")],
        )
        ids["0.1"] = await _run(sessions, p, "0.1", now - timedelta(days=40), failing)
        ids["q"] = await _run(sessions, q, "1", now - timedelta(minutes=15), failing)
        ids["d"] = await _run(sessions, d, "1", now - timedelta(minutes=15), failing)

        await _pipeline(sessions, ids["1.8"], "pending", now - timedelta(hours=3))
        await _pipeline(sessions, ids["1.7"], "completed", now - timedelta(minutes=10))
        await _pipeline(sessions, ids["1.6"], "completed", now - timedelta(hours=3))

        async with sessions() as db:
            statuses = dict(
                (
                    await db.execute(
                        text("SELECT id, status FROM test_runs WHERE id = ANY(:ids)"),
                        {"ids": list(ids.values())},
                    )
                ).all()
            )
        # Precondition: the ingest path graded the runs as the scenario says.
        assert {
            build for build, run_id in ids.items() if str(statuses[run_id]) == "PASSED"
        } == {"3"}, statuses
        yield SimpleNamespace(sessions=sessions, p=p, q=q, d=d, ids=ids)
    finally:
        await _cleanup(sessions, [p, q, d])
        await engine.dispose()


async def _failed_ids(
    w, *, project_id="P", days=30, limit=1000, only_pending=False, suite_name=None
) -> dict:
    from app.models.postgres import UserRole
    from app.routers.runs import list_failed_run_ids

    admin = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, is_active=True)
    async with w.sessions() as db:
        return await list_failed_run_ids(
            project_id=str(w.p) if project_id == "P" else project_id,
            days=days,
            limit=limit,
            only_pending=only_pending,
            suite_name=suite_name,
            db=db,
            current_user=admin,
        )


def _builds(w, body: dict) -> list[str]:
    by_id = {str(run_id): build for build, run_id in w.ids.items()}
    return [by_id.get(run_id, run_id) for run_id in body["ids"]]


async def test_every_failed_run_of_the_project_in_natural_build_order(world):
    body = await _failed_ids(world)
    assert _builds(world, body) == P_FAILED
    assert (body["count"], body["truncated"]) == (len(P_FAILED), False)


async def test_a_limit_keeps_the_newest_builds_and_says_it_truncated(world):
    body = await _failed_ids(world, limit=2)
    assert _builds(world, body) == ["2", "1.10"]
    assert (body["count"], body["truncated"]) == (2, True)


async def test_only_pending_skips_runs_with_a_pipeline_in_flight_or_just_done(world):
    body = await _failed_ids(world, only_pending=True)
    # 1.8's pipeline is still pending (older than 2 h); 1.7's finished inside the window.
    assert _builds(world, body) == ["2", "1.10", "1.9", "1.6", "1.5"], (
        "a run whose pipeline is still in progress, or ran within the 2 h dedup window, was offered again"
    )
    assert body["count"] == 5


async def test_the_suite_filter_matches_the_primary_suite_or_any_cases_suite(world):
    web = await _failed_ids(world, suite_name="web")
    assert _builds(world, web) == ["1.5"]
    api = await _failed_ids(world, suite_name="  API ")
    assert _builds(world, api) == P_FAILED
    assert (await _failed_ids(world, suite_name="payments"))["ids"] == []


async def test_the_day_window_and_all_time(world):
    all_time = await _failed_ids(world, days=0)
    assert _builds(world, all_time) == [*P_FAILED, "0.1"]


async def test_without_a_project_a_deleted_projects_run_is_never_offered(world):
    body = await _failed_ids(world, project_id=None, days=1, limit=5000)
    assert str(world.ids["q"]) in body["ids"]
    assert str(world.ids["d"]) not in body["ids"], (
        "a soft-deleted project's run was offered for a pipeline"
    )
    assert set(map(str, (world.ids[b] for b in P_FAILED))) <= set(body["ids"])
