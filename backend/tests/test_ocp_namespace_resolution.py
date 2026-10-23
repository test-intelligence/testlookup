"""W10 INT-03: the OpenShift namespace chain, stored at ingest; the pod lookup after the commit.

* ``IntegrationRuntimeConfig.namespace_for``: the run's own namespace -> the
  project's -> the saved Default Namespace -> env ``OCP_DEFAULT_NAMESPACE``;
* a sentinel ingest of a run that names a pod but no namespace stores the
  resolved namespace in the ingest transaction and enqueues
  ``enrich_run_ocp_metadata`` once AFTER the commit (the ``.delay`` stand-in
  binds its kwargs to the task's REAL signature: a bare mocked ``delay``
  would hide Celery's argument check). The OpenShift call no longer runs
  inside the ingest transaction;
* the task (driven with ``apply``) makes exactly the pod GET and the events
  GET against the fake, with NO session open during them, then writes
  ``ocp_metadata`` on its own session; offline it sends nothing.

The PostgreSQL half (a real ingest storing ``qa-payments``) is
``tests/integration/test_ocp_ingest_namespace_postgres.py``.
"""
from __future__ import annotations

import inspect
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import settings
from app.services import integration_config_service, ocp_client, ocp_enrichment, url_safety
from app.services.integration_config_service import (
    IntegrationRuntimeConfig,
    clear_runtime_integration_cache,
    resolve_runtime_integrations,
)
from tests.fakes.fake_integrations import FakeState, create_app

PROJECT = str(uuid.uuid4())
POD = "payment-runner-7d9f"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


class _Result:
    def __init__(self, row=None, first=None):
        self._row, self._first = row, first

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first


class _ResolverDb:
    def __init__(self, saved: dict, project_namespace):
        self.saved, self.project_namespace = saved, project_namespace

    async def execute(self, stmt):
        if "app_settings" in str(stmt):
            return _Result(row=SimpleNamespace(value=dict(self.saved)))
        return _Result(first=(None, self.project_namespace))


async def _cfg(monkeypatch, *, saved_namespace=None, project_namespace=None) -> IntegrationRuntimeConfig:
    async def no_secret(*_a, **_k):
        return None

    monkeypatch.setattr(integration_config_service, "read_secret", no_secret)
    monkeypatch.setattr(settings, "OCP_DEFAULT_NAMESPACE", "qa-env")
    saved = {"ocp_default_namespace": saved_namespace} if saved_namespace is not None else {}
    return await resolve_runtime_integrations(_ResolverDb(saved, project_namespace), PROJECT)


# ── The chain ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_runs_own_namespace_wins(monkeypatch):
    cfg = await _cfg(monkeypatch, saved_namespace="qa-saved", project_namespace="qa-payments")
    assert cfg.namespace_for("qa-run") == "qa-run"


@pytest.mark.asyncio
async def test_then_the_projects_namespace(monkeypatch):
    cfg = await _cfg(monkeypatch, saved_namespace="qa-saved", project_namespace="qa-payments")
    assert cfg.namespace_for(None) == "qa-payments"
    assert cfg.namespace_for("   ") == "qa-payments"
    assert cfg.ocp_namespace_source == "project"


@pytest.mark.asyncio
async def test_then_the_saved_default_namespace(monkeypatch):
    cfg = await _cfg(monkeypatch, saved_namespace="qa-saved", project_namespace="")
    assert cfg.namespace_for(None) == "qa-saved"
    assert cfg.ocp_namespace_source == "saved"


@pytest.mark.asyncio
async def test_then_the_environment(monkeypatch):
    cfg = await _cfg(monkeypatch, saved_namespace="", project_namespace=None)
    assert cfg.namespace_for(None) == "qa-env"
    assert cfg.ocp_namespace_source == "environment"


# ── The ingest stores the namespace and enqueues the lookup after its commit ─


def _bound_delay(calls: list[dict]):
    from app.worker.tasks import enrich_run_ocp_metadata

    def delay(*args, **kwargs):
        inspect.signature(enrich_run_ocp_metadata.run).bind(*args, **kwargs)
        calls.append(kwargs)

    return delay


async def _ingest(monkeypatch, run_row, journal: list[str]):
    from app.models.postgres import Project as ProjectModel
    from app.models.schemas import SentinelFile
    from app.services import cache_service, ingestion
    from app.worker.tasks import enrich_run_ocp_metadata

    project_row = ProjectModel(id=run_row.project_id, name="Payments", slug="payments")

    class _SentinelSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, stmt, *_a, **_k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = project_row if "projects" in str(stmt) else None
            result.scalars.return_value.all.return_value = []
            return result

        async def commit(self):
            journal.append("commit")

        async def rollback(self):
            journal.append("rollback")

    calls: list[dict] = []
    delay = _bound_delay(calls)

    def recording_delay(*args, **kwargs):
        journal.append("enqueue")
        return delay(*args, **kwargs)

    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: _SentinelSession())
    monkeypatch.setattr(ingestion, "_upsert_test_run", AsyncMock(return_value=run_row))
    storage = SimpleNamespace(list_objects=AsyncMock(return_value=[]), get_object_content=AsyncMock(return_value=b"{}"))
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", AsyncMock(return_value={}))
    monkeypatch.setattr(ingestion, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr("app.services.run_downstream_outbox.stage_finalize_operations", AsyncMock())
    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", AsyncMock())
    monkeypatch.setattr(enrich_run_ocp_metadata, "delay", recording_delay)
    monkeypatch.setattr(ocp_client, "get_http_client", MagicMock(side_effect=AssertionError("no OpenShift call in the ingest")))
    resolved = IntegrationRuntimeConfig(
        project_id=str(run_row.project_id), splunk_enabled=False, splunk_base_url=None, splunk_index="main",
        splunk_index_source="environment", ocp_enabled=True, ocp_api_url="http://ocp.fake.test:6443",
        ocp_default_namespace="qa-payments", ocp_namespace_source="project",
    )
    resolve = AsyncMock(return_value=resolved)
    monkeypatch.setattr(integration_config_service, "resolve_runtime_integrations", resolve)

    sentinel = SentinelFile(project_id=str(run_row.project_id), build_number="b1", ocp_pod_name=run_row.ocp_pod_name,
                            ocp_namespace=run_row.ocp_namespace, release_name=None)
    await ingestion.process_sentinel(sentinel, "prefix/")
    return calls, resolve


def _run_row(**overrides):
    values = dict(
        id=uuid.uuid4(), project_id=uuid.UUID(PROJECT), build_number="b1", ingestion_attempted_tests=0,
        ingestion_rejected_tests=0, ingestion_complete=False, ingestion_rejection_reasons=None,
        ocp_pod_name=POD, ocp_namespace=None, start_time=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_a_pod_without_a_namespace_gets_the_resolved_one_and_one_lookup_after_the_commit(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    run_row, journal = _run_row(), []
    calls, resolve = await _ingest(monkeypatch, run_row, journal)

    assert run_row.ocp_namespace == "qa-payments"
    resolve.assert_awaited_once()
    assert calls == [{"run_id": str(run_row.id)}]
    assert journal.index("commit") < journal.index("enqueue"), "the lookup is enqueued after the commit"


@pytest.mark.asyncio
async def test_a_run_with_its_own_namespace_keeps_it(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    run_row, journal = _run_row(ocp_namespace="qa-own"), []
    calls, resolve = await _ingest(monkeypatch, run_row, journal)

    assert run_row.ocp_namespace == "qa-own"
    resolve.assert_not_awaited()
    assert calls == [{"run_id": str(run_row.id)}]


@pytest.mark.asyncio
async def test_offline_the_namespace_is_stored_and_nothing_is_enqueued(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    run_row, journal = _run_row(), []
    calls, _ = await _ingest(monkeypatch, run_row, journal)

    assert run_row.ocp_namespace == "qa-payments"
    assert calls == []


# ── The worker task ─────────────────────────────────────────────────────────


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, events: list[str]):
        self._inner, self._events = inner, events

    async def handle_async_request(self, request):
        self._events.append(f"http:{request.url.path}")
        return await self._inner.handle_async_request(request)


@pytest.fixture
def task_world(monkeypatch):
    events: list[str] = []
    writes: list[dict] = []
    open_sessions = {"n": 0}
    run_id = uuid.uuid4()

    class _ReadSession:
        async def __aenter__(self):
            open_sessions["n"] += 1
            events.append("read_open")
            return self

        async def __aexit__(self, *_a):
            open_sessions["n"] -= 1
            events.append("read_close")
            return False

        async def execute(self, _stmt):
            return _Result(first=(POD, "qa-payments", uuid.UUID(PROJECT)))

    class _WriteSession:
        async def __aenter__(self):
            open_sessions["n"] += 1
            events.append("write_open")
            return self

        async def __aexit__(self, *_a):
            open_sessions["n"] -= 1
            events.append("write_close")
            return False

        async def execute(self, stmt):
            writes.append(dict(stmt.compile().params))
            return None

        async def commit(self):
            events.append("commit")

    state = FakeState()
    inner = httpx.ASGITransport(app=create_app(state))

    class _GuardedTransport(_RecordingTransport):
        async def handle_async_request(self, request):
            assert open_sessions["n"] == 0, "a session is open during the OpenShift call"
            return await super().handle_async_request(request)

    client = httpx.AsyncClient(transport=_GuardedTransport(inner, events))
    cfg = IntegrationRuntimeConfig(
        project_id=PROJECT, splunk_enabled=False, splunk_base_url=None, splunk_index="main",
        splunk_index_source="environment", ocp_enabled=True, ocp_api_url="http://ocp.fake.test:6443",
        ocp_default_namespace="qa-payments", ocp_namespace_source="project", ocp_sa_token=state.ocp_token,
    )

    def getaddrinfo(host, *_a, **_k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(ocp_enrichment, "AsyncSessionLocal", _ReadSession)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _WriteSession, raising=False)
    monkeypatch.setattr(ocp_enrichment, "runtime_integrations_for", AsyncMock(return_value=cfg))
    monkeypatch.setattr(ocp_client, "get_http_client", lambda: client)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    return SimpleNamespace(events=events, writes=writes, state=state, run_id=run_id)


def test_the_task_reads_closes_then_calls_openshift_then_writes(task_world, monkeypatch):
    from app.worker.tasks import enrich_run_ocp_metadata

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    result = enrich_run_ocp_metadata.apply(kwargs={"run_id": str(task_world.run_id)}).get()

    assert result == {"run_id": str(task_world.run_id), "stored": True}
    ocp = [r for r in task_world.state.requests if r["service"] == "ocp"]
    assert [r["path"] for r in ocp] == [
        f"/api/v1/namespaces/qa-payments/pods/{POD}", "/api/v1/namespaces/qa-payments/events",
    ]
    assert {r["auth"] for r in ocp} == {"ok"}
    assert task_world.events == [
        "read_open", "read_close",
        f"http:/api/v1/namespaces/qa-payments/pods/{POD}", "http:/api/v1/namespaces/qa-payments/events",
        "write_open", "commit", "write_close",
    ]
    (write,) = task_world.writes
    meta = write["ocp_metadata"]
    assert meta["namespace"] == "qa-payments" and meta["phase"] == "Running"
    assert [e["reason"] for e in meta["events"]] == ["OOMKilled", "BackOff"]


def test_offline_the_task_sends_nothing_and_writes_nothing(task_world, monkeypatch):
    from app.worker.tasks import enrich_run_ocp_metadata

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    result = enrich_run_ocp_metadata.apply(kwargs={"run_id": str(task_world.run_id)}).get()

    assert result == {"run_id": str(task_world.run_id), "stored": False}
    assert task_world.state.requests == []
    assert task_world.writes == []


def test_a_failure_is_logged_and_swallowed(task_world, monkeypatch):
    from app.worker.tasks import enrich_run_ocp_metadata

    monkeypatch.setattr(ocp_enrichment, "pod_metadata_for_run", AsyncMock(side_effect=RuntimeError("db down")))
    result = enrich_run_ocp_metadata.apply(kwargs={"run_id": str(task_world.run_id)}).get()
    assert result == {"run_id": str(task_world.run_id), "stored": False}
