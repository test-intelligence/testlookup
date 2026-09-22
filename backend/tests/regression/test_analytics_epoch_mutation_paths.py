"""VIZ-212: every analytics mutation path bumps the project's epoch AFTER commit.

Analytics cache keys carry a per-project epoch (``cache_service``). Before this
story only batch ingestion invalidated the cache, so a run deletion, a retention
purge, a project reset, every change of a run's primary release, a live drain or
close, and a deactivation all left cached reports answering from the old data
until TTL.

One test per path. Each drives the real entry point with its collaborators
stubbed, records ``commit`` and ``bump`` into ONE journal, and asserts the bump
happened, for the right project, after the commit. Removing the single bump call
of a path fails its test (mutation-checked when this file was written). A
rollback, and a no-op, must not bump.

The static half — that no committing caller of a mutation is missing its bump —
is the ``backend.analytics-epoch-bump`` quality-gate guard.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression


# ── Journal: commits and bumps in the order they happened ────────────────────


@pytest.fixture
def journal(monkeypatch) -> list[tuple[str, str]]:
    """Replace both bump entry points with recorders. The sweep recorder
    mirrors the real helper's contract — each distinct project once, falsy
    ids skipped — which ``test_analytics_epoch_cache`` pins on the real one."""
    from app.services import cache_service

    events: list[tuple[str, str]] = []

    async def _bump(project_id):
        if project_id:
            events.append(("bump", str(project_id)))

    async def _bumps_many(project_ids):
        seen: list[str] = []
        for project_id in project_ids:
            if project_id and str(project_id) not in seen:
                seen.append(str(project_id))
        for project_id in seen:
            events.append(("bump", project_id))

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", _bump)
    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _bumps_many)
    return events


def _bumps(journal) -> list[str]:
    return [pid for kind, pid in journal if kind == "bump"]


def _mutating(journal, name: str, return_value=None) -> AsyncMock:
    """A stubbed mutation that records WHEN it ran in the journal."""

    async def _run(*_a, **_k):
        journal.append(("mutate", name))
        return return_value

    return AsyncMock(side_effect=_run)


def _assert_bumped_after_commit(journal, project_id) -> None:
    """The project is bumped after a commit that follows the LAST recorded
    mutation. "Some commit before the first bump" is not enough: a path that
    commits, mutates again and bumps without a second commit bumps before the
    commit that makes its last change durable."""
    target = ("bump", str(project_id))
    assert target in journal, (
        f"no analytics epoch bump for {project_id}: cached reports stay stale "
        f"until TTL (journal={journal})"
    )
    last_mutation = max(
        (i for i, (kind, _) in enumerate(journal) if kind == "mutate"), default=-1
    )
    commits_after = [
        i for i, (kind, _) in enumerate(journal) if kind == "commit" and i > last_mutation
    ]
    assert commits_after, (
        f"no commit after the last mutation (journal={journal}) — the change "
        "the bump announces is not durable yet"
    )
    assert any(event == target and i > commits_after[0] for i, event in enumerate(journal)), (
        f"the bump ran before the commit (journal={journal}) — a reader in "
        "between re-caches the old data under the new epoch"
    )


@pytest.mark.parametrize(
    "events",
    [
        [("commit", ""), ("mutate", "m"), ("bump", "p")],  # committed before the mutation
        [("mutate", "m"), ("bump", "p"), ("commit", "")],  # bumped before the commit
        [("mutate", "m"), ("commit", ""), ("bump", "q")],  # another project's bump
        [("mutate", "m"), ("commit", ""), ("mutate", "m2"), ("bump", "p")],
    ],
    ids=["commit-then-mutate", "bump-then-commit", "wrong-project", "second-mutation"],
)
def test_the_ordering_check_rejects_a_bump_that_precedes_the_durable_commit(events):
    with pytest.raises(AssertionError):
        _assert_bumped_after_commit(list(events), "p")
    _assert_bumped_after_commit([("mutate", "m"), ("commit", ""), ("bump", "p")], "p")


class _Session:
    """Async-context session whose commit is journaled (and can fail)."""

    def __init__(self, journal, *, scalar=None, results=None, fail_commit=False):
        self.journal = journal
        self._scalar = scalar
        self._results = list(results or [])
        self.fail_commit = fail_commit
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        if self._results:
            return self._results.pop(0)
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalar
        result.scalars.return_value.all.return_value = []
        return result

    async def scalar(self, *_a, **_k):
        return self._scalar

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.journal.append(("commit", ""))

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, *_a, **_k):
        return None

    async def flush(self):
        return None

    def add(self, *_a, **_k):
        return None


def _factory(*sessions):
    pending = list(sessions)
    return lambda: pending.pop(0)


def _run_tasks_inline(monkeypatch):
    from app.worker import tasks

    monkeypatch.setattr(tasks, "_run_async", asyncio.run)


# ── Batch ingestion: finalize_run / process_sentinel call the invalidator ────


async def test_finalize_run_invalidates_the_cache_before_and_after_poststeps(
    journal, monkeypatch
):
    """Drives the REAL ``finalize_run``, not just the helper it calls. The
    early invalidation (right after the outbox-staging commit) and the final
    one (after the readiness-gate commit) are both required — either one
    being removed must fail this test."""
    from app.services import cache_service, ingestion_pipeline
    from app.services import run_downstream_outbox

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run_row = SimpleNamespace(
        id=run_id, project_id=project_id, build_number="b1", total_tests=1,
        passed_tests=1, failed_tests=0, skipped_tests=0, duration_ms=1,
        jenkins_job=None, primary_release_id=None,
    )
    project_row = SimpleNamespace(id=project_id)

    class _FirstStepSession:
        """Step 1's session: only the two selects it names are answered."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, stmt, *_a, **_k):
            result = MagicMock()
            compiled = str(stmt)
            if "test_runs" in compiled:
                result.scalar_one_or_none.return_value = run_row
            elif "projects" in compiled:
                result.scalar_one_or_none.return_value = project_row
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        async def commit(self):
            journal.append(("commit", ""))

        async def rollback(self):
            journal.append(("rollback", ""))

        async def scalar(self, *_a, **_k):
            return None

    class _LaterStepSession(_FirstStepSession):
        """Every isolated post-step and the readiness gate: anything they
        touch beyond a bare SELECT raises, and ``_run_isolated_step`` (or the
        try/except around the readiness gate) swallows it — this test's only
        interest is the two invalidations around them."""

        async def execute(self, *_a, **_k):
            raise RuntimeError("no real DB in this test")

    sessions = iter([_FirstStepSession(), *([_LaterStepSession()] * 40)])
    monkeypatch.setattr(ingestion_pipeline, "AsyncSessionLocal", lambda: next(sessions))
    monkeypatch.setattr(ingestion_pipeline, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr(
        run_downstream_outbox, "stage_finalize_operations", AsyncMock()
    )
    # The readiness gate (_activate_finalize_children) deliberately RE-RAISES
    # on failure rather than swallowing like the isolated steps — it must
    # succeed for the final invalidation to run at all.
    monkeypatch.setattr(
        run_downstream_outbox, "activate_finalize_operations", AsyncMock(return_value=0)
    )
    invalidations: list[str] = []

    async def _fake_invalidate(project_id=None):
        invalidations.append(str(project_id))

    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", _fake_invalidate)

    await ingestion_pipeline.finalize_run(
        run_id=str(run_id), project_id=str(project_id), build_number="b1",
    )

    assert invalidations == [str(project_id), str(project_id)], (
        "finalize_run must invalidate the analytics cache both right after "
        "staging (so a slow post-step window is still covered) and again "
        "after the readiness-gate commit"
    )


async def test_process_sentinel_invalidates_the_cache_after_commit(journal, monkeypatch):
    """Drives the REAL ``process_sentinel`` ingestion entry point end to end,
    with the S3/Mongo/parsing collaborators stubbed the way this module's own
    tests already stub them."""
    from app.models.schemas import SentinelFile
    from app.services import cache_service, ingestion

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run_row = SimpleNamespace(
        id=run_id, project_id=project_id, build_number="b1",
        ingestion_attempted_tests=0, ingestion_rejected_tests=0,
        ingestion_complete=False, ingestion_rejection_reasons=None,
        ocp_pod_name=None, ocp_namespace=None, start_time=None,
    )

    class _SentinelSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, *_a, **_k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None  # no project row -> raises
            result.scalars.return_value.all.return_value = []
            return result

        async def commit(self):
            journal.append(("commit", ""))

        async def rollback(self):
            journal.append(("rollback", ""))

    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: _SentinelSession())
    monkeypatch.setattr(ingestion, "_upsert_test_run", AsyncMock(return_value=run_row))
    storage = SimpleNamespace(
        list_objects=AsyncMock(return_value=[]),
        get_object_content=AsyncMock(return_value=b"{}"),
    )
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", AsyncMock(return_value={}))
    monkeypatch.setattr(ingestion, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr(
        "app.services.run_downstream_outbox.stage_finalize_operations", AsyncMock()
    )

    invalidations: list[str] = []

    async def _fake_invalidate(project_id=None):
        invalidations.append(str(project_id))

    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", _fake_invalidate)

    sentinel = SentinelFile(
        project_id=str(project_id), build_number="b1", ocp_pod_name=None,
        ocp_namespace=None, release_name=None,
    )

    # The project-lookup ``None`` raises inside the try (before staging), the
    # outer except rolls back and re-raises — proving the invalidation ONLY
    # happens on the success path is as important as proving it happens at all.
    with pytest.raises(RuntimeError, match="disappeared"):
        await ingestion.process_sentinel(sentinel, "prefix/")

    assert invalidations == []
    assert ("rollback", "") in journal


async def test_process_sentinel_commits_and_invalidates_on_the_happy_path(
    journal, monkeypatch
):
    from app.models.postgres import Project as ProjectModel
    from app.models.schemas import SentinelFile
    from app.services import cache_service, ingestion

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run_row = SimpleNamespace(
        id=run_id, project_id=project_id, build_number="b1",
        ingestion_attempted_tests=0, ingestion_rejected_tests=0,
        ingestion_complete=False, ingestion_rejection_reasons=None,
        ocp_pod_name=None, ocp_namespace=None, start_time=None,
    )
    project_row = ProjectModel(id=project_id, name="p", slug="p")

    class _SentinelSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, stmt, *_a, **_k):
            result = MagicMock()
            compiled = str(stmt)
            if "projects" in compiled:
                result.scalar_one_or_none.return_value = project_row
            else:
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = []
            return result

        async def commit(self):
            journal.append(("commit", ""))

        async def rollback(self):
            journal.append(("rollback", ""))

    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: _SentinelSession())
    monkeypatch.setattr(ingestion, "_upsert_test_run", AsyncMock(return_value=run_row))
    storage = SimpleNamespace(
        list_objects=AsyncMock(return_value=[]),
        get_object_content=AsyncMock(return_value=b"{}"),
    )
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", AsyncMock(return_value={}))
    monkeypatch.setattr(ingestion, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr(
        "app.services.run_downstream_outbox.stage_finalize_operations", AsyncMock()
    )

    invalidations: list[str] = []

    async def _fake_invalidate(project_id=None):
        invalidations.append(str(project_id))

    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", _fake_invalidate)

    sentinel = SentinelFile(
        project_id=str(project_id), build_number="b1", ocp_pod_name=None,
        ocp_namespace=None, release_name=None,
    )

    await ingestion.process_sentinel(sentinel, "prefix/")

    assert invalidations == [str(project_id)], (
        "process_sentinel's success path must invalidate the analytics cache "
        "after its commit"
    )
    assert ("commit", "") in journal


async def test_invalidate_analytics_cache_bumps_the_epoch(journal, monkeypatch):
    """Batch ingestion (``finalize_run`` twice, ``process_sentinel``) reaches
    the epoch through ``invalidate_analytics_cache``. The SCAN+DELETE alone is
    not enough: a reader that read before the delete re-fills the old value."""
    from app.services import cache_service

    monkeypatch.setattr(
        "app.db.redis_client.get_redis",
        lambda: SimpleNamespace(scan=AsyncMock(return_value=(0, [])), delete=AsyncMock()),
    )
    await cache_service.invalidate_analytics_cache("p-ingest")
    assert _bumps(journal) == ["p-ingest"]


# ── Live: drain, close (router, API-key ingest, /ws/events, reaper) ──────────


async def test_live_drain_bumps_after_its_commit(journal, monkeypatch):
    from app.services.live_session_drainer import drain_run_buffer
    from tests.test_live_session_drainer import (
        _FakeRedis,
        _FakeSession,
        _make_event,
        _patch_targets,
    )

    class _JournaledSession(_FakeSession):
        async def commit(self):
            await super().commit()
            journal.append(("commit", ""))

    project_id = uuid.uuid4()
    _patch_targets(
        monkeypatch,
        _FakeRedis(buffer_entries=[_make_event("t1"), _make_event("t2", "FAILED")]),
        _JournaledSession(existing_run=None),
        state={"passed": 1, "failed": 1, "total": 2},
    )

    result = await drain_run_buffer(run_id="run-d", project_id=str(project_id))

    assert result["drained"] == 2
    _assert_bumped_after_commit(journal, project_id)


async def test_live_drain_of_an_empty_buffer_does_not_bump(journal, monkeypatch):
    from app.services.live_session_drainer import drain_run_buffer
    from tests.test_live_session_drainer import _FakeRedis, _FakeSession, _patch_targets

    _patch_targets(monkeypatch, _FakeRedis(buffer_entries=[]), _FakeSession())
    await drain_run_buffer(run_id="run-e", project_id=str(uuid.uuid4()))
    assert _bumps(journal) == []


async def test_close_session_service_returns_the_project_it_closed(monkeypatch):
    """The service stages; it returns the project so the committer can bump."""
    from unittest.mock import patch

    from app.services import stream_service

    project_id = uuid.uuid4()
    session = SimpleNamespace(
        id=uuid.uuid4(), run_id="local-1", status="active", project_id=project_id,
        release_name=None, completed_at=None, extra_metadata=None,
        build_number="b1", client_name=None, framework="", branch="",
        commit_hash="", suite_name=None,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)
    fake_redis = SimpleNamespace(
        eval=AsyncMock(return_value=[
            b"closing",
            b'{"total":0,"failed":0,"passed":0,"skipped":0,"broken":0,'
            b'"unknown":0,"events_received":0}',
            b"0",
        ]),
        lrange=AsyncMock(return_value=[]),
    )
    with (
        patch(
            "app.streams.live_run_state.RedisLiveRunState.complete",
            new=AsyncMock(return_value={}),
        ),
        patch("app.services.stream_service.upsert_test_run", new=AsyncMock()),
        patch("app.db.redis_client.get_redis", return_value=fake_redis),
        patch("app.services.release_linker.link_run_or_default", new=AsyncMock()),
        patch(
            "app.services.run_downstream_outbox.stage_live_persist_operation",
            new=AsyncMock(return_value=True),
        ),
    ):
        closed = await stream_service.close_session(db, str(session.id))

    assert closed == project_id

    session.status = "completed"
    assert await stream_service.close_session(db, str(session.id)) is None, (
        "an already-completed session changed nothing and must not be bumped"
    )


async def test_stream_close_route_bumps_after_commit(journal, monkeypatch):
    from app.routers import stream as route
    from app.services import stream_service

    project_id = uuid.uuid4()
    monkeypatch.setattr(stream_service, "close_session", AsyncMock(return_value=project_id))
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())

    await route.close_session(
        session_id=str(uuid.uuid4()), db=_Session(journal), auth=(SimpleNamespace(), None)
    )

    _assert_bumped_after_commit(journal, project_id)


async def test_stream_close_route_does_not_bump_an_already_closed_session(
    journal, monkeypatch
):
    from app.routers import stream as route
    from app.services import stream_service

    monkeypatch.setattr(stream_service, "close_session", AsyncMock(return_value=None))
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())

    await route.close_session(
        session_id=str(uuid.uuid4()), db=_Session(journal), auth=(SimpleNamespace(), None)
    )
    assert _bumps(journal) == []


async def test_stream_close_route_rollback_does_not_bump(journal, monkeypatch):
    from app.routers import stream as route
    from app.services import stream_service

    monkeypatch.setattr(
        stream_service, "close_session", AsyncMock(return_value=uuid.uuid4())
    )
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())

    with pytest.raises(RuntimeError):
        await route.close_session(
            session_id=str(uuid.uuid4()),
            db=_Session(journal, fail_commit=True),
            auth=(SimpleNamespace(), None),
        )
    assert _bumps(journal) == []


@pytest.mark.parametrize("event_type, bumped", [("run_complete", True), ("test_result", False)])
async def test_api_key_stream_ingest_bumps_on_run_complete(
    journal, monkeypatch, event_type, bumped
):
    from app.routers import stream as route
    from app.services import stream_service

    project_id = uuid.uuid4()
    monkeypatch.setattr(route, "enforce_redis_memory_backpressure", AsyncMock())
    monkeypatch.setattr(route, "enforce_ingest_rate_limit", AsyncMock())
    monkeypatch.setattr(
        stream_service, "ingest_via_api_key",
        AsyncMock(return_value=SimpleNamespace(session_id="s-1")),
    )
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())

    await route.ingest_via_api_key(
        payload=SimpleNamespace(events=[{"event_type": event_type}]),
        db=_Session(journal),
        auth=SimpleNamespace(project_id=project_id, api_key_name="k"),
    )

    if bumped:
        _assert_bumped_after_commit(journal, project_id)
    else:
        assert _bumps(journal) == []


@pytest.mark.parametrize("completes", [True, False])
async def test_ws_events_route_bumps_when_it_closes_a_run(journal, monkeypatch, completes):
    """``POST /ws/events`` with a project key stages the SDK path; a
    ``run_complete`` closes the session inside that staging."""
    from app.core.config import settings
    from app.routers.live import ingest_live_event
    from app.services.ws_event_ingest import WsIngestOutcome

    project = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    async def _admit(_db, *, project_id, api_key_name, run_id, event, api_key=None):
        return WsIngestOutcome("s-1", cache_key="k", completes=completes, staged=True)

    async def _noop(*_a, **_k):
        return None

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)
    monkeypatch.setattr("app.services.ws_event_ingest.after_commit", AsyncMock())
    monkeypatch.setattr("app.streams.producer.publish_live_event", AsyncMock())
    monkeypatch.setattr("app.streams.live_run_state.RedisLiveRunState.start", _noop)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.record_test_event", _noop
    )
    monkeypatch.setattr(
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure", _noop
    )
    monkeypatch.setattr(
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit", _noop
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=project),
    )
    monkeypatch.setattr(
        "app.services.live_event_authz.resolve_run_project",
        AsyncMock(return_value=project),
    )
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)
    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )

    await ingest_live_event(
        run_id="run-ws",
        event={"type": "run_complete" if completes else "test_result",
               "test_name": "t", "status": "PASSED"},
        x_api_key="qai_key",
        x_webhook_secret=None,
        db=_Session(journal),
    )

    if completes:
        _assert_bumped_after_commit(journal, project)
    else:
        assert _bumps(journal) == []


def test_stale_session_reaper_bumps_each_closed_project_once(journal, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import app.db.postgres as pg
    from app.services import stream_service

    _run_tasks_inline(monkeypatch)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    p1, p2, p_failed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sessions = [
        SimpleNamespace(id=uuid.uuid4(), run_id="a", status="active", started_at=old,
                        project_id=p1),
        SimpleNamespace(id=uuid.uuid4(), run_id="b", status="active", started_at=old,
                        project_id=p1),
        SimpleNamespace(id=uuid.uuid4(), run_id="c", status="active", started_at=old,
                        project_id=p2),
        SimpleNamespace(id=uuid.uuid4(), run_id="d", status="active", started_at=old,
                        project_id=p_failed),
    ]

    class _Rows:
        def scalars(self):
            return SimpleNamespace(all=lambda: sessions)

    class _ReaperSession(_Session):
        async def execute(self, *_a, **_k):
            return _Rows()

    db = _ReaperSession(journal)
    fail_on = sessions[3].id

    async def _close(session_db, session_id, **_k):
        session = next(s for s in sessions if str(s.id) == session_id)
        db.fail_commit = session.id == fail_on
        return session.project_id

    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.get", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(stream_service, "close_session", _close)
    monkeypatch.setattr(stream_service, "finalize_closed_session_redis", AsyncMock())

    from app.worker import tasks

    result = tasks.close_stale_live_sessions.run(idle_minutes=5)

    assert result["closed"] == 3 and result["errors"] == 1
    assert sorted(_bumps(journal)) == sorted([str(p1), str(p2)]), (
        "each project whose close committed is bumped exactly once; the "
        "rolled-back close is not bumped"
    )
    _assert_bumped_after_commit(journal, p1)


# ── Recovery: the suite-name repair sweep ────────────────────────────────────


def test_live_run_recovery_bumps_repaired_projects_after_commit(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import live_run_recovery_service as svc

    _run_tasks_inline(monkeypatch)
    p1 = uuid.uuid4()
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _Session(journal))
    monkeypatch.setattr(
        svc, "auto_recover_completed_runs",
        AsyncMock(return_value={"candidates": 0, "recovered": 0, "errors": 0}),
    )
    monkeypatch.setattr(
        svc, "repair_clobbered_primary_suite_names",
        AsyncMock(return_value={"candidates": 2, "repaired": 2, "project_ids": [str(p1)]}),
    )
    from app.worker import tasks

    tasks.auto_recover_completed_live_runs.run()

    assert _bumps(journal) == [str(p1)]
    _assert_bumped_after_commit(journal, p1)


def test_placeholder_backfill_bumps_filled_projects_after_commit(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import placeholder_backfill_service as svc

    _run_tasks_inline(monkeypatch)
    p1 = uuid.uuid4()
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _Session(journal))
    monkeypatch.setattr(
        svc, "backfill_placeholders_all_projects",
        AsyncMock(return_value={"runs_filled": 3, "project_ids": [str(p1)]}),
    )
    from app.worker import tasks

    tasks.backfill_placeholder_test_cases.run()

    _assert_bumped_after_commit(journal, p1)
    assert _bumps(journal) == [str(p1)]


async def test_placeholder_service_names_only_projects_that_got_rows(monkeypatch):
    from app.services import placeholder_backfill_service as svc

    filled, empty = uuid.uuid4(), uuid.uuid4()

    class _Rows:
        def all(self):
            return [(filled,), (empty,)]

    db = SimpleNamespace(execute=AsyncMock(return_value=_Rows()))

    async def _per_project(_db, project_id, max_runs=500):
        n = 1 if project_id == filled else 0
        return {"runs_scanned": 1, "runs_filled": n, "rows_synthesised": n}

    monkeypatch.setattr(svc, "backfill_placeholders_for_project", _per_project)

    totals = await svc.backfill_placeholders_all_projects(db)
    assert totals["project_ids"] == [str(filled)]


# ── Run deletion: single run and criteria job ────────────────────────────────


def _deletion_stubs(monkeypatch):
    from app.db import mongo as mongo_module
    from app.db import storage as storage_module
    from app.services import deletion_job_service, run_deletion_service, semantic_search

    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: object())
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: object())
    monkeypatch.setattr(
        run_deletion_service, "execution_blockers", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(semantic_search, "purge_run_documents", AsyncMock(return_value=0))
    monkeypatch.setattr(
        run_deletion_service, "perform_run_deletion",
        AsyncMock(return_value={"postgres": {"runs": 1}}),
    )
    monkeypatch.setattr(deletion_job_service, "close_job", AsyncMock(return_value=True))
    _run_tasks_inline(monkeypatch)


def test_run_delete_bumps_after_its_commit(journal, monkeypatch):
    import app.db.postgres as pg
    from app.worker import tasks

    _deletion_stubs(monkeypatch)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), status="PASSED",
                          minio_prefix=None)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _factory(_Session(journal, scalar=run)))

    result = tasks.delete_run_everywhere.run(str(run.id))

    assert result["deleted"] is True
    _assert_bumped_after_commit(journal, run.project_id)


def test_run_delete_rollback_does_not_bump(journal, monkeypatch):
    import app.db.postgres as pg
    from app.worker import tasks

    _deletion_stubs(monkeypatch)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), status="PASSED",
                          minio_prefix=None)
    monkeypatch.setattr(
        pg, "AsyncSessionLocal", _factory(_Session(journal, scalar=run, fail_commit=True))
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        tasks.delete_run_everywhere.run(str(run.id))
    assert _bumps(journal) == []


def test_criteria_deletion_bumps_its_project_once_after_the_loop(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import deletion_job_service
    from app.worker import tasks

    _deletion_stubs(monkeypatch)
    project_id = uuid.uuid4()
    runs = [
        SimpleNamespace(id=uuid.uuid4(), project_id=project_id, status="PASSED",
                        minio_prefix=None)
        for _ in range(3)
    ]
    monkeypatch.setattr(
        deletion_job_service, "start_frozen_set",
        AsyncMock(return_value=[r.id for r in runs]),
    )
    monkeypatch.setattr(
        pg, "AsyncSessionLocal",
        _factory(_Session(journal), *[_Session(journal, scalar=r) for r in runs]),
    )

    result = tasks.execute_criteria_deletion_task.run(
        str(uuid.uuid4()), str(project_id), None
    )

    assert result["deleted"] == 3
    assert _bumps(journal) == [str(project_id)], "one bump for the job, not one per run"
    assert journal[-1] == ("bump", str(project_id)), "after the last run's commit"


def test_criteria_deletion_with_nothing_committed_does_not_bump(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import deletion_job_service
    from app.worker import tasks

    _deletion_stubs(monkeypatch)
    run_id = uuid.uuid4()
    monkeypatch.setattr(
        deletion_job_service, "start_frozen_set", AsyncMock(return_value=[run_id])
    )
    # The run is already gone: idempotent, nothing deleted, nothing committed.
    monkeypatch.setattr(
        pg, "AsyncSessionLocal", _factory(_Session(journal), _Session(journal, scalar=None))
    )

    tasks.execute_criteria_deletion_task.run(str(uuid.uuid4()), str(uuid.uuid4()), None)
    assert _bumps(journal) == []


# ── Retention purge ──────────────────────────────────────────────────────────


async def test_retention_sweep_bumps_each_purged_project_once(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import deletion_job_service, retention_service
    from app.worker.tasks import _retention_purge_sweep

    ok_a, failing, ok_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    class _Targets:
        def scalars(self):
            return SimpleNamespace(all=lambda: [ok_a, failing, ok_b])

    class _SweepSession(_Session):
        async def execute(self, *_a, **_k):
            return _Targets()

    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _SweepSession(journal))
    monkeypatch.setattr(deletion_job_service, "open_job", AsyncMock(return_value=None))
    monkeypatch.setattr(deletion_job_service, "close_job", AsyncMock(return_value=True))

    async def _purge(db, *, project_id, mode):
        if project_id == failing:
            raise RuntimeError("mongo unreachable")
        return {"counts": {"postgres": {"runs": 2}}, "cutoffs": {}}

    monkeypatch.setattr(retention_service, "run_purge", _purge)

    summary = await _retention_purge_sweep(None)

    assert summary["errors"] == 1
    assert _bumps(journal) == [str(ok_a), str(ok_b)], (
        "each purged project once, after its own commit; the failed purge "
        "rolled back and is not bumped"
    )
    _assert_bumped_after_commit(journal, ok_a)


# ── Project reset and deactivation ───────────────────────────────────────────


async def test_project_reset_bumps_after_its_commit(journal):
    from app.services.project_reset_service import reset_project
    from tests.test_project_reset_service import _fake_db, _project

    project = _project()
    db = _fake_db(project=project, run_count=3)
    db.commit = AsyncMock(side_effect=lambda: journal.append(("commit", "")))

    await reset_project(
        db, project_id=project.id, mode="runs", confirmation_name=project.name
    )

    _assert_bumped_after_commit(journal, project.id)


async def test_project_reset_that_fails_does_not_bump(journal):
    from app.services.project_reset_service import ConfirmationMismatch, reset_project
    from tests.test_project_reset_service import _fake_db, _project

    project = _project()
    with pytest.raises(ConfirmationMismatch):
        await reset_project(
            _fake_db(project=project), project_id=project.id, mode="runs",
            confirmation_name="wrong",
        )

    db = _fake_db(project=project, run_count=3)
    db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    with pytest.raises(RuntimeError):
        await reset_project(
            db, project_id=project.id, mode="runs", confirmation_name=project.name
        )
    assert _bumps(journal) == []


async def test_project_deactivation_bumps_after_its_commit(journal, monkeypatch):
    from app.routers.projects import delete_project
    import app.services.project_access_revocation_service as revocation

    project = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    monkeypatch.setattr(revocation, "revoke_project_credentials", AsyncMock())

    await delete_project(project.id, db=_Session(journal, scalar=project))

    assert project.is_active is False
    _assert_bumped_after_commit(journal, project.id)


# ── Primary release: Run Detail, link, unlink, release delete, reconcile ─────


async def test_run_detail_release_change_bumps_after_commit(journal, monkeypatch):
    from app.routers.runs import set_run_release
    from app.services import release_linker

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    release = SimpleNamespace(id=uuid.uuid4(), name="2.4", status="active")
    monkeypatch.setattr(
        release_linker, "auto_link_release", AsyncMock(return_value=(release, False))
    )
    monkeypatch.setattr(release_linker, "sync_primary_release", AsyncMock())

    await set_run_release(
        run.id, {"release_name": "2.4"}, db=_Session(journal, scalar=run),
        current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )

    _assert_bumped_after_commit(journal, run.project_id)


async def test_run_detail_release_change_rollback_does_not_bump(journal, monkeypatch):
    from app.routers.runs import set_run_release
    from app.services import release_linker

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    release = SimpleNamespace(id=uuid.uuid4(), name="2.4", status="active")
    monkeypatch.setattr(
        release_linker, "auto_link_release", AsyncMock(return_value=(release, False))
    )
    monkeypatch.setattr(release_linker, "sync_primary_release", AsyncMock())

    with pytest.raises(RuntimeError):
        await set_run_release(
            run.id, {"release_name": "2.4"},
            db=_Session(journal, scalar=run, fail_commit=True),
            current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
        )
    assert _bumps(journal) == []


@pytest.mark.parametrize("is_new", [True, False])
async def test_release_link_bumps_when_it_links(journal, monkeypatch, is_new):
    from app.routers import releases as route
    from app.services import release_service

    link = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    monkeypatch.setattr(
        release_service, "link_test_run", AsyncMock(return_value=(link, is_new))
    )
    monkeypatch.setattr(route, "serialize_model", lambda obj: {"id": str(obj.id)})

    await route.link_test_run(
        str(uuid.uuid4()), SimpleNamespace(), db=_Session(journal),
        current_user=SimpleNamespace(id=uuid.uuid4()), __=None,
    )

    if is_new:
        _assert_bumped_after_commit(journal, link.project_id)
    else:
        assert _bumps(journal) == [], "an existing link changed nothing"


async def test_release_unlink_bumps_the_runs_project_after_commit(journal, monkeypatch):
    from app.routers import releases as route
    from app.services import release_service

    project_id = uuid.uuid4()
    monkeypatch.setattr(release_service, "unlink_test_run", AsyncMock())

    await route.unlink_test_run(
        str(uuid.uuid4()), str(uuid.uuid4()), db=_Session(journal, scalar=project_id),
        _=None, __=None, ___=None,
    )

    _assert_bumped_after_commit(journal, project_id)


async def test_release_delete_bumps_after_commit(journal, monkeypatch):
    from app.routers import releases as route
    from app.services import release_service

    doomed = SimpleNamespace(project_id=uuid.uuid4(), name="2.3")
    monkeypatch.setattr(release_service, "get_release_or_404", AsyncMock(return_value=doomed))
    monkeypatch.setattr(release_service, "delete_release", AsyncMock())
    monkeypatch.setattr(route, "record_activity", AsyncMock())

    await route.delete_release(
        str(uuid.uuid4()), db=_Session(journal),
        current_user=SimpleNamespace(id=uuid.uuid4(), username="u"), __=None,
    )

    _assert_bumped_after_commit(journal, doomed.project_id)


# ── Cached dashboard summary: defects, active release-gate policy ───────────


async def test_manual_defect_create_bumps_after_explicit_commit(journal, monkeypatch):
    """``create_defect`` has no caller — it is a get_db route handler — so it
    must commit explicitly itself, then bump; the teardown commit runs after
    the handler returns and cannot be followed by anything inside it."""
    from app.models.schemas import DefectIntakeRequest, FailureCategory
    from app.routers import analytics as route
    from app.services import analytics_service

    from datetime import datetime, timezone

    project_id = uuid.uuid4()
    defect = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, title="t", component=None,
        jira_ticket_id=None, jira_ticket_url=None, resolution_status="OPEN",
        ai_confidence_score=None, created_at=datetime.now(timezone.utc),
        failure_category=FailureCategory.PRODUCT_BUG,
    )
    monkeypatch.setattr(route, "get_accessible_project_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(
        analytics_service, "create_manual_defect",
        _mutating(journal, "create_manual_defect", return_value=defect),
    )
    payload = DefectIntakeRequest(
        project_id=project_id, title="A failing thing", severity="P2",
    )

    await route.create_defect(payload, db=_Session(journal), current_user=SimpleNamespace())

    _assert_bumped_after_commit(journal, project_id)


async def test_manual_defect_create_rollback_does_not_bump(journal, monkeypatch):
    from app.models.schemas import DefectIntakeRequest
    from app.routers import analytics as route
    from app.services import analytics_service

    project_id = uuid.uuid4()
    monkeypatch.setattr(route, "get_accessible_project_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(
        analytics_service, "create_manual_defect",
        AsyncMock(return_value=SimpleNamespace(
            id=uuid.uuid4(), project_id=project_id, title="t", component=None,
            jira_ticket_id=None, jira_ticket_url=None, resolution_status="OPEN",
            ai_confidence_score=None, created_at=None, failure_category=None,
        )),
    )
    payload = DefectIntakeRequest(project_id=project_id, title="A failing thing", severity="P2")

    with pytest.raises(RuntimeError):
        await route.create_defect(
            payload, db=_Session(journal, fail_commit=True), current_user=SimpleNamespace()
        )
    assert _bumps(journal) == []


async def test_release_gate_policy_publish_bumps_after_commit(journal, monkeypatch):
    """The cached dashboard's readiness band reads the ACTIVE policy."""
    from app.routers import release_gate_policies as route

    project_id = uuid.uuid4()
    policy = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, is_draft=True, is_active=False,
        name="p", version=1, activated_by=None, activated_at=None,
    )

    class _PolicySession(_Session):
        async def execute(self, *_a, **_k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = policy
            result.scalars.return_value.all.return_value = []
            return result

        async def refresh(self, *_a, **_k):
            return None

    await route.publish_policy(
        policy.id, current_user=SimpleNamespace(username="u", id=uuid.uuid4()),
        db=_PolicySession(journal),
    )

    assert policy.is_active is True
    _assert_bumped_after_commit(journal, project_id)


async def test_release_gate_policy_deactivate_bumps_after_commit(journal, monkeypatch):
    from app.routers import release_gate_policies as route

    project_id = uuid.uuid4()
    policy = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, is_active=True, name="p", version=1,
    )

    class _PolicySession(_Session):
        async def execute(self, *_a, **_k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = policy
            return result

        async def refresh(self, *_a, **_k):
            return None

    await route.deactivate_policy(
        policy.id, current_user=SimpleNamespace(username="u", id=uuid.uuid4()),
        db=_PolicySession(journal),
    )

    assert policy.is_active is False
    _assert_bumped_after_commit(journal, project_id)


async def test_release_gate_system_default_publish_bumps_every_project(journal, monkeypatch):
    """A system-default (project_id NULL) policy change moves the readiness
    band for every project that has no policy of its own."""
    from app.routers import release_gate_policies as route

    p1, p2 = uuid.uuid4(), uuid.uuid4()
    policy = SimpleNamespace(
        id=uuid.uuid4(), project_id=None, is_draft=True, is_active=False,
        name="default", version=1, activated_by=None, activated_at=None,
    )

    class _PolicySession(_Session):
        def __init__(self, journal):
            super().__init__(journal)
            self._call = 0

        async def execute(self, *_a, **_k):
            self._call += 1
            result = MagicMock()
            if self._call == 1:  # fetch the policy by id
                result.scalar_one_or_none.return_value = policy
            elif self._call == 2:  # existing active policies for the same scope
                result.scalars.return_value.all.return_value = []
            else:  # _policy_scope_projects: every project (system default)
                result.scalars.return_value.all.return_value = [p1, p2]
            return result

        async def refresh(self, *_a, **_k):
            return None

    await route.publish_policy(
        policy.id, current_user=SimpleNamespace(username="u", id=uuid.uuid4()),
        db=_PolicySession(journal),
    )

    assert sorted(_bumps(journal)) == sorted([str(p1), str(p2)])


# ── Cached hours-saved model: quarantine, AI analysis, failure clusters ─────


async def test_quarantine_approve_bumps_after_commit(journal, monkeypatch):
    from app.services import flaky_quarantine_service as svc

    project_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id,
        status=svc.FlakyQuarantineStatus.PROPOSED.value,
        quarantine_duration_days=svc._DEFAULT_QUARANTINE_DAYS,
        rationale=None, defect_id=None, test_name="t", test_fingerprint="fp",
        suite_name=None, flip_rate=0.5, flip_window_size=10,
        quarantine_start=None, quarantine_expires_at=None,
        approved_at=None, approved_by_user_id=None, rejected_by_user_id=None,
        recheck_at=None, reviewer_notes=None, owner_user_id=None, sla_days=None,
        stale_at=None, stale_notified_at=None, consecutive_passes=0,
        last_stability_run_id=None, ready_to_promote=False, ready_notified_at=None,
        updated_at=None,
    )
    monkeypatch.setattr(svc, "get_request", AsyncMock(return_value=row))
    monkeypatch.setattr(
        svc, "_lifecycle_activation_context",
        AsyncMock(return_value=(SimpleNamespace(sla_days=7, auto_create_defect=False), None)),
    )
    monkeypatch.setattr(svc, "_audit", AsyncMock())
    monkeypatch.setattr(svc, "dispatch_quarantine_transitions", AsyncMock(), raising=False)
    monkeypatch.setattr(
        "app.services.notification_transitions.dispatch_quarantine_transitions", AsyncMock()
    )
    monkeypatch.setattr("app.services.webhook_service.emit_event", AsyncMock())

    await svc.approve(_Session(journal), row.id, actor=SimpleNamespace(id=uuid.uuid4()))

    _assert_bumped_after_commit(journal, project_id)


async def test_quarantine_propose_new_row_bumps_after_commit(journal, monkeypatch):
    from app.services import flaky_quarantine_service as svc

    project_id = uuid.uuid4()
    monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "_audit", AsyncMock())
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _Session(journal))

    result = await svc.propose_quarantine(
        project_id=project_id, test_fingerprint="fp", test_name="t", suite_name=None,
        detection_method="auto", flip_rate=0.5, flip_window_size=10,
    )

    assert result is not None
    _assert_bumped_after_commit(journal, project_id)


async def test_analyze_test_case_single_bumps_after_commit(journal, monkeypatch):
    """``analyze_test_case`` commits explicitly (not via get_db teardown) and
    must bump right after — a new ai_analysis row feeds the cached
    hours-saved model."""
    from app.core.deps import AuthorizedTestCaseContext
    from app.models.schemas import AnalyzeRequest
    from app.routers import analyze as route

    project_id = uuid.uuid4()
    tc_id = uuid.uuid4()
    tc = SimpleNamespace(
        id=tc_id, test_name="t", suite_name="s", error_message="boom",
        stack_trace=None, duration_ms=1, severity="P2",
        ocp_pod_name=None, ocp_namespace=None, test_fingerprint="fp",
    )
    test_run = SimpleNamespace(end_time=None, start_time=None, ocp_pod_name=None, ocp_namespace=None)
    authorized = AuthorizedTestCaseContext(
        test_case=tc, test_run=test_run, run_id=uuid.uuid4(), project_id=project_id,
    )
    monkeypatch.setattr(route, "resolve_authorized_test_case", AsyncMock(return_value=authorized))
    monkeypatch.setattr(
        route.analysis_router, "classify_test",
        AsyncMock(return_value={
            "root_cause_summary": "x", "failure_category": "PRODUCT_BUG",
            "confidence_score": 80,
            "evidence_references": [{"source": "s", "reference_id": "1", "excerpt": "x"}],
            "recommended_actions": [], "tools_used": [], "is_flaky": False,
            "backend_error_found": False, "pod_issue_found": False,
            "requires_human_review": False,
        }),
    )

    db = _Session(journal, scalar=None)  # no existing AIAnalysis row
    await route.analyze_test_case(
        AnalyzeRequest(test_case_id=tc_id), db=db, current_user=SimpleNamespace(),
    )

    _assert_bumped_after_commit(journal, project_id)


async def test_failure_cluster_snapshot_bumps_after_commit(journal, monkeypatch):
    """``persist_failure_cluster_snapshot`` writes new FailureCluster rows,
    which feed the cached hours-saved model's cluster-size sum."""
    from app.agents import deep_persistence as svc

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    member_id = uuid.uuid4()

    class _ClusterSession(_Session):
        def __init__(self, journal):
            super().__init__(journal)
            self._call = 0

        async def execute(self, *_a, **_k):
            self._call += 1
            result = MagicMock()
            if self._call == 1:  # authorized TestCase members
                result.scalars.return_value.all.return_value = [member_id]
            elif self._call == 2:  # existing FailureCluster rows for this pipeline
                result.scalars.return_value = []
            elif self._call == 3:  # TestRun.project_id lookup added for the bump
                result.scalar_one_or_none.return_value = project_id
            else:  # final read-back of the persisted rows
                result.scalars.return_value.all.return_value = [SimpleNamespace(id=uuid.uuid4())]
            return result

    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _ClusterSession(journal))

    await svc.persist_failure_cluster_snapshot(
        str(run_id), str(pipeline_run_id),
        [{"cluster_id": "c1", "label": "cluster", "member_test_ids": [str(member_id)]}],
    )

    _assert_bumped_after_commit(journal, project_id)


def test_primary_release_reconcile_sweep_bumps_each_project_once(journal, monkeypatch):
    import app.db.postgres as pg
    from app.services import release_linker
    from app.worker import tasks

    _run_tasks_inline(monkeypatch)
    p1, p2, p_failed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    drifted = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    owners = {drifted[0]: p1, drifted[1]: p1, drifted[2]: p2, drifted[3]: p_failed}
    failing = drifted[3]
    sessions = [_Session(journal)] + [
        _Session(journal, scalar=owners[r], fail_commit=(r == failing)) for r in drifted
    ]
    monkeypatch.setattr(pg, "AsyncSessionLocal", _factory(*sessions))
    monkeypatch.setattr(
        release_linker, "find_primary_release_drift", AsyncMock(return_value=drifted)
    )
    monkeypatch.setattr(release_linker, "sync_primary_release", AsyncMock())

    result = tasks.reconcile_primary_releases.run()

    assert result["repaired"] == 3 and result["failed"] == 1
    assert sorted(_bumps(journal)) == sorted([str(p1), str(p2)]), (
        "each repaired project once — not once per run — and not the project "
        "whose repair rolled back"
    )
    _assert_bumped_after_commit(journal, p1)
