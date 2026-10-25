"""W14 AGT-12 on real PostgreSQL + Redis, through the app: a bulk job's progress and its cancel.

* pipeline rows stamped with the job (``execution_metadata.bulk_job_id``) in
  several statuses -> the progress COUNTED from them on each read; the read
  writes nothing (no statement that writes, the record unchanged);
* another user's job is the unknown-job 404 (no oracle); an admin reads it;
* a member who lost a project no longer sees that project's rows counted;
* the record deleted -> ``counters_source: "postgres"``, the caller's own rows
  counted, every request count None; another user -> 404;
* a job older than the settle window with tasks that never started is
  ``finished`` with the not-started note (never counted as failed);
* cancel -> 200 + one ``analysis.bulk_cancelled`` per project of the job; again
  -> 200 and no second row; a finished job -> 409.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _url(job_id, cancel=False):
    path = f"/api/v1/agents/pipelines/bulk-jobs/{job_id}"
    return f"{path}/cancel" if cancel else path


async def _job(w, statuses_by_project, *, queued_extra=0, age=timedelta(minutes=1)):
    """A job record + one stamped pipeline per (project, status); ``queued_extra``
    queued runs never started."""
    from app.models.postgres import AgentPipelineRun, LaunchStatus, TestRun
    from app.services import bulk_pipeline_job_service as jobs

    job_id = str(uuid.uuid4())
    queued: list[tuple] = []
    per_project: dict = {}
    async with w.sessions.begin() as db:
        for project_id, statuses in statuses_by_project.items():
            for status in [*statuses, *([None] * (queued_extra if project_id == w.P else 0))]:
                run_id = uuid.uuid4()
                db.add(TestRun(id=run_id, project_id=project_id, build_number=f"bj-{run_id.hex[:6]}",
                               jenkins_job="bulkj", status=LaunchStatus.FAILED, ingestion_source="unknown",
                               total_tests=1, failed_tests=1))
                await db.flush()
                queued.append((run_id, project_id, None))
                per_project[project_id] = per_project.get(project_id, 0) + 1
                if status is not None:
                    db.add(AgentPipelineRun(test_run_id=run_id, workflow_type="offline", status=status,
                                            requested_by=w.requester_id,
                                            execution_metadata={"bulk_job_id": job_id}))
    created = await jobs.create_job(
        job_id=job_id, requested_by=w.requester_id, workflow_type="offline", source="selection",
        requested=len(queued) + 1, queued_rows=queued, skipped=1, not_found=0, matched=None, truncated=False,
        queued_per_project=per_project, now=datetime.now(timezone.utc) - age,
    )
    assert created
    return job_id


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.X = await w.project(db)
            w.requester_id = await w.user(db, role=UserRole.QA_ENGINEER.value)
            w.other_id = await w.user(db, role=UserRole.QA_ENGINEER.value)
            for uid, pid in ((w.requester_id, w.P), (w.requester_id, w.X), (w.other_id, w.P)):
                db.add(ProjectMember(project_id=pid, user_id=uid, role=UserRole.QA_ENGINEER.value))
        w.requester, w.other = w.jwt(w.requester_id), w.jwt(w.other_id)
        yield w


async def _cancel_events(w, job_id):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        rows = (await db.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.event_type == "analysis.bulk_cancelled",
                ProjectActivityEvent.project_id.in_([w.P, w.X]),
            )
        )).scalars().all()
    return [row for row in rows if (row.context or {}).get("bulk_job_id") == job_id]


async def _record(job_id):
    from app.db.redis_client import get_redis
    from app.services import bulk_pipeline_job_service as jobs

    return await get_redis().hgetall(jobs.job_key(job_id))


async def test_progress_is_counted_from_the_rows_and_the_read_writes_nothing(world):
    w = world
    job_id = await _job(w, {w.P: ["running", "passed", "failed"], w.X: ["completed"]})
    before = await _record(job_id)
    with StatementCounter(w.engine) as counter:
        response = await w.client.get(_url(job_id), headers=w.requester)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counters_source"] == "redis" and body["state"] == "active"
    assert body["progress"] == {"started": 4, "in_progress": 1, "completed": 1, "passed": 1, "failed": 1,
                                "not_started": 0}
    assert (body["requested"], body["queued"], body["skipped_in_progress"]) == (5, 4, 1)
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in counter.statements)
    assert await _record(job_id) == before

    admin = await w.client.get(_url(job_id), headers=w.admin)
    assert admin.status_code == 200 and admin.json()["progress"]["started"] == 4
    for headers in (w.other, w.outsider):
        stranger = await w.client.get(_url(job_id), headers=headers)
        assert stranger.status_code == 404 and stranger.json()["detail"] == "Bulk job not found"
    unknown = await w.client.get(_url(uuid.uuid4()), headers=w.requester)
    assert unknown.status_code == 404 and unknown.json()["detail"] == "Bulk job not found"


async def test_a_member_who_lost_a_project_no_longer_counts_its_rows(world):
    from app.core.deps import invalidate_membership_cache
    from app.models.postgres import ProjectMember

    w = world
    job_id = await _job(w, {w.P: ["passed", "passed"], w.X: ["failed"]})
    async with w.sessions.begin() as db:
        await db.execute(delete(ProjectMember).where(
            ProjectMember.project_id == w.X, ProjectMember.user_id == w.requester_id,
        ))
    await invalidate_membership_cache(w.requester_id)
    body = (await w.client.get(_url(job_id), headers=w.requester)).json()
    assert body["progress"]["started"] == 2 and body["progress"]["failed"] == 0


async def test_without_the_record_the_callers_own_rows_are_counted_from_postgres(world):
    from app.db.redis_client import get_redis
    from app.services import bulk_pipeline_job_service as jobs

    w = world
    job_id = await _job(w, {w.P: ["passed", "running"]})
    await get_redis().delete(jobs.job_key(job_id))
    response = await w.client.get(_url(job_id), headers=w.requester)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counters_source"] == "postgres" and body["state"] == "active"
    assert body["requested"] is None and body["queued"] is None and body["progress"]["not_started"] is None
    assert body["progress"]["started"] == 2 and body["progress"]["passed"] == 1
    assert (await w.client.get(_url(job_id), headers=w.admin)).status_code == 404   # only the caller's own rows
    assert (await w.client.get(_url(job_id, cancel=True), headers=w.requester)).status_code == 405
    response = await w.client.post(_url(job_id, cancel=True), headers=w.requester)
    assert response.status_code == 404


async def test_tasks_that_never_started_are_noted_after_the_settle_window(world):
    from app.core.agent_ops_vocab import BULK_JOB_SETTLE_SECONDS, BULK_NOT_STARTED_NOTE

    w = world
    job_id = await _job(w, {w.P: ["passed", "failed"]}, queued_extra=1,
                        age=timedelta(seconds=BULK_JOB_SETTLE_SECONDS + 60))
    body = (await w.client.get(_url(job_id), headers=w.requester)).json()
    assert body["state"] == "finished" and body["note"] == BULK_NOT_STARTED_NOTE.format(n=1)
    assert body["progress"]["not_started"] == 1 and body["progress"]["failed"] == 1


async def test_cancel_records_once_per_project_and_a_finished_job_is_a_409(world):
    from app.core.agent_ops_vocab import BULK_JOB_ALREADY_FINISHED

    w = world
    job_id = await _job(w, {w.P: ["running", "passed"], w.X: ["pending"]})
    assert (await w.client.post(_url(job_id, cancel=True), headers=w.other)).status_code == 404
    response = await w.client.post(_url(job_id, cancel=True), headers=w.requester)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "cancelled" and response.json()["cancelled_at"]
    events = await _cancel_events(w, job_id)
    assert sorted((str(e.project_id), e.context["queued"]) for e in events) == sorted(
        [(str(w.P), 2), (str(w.X), 1)]
    )
    again = await w.client.post(_url(job_id, cancel=True), headers=w.requester)
    assert again.status_code == 200 and again.json()["state"] == "cancelled"
    assert len(await _cancel_events(w, job_id)) == 2
    from app.services import bulk_pipeline_job_service as jobs

    assert await jobs.is_cancelled(job_id) is True

    finished = await _job(w, {w.P: ["passed", "failed"]})
    response = await w.client.post(_url(finished, cancel=True), headers=w.requester)
    assert response.status_code == 409 and response.json()["detail"] == BULK_JOB_ALREADY_FINISHED
    assert await _cancel_events(w, finished) == []
