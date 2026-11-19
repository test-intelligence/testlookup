"""A report cannot point the server's OpenShift lookups outside its project.

The rule: when a project has its own saved OpenShift namespace, a pod in any
other namespace is not looked up for that project. A report that names another
namespace has its pod and namespace dropped at ingest (not stored, no lookup
queued) and the refusal is logged. Runs stored before this rule are covered
where the lookups happen: the enrichment worker and the server-side evidence
lookup both skip a namespace that is not the project's.

A project with no saved namespace of its own keeps the earlier behaviour: the
report's namespace is stored and used.

Before this rule the report's namespace was only checked for syntax, and the
server then read that pod's phase, node, image and events with its own cluster
token and showed them in the reporter's project.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from app.core.config import settings
from app.models.postgres import Project, TestRun
from app.models.schemas import SentinelFile
from app.services import ingestion, integration_evidence, ocp_client, ocp_enrichment
from app.services.integration_config_service import (
    IntegrationRuntimeConfig,
    clear_runtime_integration_cache,
)
from app.tools.investigation_context import InvestigationContext

pytestmark = pytest.mark.regression

PROJECT_ID = uuid.uuid4()
POD = "payment-runner-7d9f"
OWN = "team-a"
OTHER = "team-b"
FOUND = {"phase": "Running", "host_ip": "10.0.0.9"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


def _project(saved_namespace):
    return Project(id=PROJECT_ID, name="Payments", slug="payments", ocp_namespace=saved_namespace)


def _sentinel(namespace, pod=POD):
    return SentinelFile(
        project_id=str(PROJECT_ID), build_number="b1", ocp_pod_name=pod,
        ocp_namespace=namespace, release_name=None,
    )


def _cfg(namespace, source) -> IntegrationRuntimeConfig:
    return IntegrationRuntimeConfig(
        project_id=str(PROJECT_ID), splunk_enabled=False, splunk_base_url=None, splunk_index="main",
        splunk_index_source="environment", ocp_enabled=True, ocp_api_url="http://ocp.fake.test:6443",
        ocp_default_namespace=namespace, ocp_namespace_source=source,
    )


class _IngestSession:
    """Answers the project read and "no such run yet"; keeps what is added."""

    def __init__(self, project, journal=None):
        self.project, self.added = project, []
        self.journal = journal if journal is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt, *_a, **_k):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.project if "FROM projects" in str(stmt) else None
        result.scalars.return_value.all.return_value = []
        return result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.journal.append("commit")

    async def rollback(self):
        self.journal.append("rollback")


async def _new_run(project, sentinel) -> TestRun:
    db = _IngestSession(project)
    run = await ingestion._upsert_test_run(db, sentinel, "prefix/")
    assert db.added == [run]
    return run


# ── At ingest: what a report may store on a new run ──────────────────────────


@pytest.mark.asyncio
async def test_a_report_naming_another_namespace_stores_neither_pod_nor_namespace():
    with structlog.testing.capture_logs() as logs:
        run = await _new_run(_project(OWN), _sentinel(OTHER))

    assert run.ocp_namespace is None, "the other team's namespace was stored"
    assert run.ocp_pod_name is None, "the pod would be looked up in the project's namespace instead"
    [refusal] = [entry for entry in logs if entry["event"] == "ocp_namespace_refused"]
    assert refusal["log_level"] == "warning"
    assert refusal["reported_namespace"] == OTHER
    assert refusal["project_namespace"] == OWN
    assert refusal["project_id"] == str(PROJECT_ID)


@pytest.mark.asyncio
async def test_a_report_naming_the_projects_namespace_is_stored_as_before():
    with structlog.testing.capture_logs() as logs:
        run = await _new_run(_project(OWN), _sentinel(OWN))

    assert (run.ocp_pod_name, run.ocp_namespace) == (POD, OWN)
    assert not [entry for entry in logs if entry["event"] == "ocp_namespace_refused"]


@pytest.mark.asyncio
async def test_spaces_around_either_value_do_not_make_them_differ():
    run = await _new_run(_project(f"  {OWN} "), _sentinel(f"{OWN} "))
    assert run.ocp_pod_name == POD and run.ocp_namespace.strip() == OWN


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, "", "   "])
async def test_a_project_without_a_saved_namespace_keeps_the_reports_value(saved):
    run = await _new_run(_project(saved), _sentinel(OTHER))
    assert (run.ocp_pod_name, run.ocp_namespace) == (POD, OTHER)


@pytest.mark.asyncio
async def test_a_report_naming_only_a_pod_is_stored_as_before():
    run = await _new_run(_project(OWN), _sentinel(None))
    assert (run.ocp_pod_name, run.ocp_namespace) == (POD, None)


# ── The whole ingest: nothing is queued for the refused pod ──────────────────


async def _ingest(monkeypatch, project, sentinel):
    from app.services import cache_service, integration_config_service
    from app.worker.tasks import enrich_run_ocp_metadata

    journal: list[str] = []
    session = _IngestSession(project, journal)
    queued: list[dict] = []

    def delay(*args, **kwargs):
        inspect.signature(enrich_run_ocp_metadata.run).bind(*args, **kwargs)
        queued.append(kwargs)

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: session)
    storage = SimpleNamespace(
        list_objects=AsyncMock(return_value=[]), get_object_content=AsyncMock(return_value=b"{}")
    )
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", AsyncMock(return_value={}))
    monkeypatch.setattr(ingestion, "_update_run_aggregates", AsyncMock())
    monkeypatch.setattr("app.services.run_downstream_outbox.stage_finalize_operations", AsyncMock())
    monkeypatch.setattr(cache_service, "invalidate_analytics_cache", AsyncMock())
    monkeypatch.setattr(enrich_run_ocp_metadata, "delay", delay)
    monkeypatch.setattr(
        ocp_client, "get_http_client", MagicMock(side_effect=AssertionError("an OpenShift call was made"))
    )
    saved = (project.ocp_namespace or "").strip()
    monkeypatch.setattr(
        integration_config_service, "resolve_runtime_integrations",
        AsyncMock(return_value=_cfg(saved or "qa-env", "project" if saved else "environment")),
    )
    await ingestion.process_sentinel(sentinel, "prefix/")
    [run] = session.added[:1]
    assert "commit" in journal
    return run, queued


@pytest.mark.asyncio
async def test_an_ingest_naming_another_namespace_queues_no_openshift_lookup(monkeypatch):
    run, queued = await _ingest(monkeypatch, _project(OWN), _sentinel(OTHER))

    assert queued == [], "a lookup was queued for a pod the project may not read"
    assert run.ocp_namespace != OTHER
    assert run.ocp_pod_name is None and run.ocp_namespace is None


@pytest.mark.asyncio
async def test_an_ingest_naming_the_projects_namespace_is_enriched_as_before(monkeypatch):
    run, queued = await _ingest(monkeypatch, _project(OWN), _sentinel(OWN))

    assert (run.ocp_pod_name, run.ocp_namespace) == (POD, OWN)
    assert queued == [{"run_id": str(run.id)}]


@pytest.mark.asyncio
async def test_an_ingest_into_a_project_without_a_namespace_is_enriched_as_before(monkeypatch):
    run, queued = await _ingest(monkeypatch, _project(None), _sentinel(OTHER))

    assert (run.ocp_pod_name, run.ocp_namespace) == (POD, OTHER)
    assert queued == [{"run_id": str(run.id)}]


# ── Runs stored earlier: the enrichment worker ───────────────────────────────


def _stored_run(monkeypatch, namespace, cfg):
    class _ReadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, _stmt):
            return SimpleNamespace(first=lambda: (POD, namespace, PROJECT_ID))

    lookup = AsyncMock(return_value=dict(FOUND))
    monkeypatch.setattr(ocp_enrichment, "AsyncSessionLocal", _ReadSession)
    monkeypatch.setattr(ocp_enrichment, "runtime_integrations_for", AsyncMock(return_value=cfg))
    monkeypatch.setattr(ocp_client, "get_pod_metadata", lookup)
    return lookup


@pytest.mark.asyncio
async def test_the_worker_skips_a_stored_run_whose_namespace_is_not_the_projects(monkeypatch):
    lookup = _stored_run(monkeypatch, OTHER, _cfg(OWN, "project"))
    run_id = uuid.uuid4()

    with structlog.testing.capture_logs() as logs:
        assert await ocp_enrichment.pod_metadata_for_run(run_id) is None

    lookup.assert_not_awaited()
    [refusal] = [entry for entry in logs if entry["event"] == "ocp_namespace_refused"]
    assert refusal["run_id"] == str(run_id) and refusal["project_namespace"] == OWN


@pytest.mark.asyncio
async def test_the_worker_skips_the_run_when_the_projects_namespace_cannot_be_read(monkeypatch):
    """No config means the rule cannot be checked: no lookup, not an unchecked one."""
    lookup = _stored_run(monkeypatch, OTHER, None)
    assert await ocp_enrichment.pod_metadata_for_run(uuid.uuid4()) is None
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_worker_enriches_a_run_in_the_projects_namespace(monkeypatch):
    cfg = _cfg(OWN, "project")
    lookup = _stored_run(monkeypatch, OWN, cfg)
    run_id = uuid.uuid4()

    assert await ocp_enrichment.pod_metadata_for_run(run_id) == (run_id, FOUND)
    lookup.assert_awaited_once_with(POD, OWN, config=cfg)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["saved", "environment"])
async def test_a_deployment_default_namespace_is_a_default_not_a_limit(monkeypatch, source):
    """Only the PROJECT's own saved namespace limits it; today's behaviour otherwise."""
    cfg = _cfg(OWN, source)
    lookup = _stored_run(monkeypatch, OTHER, cfg)
    run_id = uuid.uuid4()

    assert await ocp_enrichment.pod_metadata_for_run(run_id) == (run_id, FOUND)
    lookup.assert_awaited_once_with(POD, OTHER, config=cfg)


# ── Runs stored earlier: the server-side evidence lookup ─────────────────────


def _context(namespace) -> InvestigationContext:
    return InvestigationContext(
        project_id=str(PROJECT_ID), run_id=str(uuid.uuid4()), test_case_id=str(uuid.uuid4()),
        test_name="test_checkout", timestamp="2026-10-20T10:00:00Z",
        ocp_pod_name=POD, ocp_namespace=namespace,
    )


@pytest.mark.asyncio
async def test_the_evidence_lookup_skips_a_namespace_that_is_not_the_projects(monkeypatch):
    events = AsyncMock(return_value=f"{ocp_client.OCP_FOUND_PREFIX} OOMKilled")
    monkeypatch.setattr(ocp_client, "analyze_pod_events", events)

    assert await integration_evidence._ocp_lookup(_context(OTHER), _cfg(OWN, "project")) is None
    events.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_evidence_lookup_reads_the_projects_own_namespace(monkeypatch):
    events = AsyncMock(return_value=f"{ocp_client.OCP_FOUND_PREFIX} OOMKilled")
    monkeypatch.setattr(ocp_client, "analyze_pod_events", events)

    found = await integration_evidence._ocp_lookup(_context(OWN), _cfg(OWN, "project"))

    assert found is not None and found[1]["namespace"] == OWN
    events.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_evidence_lookup_is_unchanged_without_a_project_namespace(monkeypatch):
    events = AsyncMock(return_value=f"{ocp_client.OCP_FOUND_PREFIX} OOMKilled")
    monkeypatch.setattr(ocp_client, "analyze_pod_events", events)

    assert await integration_evidence._ocp_lookup(_context(OTHER), _cfg(OWN, "environment")) is not None
    events.assert_awaited_once()


# -- the client itself refuses, so every caller is covered ---------------------


def _client_cfg(source: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        ocp_enabled=True, ocp_api_url="https://ocp.example:6443", ocp_token="t",
        ocp_default_namespace="team-a", ocp_namespace_source=source, ocp_verify_ssl=True,
    )


def test_the_client_refuses_another_namespace_before_any_request(monkeypatch):
    import asyncio

    from app.services import ocp_client

    monkeypatch.setattr(ocp_client.settings, "AI_OFFLINE_MODE", False)

    async def _must_not_run(_url):
        raise AssertionError("the egress check ran: the lookup was not refused first")

    monkeypatch.setattr(ocp_client, "egress_block_reason", _must_not_run)
    refusals: list[str] = []

    result = asyncio.run(ocp_client.get_pod_metadata("pod-1", "team-b", config=_client_cfg("project"), refusals=refusals))

    assert result is None
    assert refusals == [ocp_client.OCP_OTHER_NAMESPACE_TEXT]


@pytest.mark.parametrize(("source", "namespace"), [("project", "team-a"), ("deployment", "team-b")])
def test_the_client_still_looks_up_the_projects_own_or_an_unlimited_namespace(monkeypatch, source, namespace):
    import asyncio

    from app.services import ocp_client

    monkeypatch.setattr(ocp_client.settings, "AI_OFFLINE_MODE", False)
    reached: list[str] = []

    async def _egress(url):
        reached.append(url)
        return "stopped here by the test"

    monkeypatch.setattr(ocp_client, "egress_block_reason", _egress)

    asyncio.run(ocp_client.get_pod_metadata("pod-1", namespace, config=_client_cfg(source)))

    assert reached == ["https://ocp.example:6443"]
