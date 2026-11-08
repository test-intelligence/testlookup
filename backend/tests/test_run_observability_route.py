"""W39 STRAT-07: ``GET /api/v1/runs/{run_id}/observability-timeline`` (route + guard + service, contract §1.1).

The real router, the real ``require_run_access`` guard and the real ``run_observability_service`` run on the session
and Mongo doubles of ``test_run_observability_service`` (each statement evaluated against an in-memory world),
extended with the guard's two reads. Pins: a member -- VIEWER included, like the run timing route -- reads 200 in
the §1.2 shape; a non-member and an unknown run get the guard's 404 before any service read; an ADMIN asking for an
unknown run gets the route's 404; an API key reads only runs of the project it is bound to; a Mongo outage is still
a 200; and the GET is PURE: no Postgres add / flush / commit / rollback / delete / merge, only SELECTs, no Mongo
method but ``find`` / ``count_documents``, no background task, and no fetcher, tool, LLM, ``read_secret``,
``runtime_integrations_for`` or intelligence snapshot write is ever reached.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from starlette import background

from app.core.deps import CREDENTIAL_KIND_API_KEY, _bind_api_key_project, _bind_credential_kind, get_current_active_user
from app.db.postgres import get_db
from app.routers import run_observability as route_module
from app.services import observability_timeline_vocab as v
from tests import observability_timeline_samples as samples
from tests.test_run_observability_service import _Result, _Session, _Snapshots, _analysis, _cluster, _clusters_findings
from tests.test_run_observability_service import _snapshot

PATH = "/api/v1/runs/{}/observability-timeline"
#: Everything that could fetch evidence, write a snapshot or reach a model / Vault. Reaching one fails the test.
FORBIDDEN = (
    ("app.tools.query_splunk", "splunk_correlation"),
    ("app.tools.reconstruct_trace", "_query_splunk"),
    ("app.services.ocp_client", "get_pod_metadata"),
    ("app.services.ocp_client", "analyze_pod_events"),
    ("app.services.app_metrics_service", "query_templates"),
    ("app.services.integration_config_service", "runtime_integrations_for"),
    ("app.services.integration_config_service", "resolve_runtime_integrations"),
    ("app.services.integration_config_service", "read_secret"),
    ("app.services.agent", "run_triage_agent"),
    ("app.services.llm_factory", "get_llm"),
    ("app.services.intelligence_snapshot_service", "save_snapshot"),
)


class _GuardedSession(_Session):
    """The service's world + the guard's run → project read and membership read; spies delete / merge too."""

    def __init__(self, world):
        super().__init__(world)
        self.delete, self.merge = MagicMock(), MagicMock()

    async def execute(self, stmt, params=None):
        sql = str(stmt.compile())
        if sql.startswith("SELECT test_runs.project_id"):
            self.statements.append((sql, {}))
            run = self.w.runs.get(next(iter(stmt.compile().params.values())))
            return _Result(value=run.project_id if run else None)
        if sql.startswith("SELECT project_members.id"):
            self.statements.append((sql, {}))
            return _Result(value=uuid.uuid4() if self.w.member else None)
        return await super().execute(stmt, params)


@pytest.fixture
async def real(monkeypatch):
    """REAL stored evidence, produced by the fetchers against the fake BEFORE ``world`` forbids every fetcher."""
    cfg = samples.config(samples.wire_fake(monkeypatch))
    return SimpleNamespace(
        trace=await samples.distributed_trace(cfg),
        splunk=samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg)),
        ocp=samples.references(samples.OCP_TOOL, await samples.ocp_text(cfg)),
        meta=await samples.ocp_metadata(cfg),
        no_hits=samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg, "ghost")),
    )


@pytest.fixture
def world(monkeypatch, real):
    project = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project, ocp_metadata=None,
                          start_time=datetime(2026, 10, 20, 9, 50, tzinfo=timezone.utc),
                          end_time=datetime(2026, 10, 20, 10, 5, tzinfo=timezone.utc),
                          exec_started_at=None, exec_finished_at=None)
    w = SimpleNamespace(project=project, run=run, runs={run.id: run}, snapshots=[], analyses=[], saved=None,
                        mongo_down=False, member=True, reached=[], background=[], real=real,
                        user=SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True, username="vera"))
    w.mongo = _Snapshots(w)

    class _MongoDb(dict):
        def __getitem__(self, name):
            if name != "decision_evidence_snapshots":
                w.reached.append(f"mongo collection {name}")
            return w.mongo

    monkeypatch.setattr("app.services.run_observability_service.get_mongo_db", lambda: _MongoDb())
    for module, name in FORBIDDEN:
        def _reached(*_a, _name=f"{module}.{name}", **_k):
            w.reached.append(_name)
            raise AssertionError(f"the observability timeline GET reached {_name}")
        monkeypatch.setattr(f"{module}.{name}", _reached)
    real_init = background.BackgroundTask.__init__

    def _task(self, func, *args, **kwargs):
        w.background.append(getattr(func, "__name__", repr(func)))
        real_init(self, func, *args, **kwargs)

    monkeypatch.setattr(background.BackgroundTask, "__init__", _task)
    w.session = _GuardedSession(w)

    async def _db():
        yield w.session

    app = FastAPI()
    app.include_router(route_module.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: w.user
    w.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testlookup.test")
    return w


async def _get(w, run_id=None):
    return await w.http.get(PATH.format(run_id or w.run.id))


def _pure(w):
    s = w.session
    assert not (s.add.called or s.flush.called or s.commit.called or s.rollback.called or s.delete.called
                or s.merge.called)
    assert all(sql.startswith("SELECT ") for sql, _p in s.statements)
    assert {call[0] for call in w.mongo.calls} <= {"find", "count_documents"}
    assert w.reached == [] and w.background == []


def _near_the_run(count=8):
    start = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc).timestamp()
    return samples.metric_points(start=start, count=count, step=300.0)


def all_evidence(w):
    """Every lane holds evidence: the deep analysis' metrics + trace, the ingested pod events, two per-test checks."""
    block = samples.app_metrics("ok", "p95 latency for checkout rose 3.1× in the failure window",
                                [samples.p95_series(_near_the_run())])
    _snapshot(w, _clusters_findings(_cluster("cluster_1", app_metrics=block, distributed_trace=w.real.trace)),
              minutes_ago=5)
    _snapshot(w, _clusters_findings(_cluster("cluster_1", app_metrics=samples.app_metrics("no_data", "older"))),
              minutes_ago=50)
    w.run.ocp_metadata = w.real.meta
    _analysis(w, "test_checkout_pays", w.real.splunk)
    _analysis(w, "test_runner_survives", w.real.ocp)
    _analysis(w, "test_seeded_by_dev_data", [{"source": "splunk", "excerpt": "ERROR PaymentService: timeout"}])


def mixed_states(w):
    """Each lane in a different honest state: no data / no trace events / never enriched / no hits / not run."""
    _snapshot(w, _clusters_findings(_cluster(
        "cluster_1", app_metrics=samples.app_metrics("no_data", "No metric data for checkout in the failure window."),
        distributed_trace={"trace_steps": [], "services_queried": [], "causal_summary":
                           "No log events found in the specified window. Check service names and time range."})))
    _analysis(w, "test_checkout_pays", w.real.no_hits)


# ── Who reads it ─────────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_viewer_member_reads_every_lane_and_the_get_is_pure(world):
    all_evidence(world)
    resp = await _get(world)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert list(body) == ["run_id", "run_window", "analysis", "offline_now", "configured_now", "axis", "lanes"]
    assert [(lane["source"], lane["state"]) for lane in body["lanes"]] == [(s, "evidence") for s in v.LANE_SOURCES]
    assert list(body["lanes"][0]) == ["source", "label", "fetched_by", "state", "sentence", "truncated", "series",
                                      "events", "untimed"]
    assert body["analysis"]["other_analyses"] == 1
    assert body["run_window"] == {"start": "2026-10-20T09:50:00Z", "end": "2026-10-20T10:05:00Z"}
    assert body["axis"] == {"start": "2026-10-20T09:30:00Z", "end": "2026-10-20T10:05:00Z"}
    ocp = body["lanes"][2]["events"][0]
    assert (ocp["title"], ocp["at"], ocp["end"]) == ("OOMKilled", "2026-10-20T09:55:00Z", "2026-10-20T10:00:02Z")
    assert body["lanes"][0]["series"][0]["points"][0] == [_near_the_run()[0][0], 0.2]
    assert "ERROR PaymentService: timeout" not in resp.text and samples.POD_HOST_IP not in resp.text
    _pure(world)


async def test_mixed_honest_states_are_sentences_never_an_empty_chart(world):
    mixed_states(world)
    body = (await _get(world)).json()
    assert [(lane["state"], lane["sentence"]) for lane in body["lanes"]] == [
        ("none_found", "No metric data for checkout in the failure window."),
        ("unavailable", v.SPLUNK_TRACE_EMPTY_SENTENCE),
        ("not_recorded", v.OCP_NOT_ENRICHED_SENTENCE),
        ("none_found", world.real.no_hits[0]["excerpt"]),
        ("not_recorded", v.LANE_SENTENCES["ocp_per_test_not_recorded"]),
    ]
    assert body["axis"] is None and all(not lane["series"] and not lane["events"] for lane in body["lanes"])
    _pure(world)


async def test_a_run_never_analysed_reads_not_analysed(world):
    body = (await _get(world)).json()
    assert [lane["state"] for lane in body["lanes"]] == [
        "not_analysed", "not_analysed", "not_recorded", "not_analysed", "not_analysed"]
    assert (body["analysis"], body["axis"]) == (None, None)
    _pure(world)


async def test_a_non_member_gets_the_guards_refusal_before_any_service_read(world):
    all_evidence(world)
    world.member = False
    resp = await _get(world)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    assert [sql.splitlines()[0].strip() for sql, _p in world.session.statements] == [
        "SELECT test_runs.project_id", "SELECT project_members.id"]
    assert world.mongo.calls == []
    _pure(world)


async def test_an_unknown_run_is_404_for_a_member_and_for_an_admin(world):
    resp = await _get(world, uuid.uuid4())
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    world.user.role = "ADMIN"                       # the guard lets an ADMIN through without its run lookup
    resp = await _get(world, uuid.uuid4())
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    assert world.mongo.calls == []
    _pure(world)


async def test_an_admin_who_is_not_a_member_reads_any_run(world):
    world.user.role, world.member = "ADMIN", False
    assert (await _get(world)).status_code == 200
    _pure(world)


async def test_an_api_key_reads_only_runs_of_the_project_it_is_bound_to(world):
    _bind_credential_kind(world.user, CREDENTIAL_KIND_API_KEY)
    _bind_api_key_project(world.user, world.project)
    assert (await _get(world)).status_code == 200
    world.user.role = "ADMIN"                       # even an ADMIN-owned key is held to its project
    _bind_api_key_project(world.user, uuid.uuid4())
    resp = await _get(world)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    _pure(world)


async def test_a_mongo_outage_is_still_a_200_with_the_store_sentence(world):
    all_evidence(world)
    world.mongo_down = True
    resp = await _get(world)
    assert resp.status_code == 200
    lanes = resp.json()["lanes"]
    assert [(lane["state"], lane["sentence"]) for lane in lanes[:2]] == [
        ("unavailable", v.STORE_UNREADABLE_SENTENCE)] * 2
    assert [lane["state"] for lane in lanes[2:]] == ["evidence", "evidence", "evidence"]
    _pure(world)


def test_the_handler_takes_no_background_tasks_and_no_query_parameters():
    params = list(inspect.signature(route_module.get_run_observability_timeline).parameters)
    assert params == ["run_id", "db", "_"]
