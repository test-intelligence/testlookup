"""W14 AGT-12: the bulk trigger as a tracked job (scripted sessions + an in-memory Redis).

* exactly one of ``run_ids`` / ``run_filter`` (two fixed 422 sentences, no statement);
* bulk deep refused by the gate queues NOTHING and writes no job record;
* a run whose pipeline is in progress is skipped, counted and echoed;
* a replayed ``client_token`` answers the first job (``replayed: true``) and queues nothing;
* Redis raising: ``tracking: false``, no ``bulk_job_id``, the batch still queues;
* every ``apply_async`` payload is BOUND to the real task signature (incl. ``bulk_job_id``);
* ``runs_service.run_list_filters`` / ``run_list_order_keys`` compile exactly as the
  predicates / order ``list_project_runs`` builds (the twin may not drift);
* the pure ``job_state`` / ``job_read`` rules (not started is never failed; unknown is None).
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.agent_ops_vocab import (
    BULK_IDS_OR_FILTER,
    BULK_JOB_SETTLE_SECONDS,
    BULK_NOT_STARTED_NOTE,
    BULK_NOTHING_TO_TRIGGER,
)
from app.routers import agents as agents_router
from app.services import bulk_pipeline_job_service as jobs

P1, P2 = uuid.uuid4(), uuid.uuid4()


class FakeRedis:
    """The calls the job service makes, over dicts (``fail=True`` raises on every call)."""

    def __init__(self, *, fail: bool = False):
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.fail = fail

    def _check(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def hset(self, key, mapping):
        self._check()
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    async def expire(self, key, seconds):
        self._check()
        self.ttl[key] = seconds

    async def hgetall(self, key):
        self._check()
        return dict(self.hashes.get(key, {}))

    async def hget(self, key, field):
        self._check()
        return self.hashes.get(key, {}).get(field)

    async def exists(self, key):
        self._check()
        return int(key in self.hashes)

    async def set(self, key, value, nx=False, ex=None):
        self._check()
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        self.ttl[key] = ex
        return True

    async def get(self, key):
        self._check()
        return self.strings.get(key)

    def pipeline(self, transaction=True):
        redis = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def hset(self, key, mapping):
                self.ops.append(lambda: redis.hset(key, mapping=mapping))

            def expire(self, key, seconds):
                self.ops.append(lambda: redis.expire(key, seconds))

            async def execute(self):
                redis._check()
                for op in self.ops:
                    await op()

        return _Pipe()


def _bind(kwargs: dict) -> inspect.BoundArguments:
    from app.worker.tasks import run_agent_pipeline

    signature = inspect.signature(run_agent_pipeline.run)
    if next(iter(signature.parameters), None) == "self":
        return signature.bind(None, **kwargs)
    return signature.bind(**kwargs)


class _Session:
    """``.all()`` = the run rows for the scope query; ``.scalars().all()`` = the
    runs with a pipeline in progress for the admission read."""

    def __init__(self, rows, in_progress=()):
        self.rows = list(rows)
        self.in_progress = list(in_progress)
        self.statements: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        sql = str(stmt)
        self.statements.append(sql)
        if "agent_pipeline_runs" in sql:
            ids = list(self.in_progress)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ids))
        rows = list(self.rows)
        return SimpleNamespace(all=lambda: rows)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", full_name="Bulk Tester", username="bulk")


async def _bulk(payload, db, *, redis=None, user=None, gate=None):
    from app.worker.tasks import run_agent_pipeline

    calls: list[dict] = []

    def apply_async(*, kwargs, queue):
        _bind(kwargs)               # a payload that does not fit the task raises here
        assert queue == "ai_analysis"
        calls.append(kwargs)

    record = AsyncMock()
    fake = redis if redis is not None else FakeRedis()
    with patch.object(run_agent_pipeline, "apply_async", apply_async), \
         patch.object(agents_router, "get_accessible_project_ids", AsyncMock(return_value=None)), \
         patch.object(agents_router, "record_activity", record), \
         patch.object(agents_router, "require_deep_investigation", gate or AsyncMock()), \
         patch.object(jobs, "_redis", lambda: fake):
        body = await agents_router.bulk_trigger_pipelines(payload, db=db, current_user=user or _user(), _=None)
    return body, calls, record, fake


def _rows(n, project=P1):
    return [(uuid.uuid4(), project, f"b{i}") for i in range(n)]


# ── the request ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("both", [True, False])
async def test_ids_and_filter_are_exclusive_and_one_is_required(both):
    from app.models.schemas import BulkRunFilter

    payload = agents_router.BulkTriggerRequest(
        run_ids=[uuid.uuid4()] if both else None, run_filter=BulkRunFilter() if both else None,
    )
    db = _Session(_rows(1))
    with pytest.raises(HTTPException) as exc:
        await _bulk(payload, db)
    assert exc.value.status_code == 422
    assert exc.value.detail == (BULK_IDS_OR_FILTER if both else BULK_NOTHING_TO_TRIGGER)
    assert db.statements == []


@pytest.mark.asyncio
async def test_bulk_deep_refused_by_the_gate_queues_nothing_and_writes_no_job():
    from app.services.deep_investigation_gate import MESSAGE_RULES_MODE

    gate = AsyncMock(side_effect=HTTPException(422, detail=MESSAGE_RULES_MODE))
    redis = FakeRedis()
    db = _Session(_rows(3))
    with pytest.raises(HTTPException) as exc:
        await _bulk(agents_router.BulkTriggerRequest(run_ids=[r[0] for r in db.rows], workflow_type="deep"),
                    db, redis=redis, gate=gate)
    assert exc.value.status_code == 422 and exc.value.detail == MESSAGE_RULES_MODE
    assert db.statements == [] and redis.hashes == {} and redis.strings == {}


@pytest.mark.asyncio
async def test_an_in_progress_run_is_skipped_counted_and_the_job_is_recorded_first():
    rows = _rows(3) + _rows(1, project=P2)
    busy = rows[1][0]
    db = _Session(rows, in_progress=[busy])
    body, calls, record, redis = await _bulk(
        agents_router.BulkTriggerRequest(run_ids=[r[0] for r in rows] + [uuid.uuid4()]), db,
    )

    assert body.requested == 5 and body.queued == 3 and body.not_found == 1
    assert body.skipped_in_progress == 1 and body.skipped_in_progress_ids == [str(busy)]
    assert body.tracking is True and body.source == "selection" and body.replayed is False
    assert str(busy) not in {c["test_run_id"] for c in calls}
    # every payload carries the job id (bound to the real signature in apply_async)
    assert {c["bulk_job_id"] for c in calls} == {body.bulk_job_id}
    [key] = redis.hashes
    job = jobs.decode_job(redis.hashes[key])
    assert key == jobs.job_key(body.bulk_job_id)
    assert (job["requested"], job["queued"], job["skipped_in_progress"], job["not_found"]) == (5, 3, 1, 1)
    assert sorted(job["run_ids"]) == sorted(c["test_run_id"] for c in calls)
    assert job["queued_per_project"] == {str(P1): 2, str(P2): 1}
    assert redis.ttl[key] == 86_400
    events = {e.kwargs["project_id"]: e.kwargs for e in record.await_args_list}
    assert events[P1]["context"] == {"count": 2, "workflow_type": "offline", "bulk_job_id": body.bulk_job_id,
                                     "source": "selection", "skipped_in_progress": 1}
    assert events[P2]["context"]["count"] == 1 and events[P2]["context"]["skipped_in_progress"] == 0
    # ONE in-progress statement for the whole batch
    assert sum(1 for s in db.statements if "agent_pipeline_runs" in s) == 1


@pytest.mark.asyncio
async def test_a_replayed_client_token_answers_the_first_job_and_queues_nothing():
    rows = _rows(2)
    redis = FakeRedis()
    user = _user()
    payload = agents_router.BulkTriggerRequest(run_ids=[r[0] for r in rows], client_token="tok_12345678")
    first, first_calls, _record, _ = await _bulk(payload, _Session(rows), redis=redis, user=user)
    assert len(first_calls) == 2 and first.tracking is True

    db = _Session(rows)
    again, calls, record, _ = await _bulk(payload, db, redis=redis, user=user)
    assert again.status_code == 200
    import json

    body = json.loads(again.body)
    assert body["replayed"] is True and body["bulk_job_id"] == first.bulk_job_id
    assert body["queued"] == 2 and body["requested"] == 2
    assert calls == [] and db.statements == []
    record.assert_not_awaited()
    # another person's identical token is a different key: it queues normally
    other, other_calls, _r, _ = await _bulk(payload, _Session(rows), redis=redis, user=_user())
    assert other.replayed is False and len(other_calls) == 2


@pytest.mark.asyncio
async def test_redis_down_is_untracked_and_the_batch_still_queues_as_before():
    rows = _rows(2)
    body, calls, _record, _ = await _bulk(
        agents_router.BulkTriggerRequest(run_ids=[r[0] for r in rows], client_token="tok_abcdefgh"),
        _Session(rows), redis=FakeRedis(fail=True),
    )
    assert body.tracking is False and body.bulk_job_id is None and body.queued == 2
    assert all("bulk_job_id" not in c for c in calls)


@pytest.mark.asyncio
async def test_a_filter_queues_the_resolved_runs_with_matched_and_truncated():
    from app.models.schemas import BulkRunFilter

    rows = _rows(3)
    resolve = AsyncMock(return_value=(rows, 2_345, True))
    with patch.object(jobs, "resolve_run_filter", resolve):
        body, calls, record, redis = await _bulk(
            agents_router.BulkTriggerRequest(run_filter=BulkRunFilter(status="FAILED")), _Session([]),
        )
    assert resolve.await_args.args[2].status == "FAILED"
    assert body.source == "filter" and body.matched == 2_345 and body.truncated is True
    assert body.requested == 3 and body.queued == 3 and body.not_found == 0
    job = jobs.decode_job(next(iter(redis.hashes.values())))
    assert job["source"] == "filter" and job["matched"] == 2_345 and job["truncated"] is True
    assert record.await_args.kwargs["context"]["source"] == "filter"


# ── the twin of the list's predicates may not drift ─────────────────────────


class _Captured(Exception):
    pass


class _Recorder:
    """Records the statements ``list_project_runs`` executes: the COUNT, then the items."""

    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if len(self.statements) == 1:
            return SimpleNamespace(scalar=lambda: 0)
        raise _Captured()


def _compiled(stmt):
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(dialect=postgresql.dialect())
    params = {
        key: ("<instant>" if isinstance(value, datetime) else value)
        for key, value in compiled.params.items()
    }
    return str(compiled), params


def _filter_matrix():
    release = str(uuid.uuid4())
    return [
        dict(project_id=None, accessible=None, status=None, release=None, days=None, suites=None),
        dict(project_id=str(P1), accessible={P1, P2}, status="FAILED", release=release, days=7, suites="checkout"),
        dict(project_id=None, accessible={P1}, status=None, release="unattributed", days=30, suites=("a", "b")),
        dict(project_id=str(P1), accessible=None, status=None, release="not-a-uuid", days=0, suites=None),
        dict(project_id=None, accessible={P2}, status="PASSED", release=(release, "unattributed"), days=365,
             suites=None, q="build_7%", branches=("main", "rel/1"), recommendation="GO"),
        dict(project_id=None, accessible=None, status=None, release=None, days=None, suites=None,
             recommendation="none"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _filter_matrix())
async def test_run_list_filters_compiles_exactly_as_the_list_count(case):
    from sqlalchemy import func, select

    from app.models.postgres import TestRun
    from app.services import runs_service

    toolbar = {k: case[k] for k in ("q", "branches", "recommendation") if k in case}
    recorder = _Recorder()
    with pytest.raises(_Captured):
        await runs_service.list_project_runs(
            recorder, case["project_id"], 1, 20, case["status"], case["release"],
            accessible_project_ids=case["accessible"], days=case["days"], suite_name=case["suites"], **toolbar,
        )
    twin = runs_service.run_list_filters(
        case["project_id"], case["accessible"], case["status"], case["release"], case["days"], case["suites"],
        **toolbar,
    )
    assert _compiled(select(func.count(TestRun.id)).where(*twin)) == _compiled(recorder.statements[0])


@pytest.mark.asyncio
async def test_run_list_order_keys_is_the_lists_order():
    from sqlalchemy import select

    from app.models.postgres import TestRun
    from app.services import runs_service

    recorder = _Recorder()
    with pytest.raises(_Captured):
        await runs_service.list_project_runs(recorder, str(P1), 1, 20, accessible_project_ids=None, days=None)
    items_sql, _ = _compiled(recorder.statements[1])
    twin_sql, _ = _compiled(select(TestRun.id).order_by(*runs_service.run_list_order_keys()))
    assert items_sql.split("ORDER BY", 1)[1].split("\n LIMIT", 1)[0].strip() == twin_sql.split("ORDER BY", 1)[1].strip()


# ── the pure job rules ───────────────────────────────────────────────────────


def _progress(**counts):
    return {"started": sum(counts.values()), "in_progress": 0, "completed": 0, "passed": 0, "failed": 0, **counts}


def test_progress_and_state_from_the_record():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    job = {"state": "active", "queued": 5, "created_at": now - timedelta(minutes=2), "workflow_type": "offline",
           "source": "selection", "requested": 6, "skipped_in_progress": 1, "not_found": 0}
    read = jobs.job_read("j1", job, _progress(in_progress=2, passed=1, failed=1), now=now)
    assert read["state"] == "active" and read["counters_source"] == "redis"
    assert read["progress"] == {"started": 4, "in_progress": 2, "completed": 0, "passed": 1, "failed": 1,
                                "not_started": 1}

    finished = jobs.job_read("j1", job, _progress(passed=5), now=now)
    assert finished["state"] == "finished" and finished["note"] is None


def test_a_task_that_never_started_is_noted_after_the_settle_window_never_failed():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    young = {"state": "active", "queued": 4, "created_at": now - timedelta(seconds=BULK_JOB_SETTLE_SECONDS - 60)}
    old = {**young, "created_at": now - timedelta(seconds=BULK_JOB_SETTLE_SECONDS + 1)}
    assert jobs.job_read("j", young, _progress(passed=3), now=now)["state"] == "active"
    read = jobs.job_read("j", old, _progress(passed=3), now=now)
    assert read["state"] == "finished" and read["note"] == BULK_NOT_STARTED_NOTE.format(n=1)
    assert read["progress"]["failed"] == 0 and read["progress"]["not_started"] == 1
    assert jobs.job_read("j", {**old, "state": "cancelled"}, _progress(passed=3), now=now)["state"] == "cancelled"


def test_without_the_record_the_counts_are_unknown_never_zero():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    read = jobs.job_read("j", None, {**_progress(passed=2, failed=1), "workflow_type": "deep"}, now=now)
    assert read["counters_source"] == "postgres" and read["workflow_type"] == "deep"
    assert read["requested"] is None and read["queued"] is None and read["not_found"] is None
    assert read["progress"]["not_started"] is None and read["state"] == "finished"
    from app.models.schemas import BulkJobRead

    BulkJobRead.model_validate(read)


@pytest.mark.asyncio
async def test_is_cancelled_is_false_on_any_redis_error_and_true_only_for_a_cancelled_record():
    redis = FakeRedis()
    with patch.object(jobs, "_redis", lambda: redis):
        await jobs.create_job(job_id="j", requested_by=uuid.uuid4(), workflow_type="offline", source="selection",
                              requested=1, queued_rows=[(uuid.uuid4(), P1, "b")], skipped=0, not_found=0,
                              matched=None, truncated=False, queued_per_project={P1: 1})
        assert await jobs.is_cancelled("j") is False
        await jobs.mark_cancelled("j", by=uuid.uuid4(), now=datetime.now(timezone.utc))
        assert await jobs.is_cancelled("j") is True
        assert await jobs.is_cancelled(None) is False
        assert await jobs.mark_cancelled("gone", by=uuid.uuid4(), now=datetime.now(timezone.utc)) is None
    with patch.object(jobs, "_redis", MagicMock(side_effect=ConnectionError("down"))):
        assert await jobs.is_cancelled("j") is False
        assert await jobs.read_job("j") is None
