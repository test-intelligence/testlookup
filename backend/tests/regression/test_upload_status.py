"""Regression: Redis-backed upload status (PRD MRU-5/6).

Pins the status record the upload task writes and the /ingest/uploads/{task_id}
endpoint reads, so async parse/ingest outcomes surface to the UI instead of a
silent background run.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import upload_status  # noqa: E402


def _fake_redis_with_store():
    store: dict[str, str] = {}
    fake = AsyncMock()

    async def _set(key, value, ex=None):
        store[key] = value

    async def _get(key):
        return store.get(key)

    fake.set = AsyncMock(side_effect=_set)
    fake.get = AsyncMock(side_effect=_get)
    return fake, store


@pytest.mark.asyncio
async def test_set_and_get_status_roundtrip():
    fake, _ = _fake_redis_with_store()
    with patch("app.db.redis_client.get_redis", return_value=fake):
        await upload_status.set_status(
            "t1", run_id="r1", project_id="p1",
            state=upload_status.STATE_SUCCEEDED,
            result={"total": 3, "passed": 2, "failed": 1},
        )
        rec = await upload_status.get_status("t1")

    assert rec is not None
    assert rec["state"] == "succeeded"
    assert rec["run_id"] == "r1"
    assert rec["project_id"] == "p1"
    assert rec["result"] == {"total": 3, "passed": 2, "failed": 1}
    # Record carries a TTL so it self-expires.
    assert fake.set.call_args.kwargs.get("ex") == upload_status.STATUS_TTL_SECONDS


@pytest.mark.asyncio
async def test_get_status_missing_returns_none():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    with patch("app.db.redis_client.get_redis", return_value=fake):
        assert await upload_status.get_status("nope") is None


@pytest.mark.asyncio
async def test_set_status_swallows_redis_errors():
    """A Redis blip must never fail the ingest itself — status is advisory."""
    fake = AsyncMock()
    fake.set = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch("app.db.redis_client.get_redis", return_value=fake):
        # Must not raise.
        await upload_status.set_status("t1", state=upload_status.STATE_PARSING)


def test_summarize_upload_uses_only_accepted_status_counts():
    """A rejected row must not make the breakdown exceed the accepted total."""
    from app.worker.tasks import _summarize_upload

    summary = _summarize_upload(
        ingested=4,
        attempted=5,
        rejected=1,
        status_counts={"PASSED": 2, "FAILED": 1, "BROKEN": 1},
    )

    assert summary["total"] == 4
    assert summary["attempted"] == 5
    assert summary["rejected"] == 1
    assert summary["complete"] is False
    assert summary["passed"] == 2
    assert summary["failed"] == 1
    assert summary["broken"] == 1
    assert summary["skipped"] == 0
    assert sum(summary[key] for key in ("passed", "failed", "broken", "skipped", "unknown")) == 4


def test_all_rejected_file_upload_finalizes_without_ai_before_reporting_failure():
    """An all-rejected upload must still get end-time/release finalization."""
    import asyncio
    import uuid
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services import ingestion_pipeline
    from app.worker import tasks

    run = SimpleNamespace(
        id=uuid.uuid4(),
        ingestion_attempted_tests=None,
        ingestion_rejected_tests=0,
        ingestion_complete=None,
        ingestion_rejection_reasons=None,
    )
    finalizer = AsyncMock()
    status_events: list[str] = []

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    async def _reject_all(_db, target_run, _results):
        target_run.ingestion_attempted_tests = 1
        target_run.ingestion_rejected_tests = 1
        target_run.ingestion_complete = False
        target_run.ingestion_rejection_reasons = [{"row_index": 0}]
        return 0

    async def _status(_task_id, *, state, **_kwargs):
        status_events.append(state)

    with (
        patch.object(tasks, "_run_async", side_effect=asyncio.run),
        patch.object(tasks, "_archive_raw_upload", new=AsyncMock(return_value=None)),
        patch.object(
            tasks,
            "_parse_file_to_results",
            return_value=[{"test_name": "bad", "status": "passed"}],
        ),
        patch.object(ingestion_pipeline, "create_run_from_payload", new=AsyncMock(return_value=run)),
        patch.object(ingestion_pipeline, "ingest_test_results", side_effect=_reject_all),
        patch.object(ingestion_pipeline, "finalize_run", finalizer),
        patch("app.db.postgres.AsyncSessionLocal", _session),
        patch("app.services.upload_status.set_status", side_effect=_status),
        patch("app.core.metrics.uploads_total", MagicMock()),
        patch("app.core.metrics.upload_failures_total", MagicMock()),
    ):
        tasks.ingest_uploaded_file.run(
            run_id=str(run.id),
            file_name="report.xml",
            file_format="junit",
            project_id=str(uuid.uuid4()),
            build_number="build-1",
            file_content="<testsuite />",
            release_name="release-1",
            run_ai=True,
        )

    finalizer.assert_awaited_once()
    assert finalizer.await_args.kwargs["release_name"] == "release-1"
    assert finalizer.await_args.kwargs["run_ai"] is False
    assert status_events[-1] == upload_status.STATE_FAILED


def test_parse_file_malformed_allure_raises():
    """Malformed Allure JSON must raise (→ parse_error status), not silently
    return [] (which would yield a misleading empty run)."""
    from app.worker.tasks import _parse_file_to_results

    with pytest.raises(ValueError):
        _parse_file_to_results("not valid json", "allure", "x.json", "run-1")


@pytest.mark.asyncio
async def test_failed_status_carries_structured_error():
    fake, _ = _fake_redis_with_store()
    with patch("app.db.redis_client.get_redis", return_value=fake):
        await upload_status.set_status(
            "t2", run_id="r2", project_id="p2",
            state=upload_status.STATE_FAILED,
            error={"code": "parse_error", "message": "bad xml"},
        )
        rec = await upload_status.get_status("t2")

    assert rec["state"] == "failed"
    assert rec["error"] == {"code": "parse_error", "message": "bad xml"}


@pytest.mark.asyncio
async def test_finalize_run_gates_ai_pipeline_on_run_ai():
    """MRU-8: finalize_run(run_ai=False) must NOT enqueue the agent pipeline;
    run_ai=True must. The other steps are best-effort and run on a mock session."""
    import uuid as _uuid
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services import ingestion_pipeline as ip

    @asynccontextmanager
    async def _sess():
        yield AsyncMock()

    common = dict(run_id=str(_uuid.uuid4()), project_id=str(_uuid.uuid4()), build_number="b")
    with patch.object(ip, "AsyncSessionLocal", _sess), \
         patch.object(ip, "_update_run_aggregates", AsyncMock()), \
         patch("app.worker.tasks.run_agent_pipeline") as pipeline, \
         patch("app.worker.tasks.dispatch_run_notifications", MagicMock()), \
         patch("app.worker.tasks.precompute_suite_comparisons_for_run", MagicMock()):
        await ip.finalize_run(**common, run_ai=False)
        pipeline.delay.assert_not_called()

        await ip.finalize_run(**common, run_ai=True)
        pipeline.delay.assert_called_once()
