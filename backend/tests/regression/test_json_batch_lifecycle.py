"""The JSON batch route has a pollable lifecycle, and its failures are visible.

Product findings D-69, D-05 (worker half), S-C-6, D-19 (ingest half), S-C-5:

* D-69: ``POST /api/v1/ingest`` answered a ``task_id`` that
  ``GET /api/v1/ingest/uploads/{task_id}`` could never find (404 always). It now
  seeds "pending" before publishing and the worker writes ingesting, then
  succeeded (with the summary) or failed.
* D-05: re-sending a deleted build met the tombstone in the worker as a plain
  ValueError, retried three times and died with only a log line.
* S-C-6: every batch failure was retried as if transient, left no activity and
  no status, and left its queued object in storage.
* D-19: the JSON route could not carry an execution time at all.
* S-C-5: a malformed ``commit_range`` on the file form was dropped after the 202
  with only a log line; a failed store was a warning only.

The publish is recorded with the task's REAL argument check (``__header__``) and
signature: a mocked ``apply_async`` cannot see a payload that does not fit.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.deps import (  # noqa: E402
    get_api_key_context,
    get_current_active_user,
    get_current_user,
    get_current_user_or_api_key,
    get_db,
)
from app.main import app  # noqa: E402
from app.models.schemas import IngestPayload  # noqa: E402
from app.services import upload_status  # noqa: E402
from app.services.ingestion_pipeline import RunTombstoned  # noqa: E402
from app.worker import tasks  # noqa: E402
from tests.regression.test_upload_status import _fake_redis_with_store  # noqa: E402


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        self.objects[key] = content if isinstance(content, bytes) else str(content).encode()

    async def delete_object(self, key, bucket=None):
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def get_object_content(self, key, bucket=None):
        return self.objects[key]


def _batch(project_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": str(project_id),
        "build_number": "nightly-1858",
        "results": [
            {"test_name": "checkout_flow", "status": "PASSED"},
            {"test_name": "refund_flow", "status": "FAILED"},
        ],
        **extra,
    }


@pytest.fixture
def world(monkeypatch, upload_receipt_store):
    """The real app and real upload_status, over an in-memory Redis and storage."""
    project_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(), username="qa_runner", email="qa@example.com", full_name="QA",
        role="QA_ENGINEER", is_active=True, must_change_password=False,
    )
    storage = _Storage()
    redis, store = _fake_redis_with_store()
    published: list[dict] = []

    async def _db():
        yield AsyncMock()

    async def _api_key_ctx():
        return user, None

    async def _user():
        return user

    overrides = {
        get_db: _db, get_api_key_context: _api_key_ctx, get_current_user: _user,
        get_current_active_user: _user, get_current_user_or_api_key: _user,
    }
    app.dependency_overrides.update(overrides)

    task = tasks.ingest_uploaded_results

    def _apply_async(args=None, kwargs=None, task_id=None, **_options):
        args, kwargs = tuple(args or ()), dict(kwargs or {})
        task.__header__(*args, **kwargs)  # Celery's own argument check
        published.append({
            "task_id": task_id,
            "kwargs": inspect.signature(task.run).bind(*args, **kwargs).arguments,
        })
        return SimpleNamespace(id=task_id)

    async def _open(*_a, **_kw):
        return None

    monkeypatch.setattr(task, "apply_async", _apply_async)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    monkeypatch.setattr("app.services.ingestion_backpressure.enforce_redis_memory_backpressure", _open)
    monkeypatch.setattr("app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _open)
    monkeypatch.setattr("app.routers.ingest.resolve_project_scope", AsyncMock())
    monkeypatch.setattr("app.routers.ingest.record_activity", AsyncMock())
    monkeypatch.setattr("app.routers.ingest.run_is_tombstoned", AsyncMock(return_value=False))
    run_id = str(uuid.uuid4())
    monkeypatch.setattr("app.routers.ingest._resolve_run_id", AsyncMock(return_value=run_id))
    try:
        yield SimpleNamespace(
            project_id=project_id, storage=storage, store=store, published=published, run_id=run_id,
            receipts=upload_receipt_store,
        )
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


async def _post(path: str, **kwargs: Any):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(path, **kwargs)


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.get(path)


@pytest.fixture
def capture_durable_status(world, monkeypatch):
    from app.worker import upload_receipts as boundary

    def capture(task_id):
        row = world.receipts[task_id]

        async def persist(receipt_id, *, state, **status_payload):
            assert receipt_id == task_id
            row.state = state
            row.status_payload = status_payload

        monkeypatch.setattr(boundary, "active_receipt_id", lambda: task_id)
        monkeypatch.setattr(boundary, "persist_active_status", persist)

    return capture


@contextmanager
def _worker(*, create=None, ingested: int = 2, retries: int = 0, task_id: str = "batch-task-1"):
    """The REAL task body with a Celery request pushed and its DB seams replaced."""
    from app.services import ingestion_pipeline

    run = SimpleNamespace(
        id=uuid.uuid4(), ingestion_attempted_tests=ingested, ingestion_rejected_tests=0,
        _ingestion_accepted_status_counts={"PASSED": 1, "FAILED": 1} if ingested else {},
    )
    if create is None:
        create = AsyncMock(return_value=run)

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    activity = AsyncMock()
    retry = MagicMock(side_effect=RuntimeError("retry requested"))
    task = tasks.ingest_uploaded_results
    with (
        patch.object(tasks, "_run_async", side_effect=asyncio.run),
        patch.object(tasks, "reindex_search", MagicMock()),
        patch.object(tasks, "_record_worker_activity", new=activity),
        patch.object(task, "retry", new=retry),
        patch.object(ingestion_pipeline, "create_run_from_payload", new=create),
        patch.object(ingestion_pipeline, "ingest_test_results", new=AsyncMock(return_value=ingested)),
        patch.object(ingestion_pipeline, "finalize_run", new=AsyncMock()),
        patch("app.db.postgres.AsyncSessionLocal", _session),
        patch("app.services.cache_service.bump_analytics_epoch", new=AsyncMock()),
    ):
        task.push_request(id=task_id, retries=retries)
        try:
            yield SimpleNamespace(task=task, create=create, activity=activity, retry=retry, run=run)
        finally:
            task.pop_request()


def _status(world, task_id: str) -> dict | None:
    raw = world.store.get(f"upload:status:{task_id}")
    return json.loads(raw) if raw else None


# ── D-69: POST then GET ──────────────────────────────────────────────────────


async def test_a_batch_task_id_can_be_polled_straight_away(world):
    resp = await _post("/api/v1/ingest", json=_batch(world.project_id))
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]

    (published,) = world.published
    assert published["task_id"] == task_id, "the published task must carry the id the caller polls"
    assert published["kwargs"]["run_id"] == world.run_id

    poll = await _get(f"/api/v1/ingest/uploads/{task_id}")
    assert poll.status_code == 200, poll.text
    assert poll.json()["state"] == "pending"
    assert poll.json()["run_id"] == world.run_id


async def test_batch_worker_reports_success_and_keeps_payload_for_durable_cleanup(world, capture_durable_status):
    resp = await _post("/api/v1/ingest", json=_batch(world.project_id))
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    (published,) = world.published
    capture_durable_status(task_id)

    def _run():
        with _worker(task_id=task_id) as w:
            w.task.run(**published["kwargs"])
            return w

    w = await asyncio.to_thread(_run)

    poll = await _get(f"/api/v1/ingest/uploads/{task_id}")
    assert poll.status_code == 200, poll.text
    body = poll.json()
    assert body["state"] == "succeeded", body
    assert body["result"]["total"] == 2
    assert body["result"]["passed"] == 1 and body["result"]["failed"] == 1
    w.retry.assert_not_called()
    assert published["kwargs"]["payload_storage_key"] in world.storage.objects


async def test_an_unconfirmed_publish_is_a_503_with_a_retained_payload_and_poll_id(world, monkeypatch):
    def _broken(*_a, **_k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.ingest_uploaded_results, "apply_async", _broken)
    resp = await _post("/api/v1/ingest", json=_batch(world.project_id))
    assert resp.status_code == 503, resp.text
    assert len(world.storage.objects) == 1, "an accepted message may still need its batch"
    task_id = resp.headers["X-Upload-Task-ID"]
    poll = await _get(f"/api/v1/ingest/uploads/{task_id}")
    assert poll.status_code == 200 and poll.json()["state"] == "pending"
    assert resp.headers["Location"] == f"/api/v1/ingest/uploads/{task_id}"


# ── D-05 / S-C-6: terminal refusals in the worker ───────────────────────────


def _seed(world, task_id: str, payload: dict) -> str:
    key = f"uploads/{world.project_id}/{world.run_id}/queued/x.json"
    world.storage.objects[key] = json.dumps(payload).encode()
    asyncio.run(upload_status.set_status(
        task_id, run_id=world.run_id, project_id=str(world.project_id),
        state=upload_status.STATE_PENDING,
    ))
    return key


def test_a_deleted_run_is_refused_once_without_a_retry(world):
    key = _seed(world, "t-del", _batch(world.project_id))
    create = AsyncMock(side_effect=RunTombstoned("run was deleted"))
    with _worker(create=create, task_id="t-del") as w:
        w.task.run(run_id=world.run_id, payload_storage_key=key)

    w.retry.assert_not_called()
    w.activity.assert_awaited_once()
    assert w.activity.await_args.args == ("run.ingest_failed",)
    kwargs = w.activity.await_args.kwargs
    assert kwargs["context"]["error"] == "run_deleted"
    assert kwargs["project_id"] == str(world.project_id)
    assert kwargs["build_number"] == "nightly-1858"
    status = _status(world, "t-del")
    assert status["state"] == "failed"
    assert status["error"]["code"] == "run_deleted"
    assert "new build number" in status["error"]["message"]
    assert key in world.storage.deleted and key not in world.storage.objects


def test_a_batch_for_a_missing_project_is_terminal_and_cleaned_up(world):
    key = _seed(world, "t-proj", _batch(world.project_id))
    create = AsyncMock(side_effect=ValueError(f"Project {world.project_id} not found"))
    with _worker(create=create, task_id="t-proj") as w:
        w.task.run(run_id=world.run_id, payload_storage_key=key)

    w.retry.assert_not_called()
    assert w.activity.await_args.kwargs["context"]["error"] == "invalid_batch"
    assert _status(world, "t-proj")["state"] == "failed"
    assert key not in world.storage.objects


def test_an_unreadable_batch_still_reports_against_the_seeded_project(world):
    key = f"uploads/{world.project_id}/{world.run_id}/queued/bad.json"
    world.storage.objects[key] = b"not json"
    asyncio.run(upload_status.set_status(
        "t-bad", run_id=world.run_id, project_id=str(world.project_id),
        state=upload_status.STATE_PENDING,
    ))
    with _worker(task_id="t-bad") as w:
        w.task.run(run_id=world.run_id, payload_storage_key=key)

    w.retry.assert_not_called()
    status = _status(world, "t-bad")
    assert status["state"] == "failed" and status["project_id"] == str(world.project_id)
    assert w.activity.await_args.kwargs["project_id"] == str(world.project_id)
    assert key not in world.storage.objects


def test_a_transient_failure_is_retried_and_reported_only_when_retries_are_spent(world):
    key = _seed(world, "t-db", _batch(world.project_id))
    create = AsyncMock(side_effect=ConnectionError("db reset"))
    with _worker(create=create, task_id="t-db", retries=0) as w:
        with pytest.raises(RuntimeError, match="retry requested"):
            w.task.run(run_id=world.run_id, payload_storage_key=key)
    w.retry.assert_called_once()
    w.activity.assert_not_awaited()
    assert _status(world, "t-db")["state"] != "failed"
    assert key in world.storage.objects, "a retry must still find its batch"

    with _worker(create=create, task_id="t-db", retries=3) as w:
        with pytest.raises(RuntimeError, match="retry requested"):
            w.task.run(run_id=world.run_id, payload_storage_key=key)
    status = _status(world, "t-db")
    assert status["state"] == "failed" and status["error"]["code"] == "infra_error"
    assert w.activity.await_args.kwargs["context"]["error"] == "infra_error"


# ── D-19: the JSON route carries the execution time ──────────────────────────


async def test_a_batch_executed_at_is_stored_and_handed_to_create_run(world):
    ran = datetime.now(timezone.utc) - timedelta(days=10)
    resp = await _post("/api/v1/ingest", json=_batch(world.project_id, executed_at=ran.isoformat()))
    assert resp.status_code == 202, resp.text
    (published,) = world.published
    stored = json.loads(world.storage.objects[published["kwargs"]["payload_storage_key"]])
    assert stored["executed_at"].startswith(ran.strftime("%Y-%m-%dT%H:%M:%S"))

    def _run():
        with _worker(task_id=resp.json()["task_id"]) as w:
            w.task.run(**published["kwargs"])
            return w.create.await_args.kwargs

    got = await asyncio.to_thread(_run)
    assert got["executed_at"] == ran


def test_a_batch_without_executed_at_dumps_exactly_as_before():
    payload = IngestPayload(**_batch(uuid.uuid4()))
    assert "executed_at" not in payload.model_dump(mode="json")


@pytest.mark.asyncio
async def test_create_run_sets_start_time_from_executed_at():
    """The pipeline half the file route already had: start_time is the execution time."""
    from app.services import ingestion_pipeline

    ran = datetime.now(timezone.utc) - timedelta(days=3)
    added: list = []

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Result(SimpleNamespace(id=uuid.uuid4())), _Result(None), _Result(None)])
    db.add = added.append
    db.flush = AsyncMock()
    with patch.object(ingestion_pipeline, "run_is_tombstoned", new=AsyncMock(return_value=False)):
        run = await ingestion_pipeline.create_run_from_payload(
            db, project_id=str(uuid.uuid4()), build_number="b-1", run_id=str(uuid.uuid4()),
            ingestion_source="sdk", ci_run_url="https://ci.example.com/1", executed_at=ran,
        )
    assert run.start_time == ran


# ── S-C-5: a supplied commit range is never dropped silently ────────────────


def test_a_commit_range_that_is_not_json_is_a_422():
    from app.routers.ingest import _parse_form_commit_range

    for bad in ("not json", "42", '"a string"'):
        with pytest.raises(HTTPException) as refused:
            _parse_form_commit_range(bad)
        assert refused.value.status_code == 422


def test_a_long_commit_list_is_cut_and_said_so():
    from app.routers.ingest import MAX_SUPPLIED_COMMITS, _parse_form_commit_range

    commits = [{"sha": f"{i:07x}"} for i in range(MAX_SUPPLIED_COMMITS + 5)]
    value, truncated = _parse_form_commit_range(json.dumps(commits))
    assert truncated is True and len(value) == MAX_SUPPLIED_COMMITS
    value, truncated = _parse_form_commit_range(json.dumps({"base": "a1", "commits": commits[:3]}))
    assert truncated is False and value["base"] == "a1" and len(value["commits"]) == 3


async def test_the_file_form_refuses_a_malformed_commit_range_before_storing(world, monkeypatch):
    file_task = MagicMock()
    monkeypatch.setattr("app.worker.tasks.ingest_uploaded_file", file_task)
    resp = await _post(
        "/api/v1/ingest/file",
        data={"project_id": str(world.project_id), "build_number": "b-cr", "format": "junit",
              "commit_range": "not json"},
        files={"file": ("r.xml", b'<testsuite name="s"><testcase name="t"/></testsuite>', "application/xml")},
    )
    assert resp.status_code == 422, resp.text
    assert world.storage.objects == {}
    file_task.apply_async.assert_not_called()


def test_the_file_task_accepts_the_truncation_flag():
    task = tasks.ingest_uploaded_file
    kwargs = dict(
        run_id="r", file_name="r.xml", file_format="junit", project_id="p", build_number="b",
        file_storage_key="k", commit_range_truncated=True,
    )
    task.__header__(**kwargs)
    inspect.signature(task.run).bind(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store", "expected"),
    [
        (AsyncMock(return_value=object()), "stored"),
        (AsyncMock(return_value=None), "ignored_malformed"),
        (AsyncMock(side_effect=RuntimeError("db")), "store_failed"),
    ],
)
async def test_the_commit_range_outcome_is_kept_on_the_run(store, expected):
    from app.services import ingestion_pipeline

    run = SimpleNamespace(id=uuid.uuid4())
    with patch("app.services.commit_attribution_service.store_supplied_range", new=store):
        got = await ingestion_pipeline._store_supplied_commit_range(MagicMock(), run, [{"sha": "abc1234"}])
    assert got == expected and run._commit_range_outcome == expected


def test_the_upload_summary_reports_the_commit_range_outcome():
    run = SimpleNamespace(_commit_range_outcome="store_failed")
    assert tasks._commit_range_status(run) == "store_failed"
    assert tasks._commit_range_status(SimpleNamespace(_commit_range_outcome="stored"), truncated=True) == "truncated"
    assert tasks._commit_range_status(SimpleNamespace()) is None
    summary = tasks._summarize_upload(
        ingested=1, attempted=1, rejected=0, status_counts={}, commit_range="store_failed",
    )
    assert summary["commit_range"] == "store_failed"
    assert "commit_range" not in tasks._summarize_upload(ingested=1, attempted=1, rejected=0, status_counts={})


def test_the_upload_form_names_every_format_the_server_reads():
    """D-71: the form's format line left out pytest, which the server reads."""
    import re
    from pathlib import Path

    from app.routers.ingest import _SUPPORTED_FORMATS

    modal = Path(__file__).parents[3] / "frontend" / "src" / "components" / "runs" / "UploadReportModal.tsx"
    if not modal.exists():
        pytest.skip("frontend sources are not in this checkout")
    description = re.search(r'description="([^"]*JUnit[^"]*)"', modal.read_text(encoding="utf-8"))
    assert description, "the upload form's format line was not found"
    missing = sorted(f for f in _SUPPORTED_FORMATS - {"auto"} if f not in description.group(1).lower())
    assert missing == [], f"the upload form does not name: {missing}"


async def test_a_batch_commit_range_failure_reaches_the_poll(world, capture_durable_status):
    resp = await _post("/api/v1/ingest", json=_batch(world.project_id, commit_range=[{"sha": "abc1234"}]))
    task_id = resp.json()["task_id"]
    (published,) = world.published
    capture_durable_status(task_id)

    def _run():
        with _worker(task_id=task_id) as w:
            w.run._commit_range_outcome = "store_failed"
            w.task.run(**published["kwargs"])

    await asyncio.to_thread(_run)
    poll = await _get(f"/api/v1/ingest/uploads/{task_id}")
    assert poll.json()["result"]["commit_range"] == "store_failed"
