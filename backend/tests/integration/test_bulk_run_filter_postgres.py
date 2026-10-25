"""W14 AGT-12 on real PostgreSQL: "re-trigger all matching" resolves EXACTLY the runs ``GET /runs`` lists.

* for status / days / suite / q / branch / recommendation / release filters on one
  seeded world, ``bulk_pipeline_job_service.resolve_run_filter`` returns the ids
  ``GET /api/v1/runs`` lists for the same filter, in the same order;
* its COUNT statement is the list's COUNT statement, text for text (the twin
  ``runs_service.run_list_filters`` cannot drift from ``list_project_runs``);
* a filter naming another project is the list's 403 (through the bulk route:
  nothing is queued); All Projects is only the caller's projects (never the
  soft-deleted one's runs); more than ``cap`` matching runs is ``truncated``
  with ``matched`` the COUNT and the newest ``cap`` ids.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _run(project_id, build, *, now, days_ago, status="FAILED", branch="main", suite="checkout", release=None):
    from app.models.postgres import TestRun

    row = TestRun(
        id=uuid.uuid4(), project_id=project_id, build_number=build, jenkins_job="bulkf", status=status,
        ingestion_source="upload", branch=branch, primary_suite_name=suite, total_tests=2, failed_tests=1,
        primary_release_id=release,
    )
    row.created_at = now - timedelta(days=days_ago, minutes=1)
    return row


@pytest.fixture
async def world():
    async with defect_world() as w:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        async with w.sessions.begin() as db:
            for row in (
                _run(w.P, f"{w.tag}-101", now=now, days_ago=1, release=w.R),
                _run(w.P, f"{w.tag}-102", now=now, days_ago=2, branch="release/2.4"),
                _run(w.P, f"{w.tag}-103", now=now, days_ago=3, status="PASSED"),
                _run(w.P, f"{w.tag}-104", now=now, days_ago=5, suite="payments", release=w.R),
                _run(w.P, f"{w.tag}-105", now=now, days_ago=40),
                _run(w.Q, f"{w.tag}-q1", now=now, days_ago=1),
                _run(w.D, f"{w.tag}-d1", now=now, days_ago=1),
            ):
                db.add(row)
        yield w


async def _user(w, user_id):
    from app.models.postgres import User

    async with w.sessions() as db:
        return await db.get(User, user_id)


async def _listed(w, headers, **params):
    response = await w.client.get("/api/v1/runs", params={"size": 500, **params}, headers=headers)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def _resolved(w, user, cap=2_000, **fields):
    from app.models.schemas import BulkRunFilter
    from app.services import bulk_pipeline_job_service as jobs

    async with w.sessions() as db:
        rows, matched, truncated = await jobs.resolve_run_filter(db, user, BulkRunFilter(**fields), cap=cap)
    return [str(r[0]) for r in rows], matched, truncated


FILTERS = [
    {},
    {"status": "FAILED"},
    {"days": 0},
    {"days": 7, "suite_name": ["payments"]},
    {"q": "-10"},
    {"branch": ["release/2.4"]},
    {"recommendation": "none"},
]


@pytest.mark.parametrize("fields", FILTERS)
async def test_the_filter_resolves_the_ids_the_list_shows(world, fields):
    w = world
    member = await _user(w, w.member_id)
    listed = await _listed(w, w.member, project_id=str(w.P), **fields)
    ids, matched, truncated = await _resolved(w, member, project_id=w.P, **fields)
    assert ids == listed and matched == len(listed) and truncated is False


async def test_a_release_filter_resolves_like_the_list(world):
    w = world
    member = await _user(w, w.member_id)
    listed = await _listed(w, w.member, project_id=str(w.P), release_id=[str(w.R)], days=0)
    ids, matched, _ = await _resolved(w, member, project_id=w.P, release_id=[str(w.R)], days=0)
    # the world's own run in R plus the two seeded here
    assert ids == listed and matched == 3 and str(w.run_r) in ids


async def _project_of(w, run_ids):
    from sqlalchemy import select

    from app.models.postgres import TestRun

    async with w.sessions() as db:
        rows = (await db.execute(
            select(TestRun.project_id).where(TestRun.id.in_([uuid.UUID(r) for r in run_ids]))
        )).scalars().all()
    return set(rows)


async def test_all_projects_is_only_the_callers_projects(world):
    w = world
    member = await _user(w, w.member_id)
    listed = await _listed(w, w.member, days=0)
    ids, matched, _ = await _resolved(w, member, days=0)
    # P's six runs (the world's + five seeded): never Q's, never soft-deleted D's
    assert ids == listed and matched == 6
    assert await _project_of(w, ids) == {w.P}


async def test_more_than_the_cap_is_truncated_with_the_count(world):
    w = world
    member = await _user(w, w.member_id)
    listed = await _listed(w, w.member, project_id=str(w.P), days=0)
    ids, matched, truncated = await _resolved(w, member, cap=2, project_id=w.P, days=0)
    assert truncated is True and matched == len(listed) == 6 and ids == listed[:2]


async def test_the_count_statement_is_the_lists_count_statement(world):
    w = world
    member = await _user(w, w.member_id)
    fields = {"status": "FAILED", "days": 7, "branch": ["main"], "q": "10"}
    with StatementCounter(w.engine) as listed:
        await _listed(w, w.member, project_id=str(w.P), **fields)
    with StatementCounter(w.engine) as resolved:
        await _resolved(w, member, project_id=w.P, **fields)
    count_of = [s for s in listed.statements if s.lstrip().upper().startswith("SELECT COUNT(TEST_RUNS.ID)")]
    resolver_counts = [s for s in resolved.statements if s.lstrip().upper().startswith("SELECT COUNT(TEST_RUNS.ID)")]
    assert len(count_of) == 1 and resolver_counts == count_of


async def test_another_projects_filter_is_the_lists_403_and_queues_nothing(world):
    w = world
    member = await _user(w, w.member_id)
    with pytest.raises(HTTPException) as exc:
        await _resolved(w, member, project_id=w.Q)
    assert exc.value.status_code == 403 and exc.value.detail == "You do not have access to this project"
    response = await w.client.post(
        "/api/v1/agents/pipelines/bulk-trigger",
        json={"run_filter": {"project_id": str(w.Q)}, "workflow_type": "offline"}, headers=w.member,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this project"
