"""W10 INT-03 on a real database: a sentinel ingest stores the run's OpenShift namespace.

A MinIO sentinel names a pod but no namespace; the project's OCP Namespace is
``qa-payments``. The REAL ``ingestion.process_sentinel`` (storage stubbed with
an empty upload) must store ``qa-payments`` on the run inside its own
transaction, make no OpenShift call while it holds that transaction, and
enqueue ``enrich_run_ocp_metadata`` exactly once after the commit -- the
``.delay`` stand-in binds its kwargs to the task's real signature. Offline the
namespace is still stored and nothing is enqueued.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
skips otherwise. To be listed in CI's protected PostgreSQL step.
"""
from __future__ import annotations

import inspect
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

POD = "payment-runner-7d9f"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def database(monkeypatch):
    from app.services import ingestion
    from app.services.integration_config_service import clear_runtime_integration_cache

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ingestion, "AsyncSessionLocal", factory)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", factory, raising=False)
    clear_runtime_integration_cache()
    created: list[uuid.UUID] = []
    try:
        yield SimpleNamespace(factory=factory, created=created)
    finally:
        async with factory() as cleanup:
            for project_id in created:
                from app.models.postgres import Project

                await cleanup.execute(delete(Project).where(Project.id == project_id))
            await cleanup.commit()
        await engine.dispose()
        clear_runtime_integration_cache()


async def _ingest(monkeypatch, database, *, offline: bool) -> tuple[object, list[dict]]:
    from app.core.config import settings
    from app.models.postgres import Project, TestRun
    from app.models.schemas import SentinelFile
    from app.services import cache_service, ingestion, ocp_client
    from app.worker.tasks import enrich_run_ocp_metadata

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", offline)
    monkeypatch.setattr(settings, "OCP_DEFAULT_NAMESPACE", "qa-testing")
    token = uuid.uuid4().hex[:12]
    project_id = uuid.uuid4()
    async with database.factory() as db:
        db.add(Project(id=project_id, name=f"OCP ns {token}", slug=f"ocp-ns-{token}", ocp_namespace="qa-payments"))
        await db.commit()
    database.created.append(project_id)

    storage = SimpleNamespace(list_objects=AsyncMock(return_value=[]), get_object_content=AsyncMock(return_value=b"{}"))
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", AsyncMock())
    monkeypatch.setattr("app.streams.live_fanout.publish_live_notification", AsyncMock())
    monkeypatch.setattr(
        ocp_client, "get_http_client", MagicMock(side_effect=AssertionError("no OpenShift call inside the ingest")),
    )
    calls: list[dict] = []

    def delay(*args, **kwargs):
        inspect.signature(enrich_run_ocp_metadata.run).bind(*args, **kwargs)
        calls.append(kwargs)

    monkeypatch.setattr(enrich_run_ocp_metadata, "delay", delay)

    sentinel = SentinelFile(project_id=str(project_id), build_number=f"b-{token}", jenkins_job="ocp-ns",
                            ocp_pod_name=POD, ocp_namespace=None)
    await ingestion.process_sentinel(sentinel, f"ocp-ns/{token}/")

    async with database.factory() as db:
        run = (await db.execute(select(TestRun).where(TestRun.project_id == project_id))).scalar_one()
    return run, calls


async def test_a_pod_without_a_namespace_stores_the_projects_and_enqueues_one_lookup(monkeypatch, database):
    run, calls = await _ingest(monkeypatch, database, offline=False)

    assert run.ocp_pod_name == POD
    assert run.ocp_namespace == "qa-payments"
    assert run.ocp_metadata is None, "the pod lookup is the worker's, after the commit"
    assert calls == [{"run_id": str(run.id)}]


async def test_offline_the_namespace_is_stored_and_nothing_is_enqueued(monkeypatch, database):
    run, calls = await _ingest(monkeypatch, database, offline=True)

    assert run.ocp_namespace == "qa-payments"
    assert calls == []
