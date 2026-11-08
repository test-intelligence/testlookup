"""W39 STRAT-07: ``run_observability_service`` builds one run's evidence timeline from what is STORED (contract §0-§1).

The real service runs on a session double that EVALUATES each statement against an in-memory world (run row,
analysed test cases, the saved integrations row) and on a Mongo double that answers ``find`` / ``count_documents``
by every predicate the query carries -- any other Mongo method fails the test (a GET writes nothing). The stored
evidence is REAL: produced by the product's own fetchers / builders against the shared fake
(``tests/observability_timeline_samples.py``).

Pins: every D-4 state of every lane; Prometheus read per cluster without counting the first cluster twice; the
newest snapshot (and the count of the others); a Mongo outage reads ``unavailable`` with the store sentence; never a
fabricated time (redacted / missing / cut times are untimed); the dev seed's ``splunk`` / ``ocp_events`` rows never
show; OCP ``reason`` / ``message`` redacted on read and ``node`` never returned; the D-7 caps with ``truncated``; the
axis from timed items only; ``offline_now`` / ``configured_now`` booleans with no URL or token.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import ServerSelectionTimeoutError
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.services import observability_timeline_vocab as v
from app.services import prometheus_vocab as pv
from app.services import run_observability_service as svc
from tests import observability_timeline_samples as samples

UTC = timezone.utc
S = v.LANE_SENTENCES


class _Result:
    def __init__(self, rows=(), value=None):
        self._rows, self._value = list(rows), value

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """Evaluates the service's statements on the world; spies every write."""

    def __init__(self, world):
        self.w = world
        self.add, self.flush, self.commit, self.rollback = MagicMock(), AsyncMock(), AsyncMock(), AsyncMock()
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        w = self.w
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, p = str(compiled), dict(compiled.params)
        self.statements.append((sql, p))
        if sql.startswith("SELECT test_runs.id, test_runs.project_id, test_runs.start_time"):
            run = w.runs.get(p["id_1"])
            return _Result(rows=[run] if run else [])
        if sql.startswith("SELECT test_cases.id, test_cases.test_name, ai_analysis.evidence_references"):
            assert sql.count("LIKE") == 2 and p["param_1"] == "%query_splunk_logs%"
            needles = [p["param_1"].strip("%"), p["param_2"].strip("%")]
            rows = [a for a in w.analyses if a.run_id == p["test_run_id_1"]
                    and any(n in json.dumps(a.refs) for n in needles)]
            rows.sort(key=lambda a: (a.test_name, str(a.case_id)))
            return _Result(rows=[(a.case_id, a.test_name, a.refs) for a in rows][: p["param_3"]])
        if sql.startswith("SELECT ai_analysis.id"):
            rows = [a for a in w.analyses if a.run_id == p["test_run_id_1"]][: p["param_1"]]
            return _Result(rows=[(uuid.uuid4(),) for _ in rows])
        if sql.startswith("SELECT app_settings."):
            assert p["key_1"] == "integrations_config"
            return _Result(value=SimpleNamespace(value=dict(w.saved)) if w.saved is not None else None)
        raise AssertionError(f"unexpected statement: {sql[:160]}")


def _project(doc: dict, projection: dict) -> dict:
    out: dict = {}
    for key, on in projection.items():
        head, _, tail = key.partition(".")
        if not on or head == "_id" or head not in doc:
            continue
        if not tail:
            out[head] = copy.deepcopy(doc[head])
        elif isinstance(doc[head], dict) and tail in doc[head]:
            out.setdefault(head, {})[tail] = copy.deepcopy(doc[head][tail])
    return out


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, keys):
        for key, direction in reversed(keys):
            self.docs.sort(key=lambda d: str(d.get(key)), reverse=direction < 0)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, length=None):
        return self.docs[:length]


class _Snapshots:
    """``decision_evidence_snapshots``: reads filter by EVERY predicate; any other method is a write and fails."""

    def __init__(self, world):
        self.w, self.calls = world, []

    def _match(self, query):
        if self.w.mongo_down:
            raise ServerSelectionTimeoutError("no Mongo server")
        return [d for d in self.w.snapshots if all(d.get(k) == val for k, val in query.items())]

    def find(self, query, projection):
        self.calls.append(("find", dict(query)))
        return _Cursor([_project(d, projection) for d in self._match(query)])

    async def count_documents(self, query):
        self.calls.append(("count_documents", dict(query)))
        return len(self._match(query))

    def __getattr__(self, name):
        raise AssertionError(f"the timeline read called Mongo {name}()")


@pytest.fixture
def world(monkeypatch):
    project = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project, ocp_metadata=None,
                          start_time=datetime(2026, 10, 20, 9, 50, tzinfo=UTC),
                          end_time=datetime(2026, 10, 20, 10, 5, tzinfo=UTC), exec_started_at=None, exec_finished_at=None)
    w = SimpleNamespace(project=project, run=run, runs={run.id: run}, snapshots=[], analyses=[], saved=None,
                        mongo_down=False)
    w.mongo = _Snapshots(w)
    monkeypatch.setattr(svc, "get_mongo_db", lambda: {"decision_evidence_snapshots": w.mongo})
    return w


def _snapshot(w, log_findings, *, minutes_ago=0, project=None, run=None, pipeline=None) -> dict:
    """A stored snapshot as Mongo returns it (``created_at`` naive UTC, no ``tz_aware`` client)."""
    doc = {
        "_id": uuid.uuid4().hex, "schema_version": 3, "project_id": str(project or w.project),
        "test_run_id": str(run or w.run.id), "pipeline_run_id": str(pipeline or uuid.uuid4()),
        "created_at": datetime(2026, 10, 20, 10, 30) - timedelta(minutes=minutes_ago),
        "canonical_decision": {"status": "complete", "log_findings": log_findings},
        "content_sha256": "0" * 64,
    }
    w.snapshots.append(doc)
    return doc


def _analysis(w, test_name, refs, *, run_id=None):
    row = SimpleNamespace(case_id=uuid.uuid4(), test_name=test_name, run_id=run_id or w.run.id, refs=refs)
    w.analyses.append(row)
    return row


async def _build(w):
    session = _Session(w)
    body = await svc.build_run_observability_timeline(session, w.run.id)
    return body, session


def _lane(body, source):
    (lane,) = [lane for lane in body["lanes"] if lane["source"] == source]
    return lane


def _states(body):
    return [lane["state"] for lane in body["lanes"]]


def _clusters_findings(*clusters, top=None):
    """``log_findings`` as the context branch stores it: the first cluster copied to the top level."""
    first = dict(clusters[0]) if clusters else {}
    return {**first, **(top or {}), "cluster_findings": [dict(c) for c in clusters], "cluster_count": len(clusters),
            "status": "complete"}


def _cluster(cluster_id, **fields):
    return {"cluster_id": cluster_id, "status": "complete", "log_anomaly": {}, "log_summary": "", "evidence_refs": [],
            **fields}


# ── The shape and the never-analysed run ─────────────────────────────────────────────────────────────────────────


async def test_an_unknown_run_is_none(world):
    world.runs.clear()
    body, session = await _build(world)
    assert body is None and len(session.statements) == 1


async def test_a_run_nothing_analysed_says_so_in_every_lane_in_order(world):
    body, _ = await _build(world)
    assert [lane["source"] for lane in body["lanes"]] == list(v.LANE_SOURCES)
    assert [lane["label"] for lane in body["lanes"]] == [v.LANE_LABELS[s] for s in v.LANE_SOURCES]
    assert [lane["fetched_by"] for lane in body["lanes"]] == [v.LANE_FETCHED_BY[s] for s in v.LANE_SOURCES]
    assert _states(body) == ["not_analysed", "not_analysed", "not_recorded", "not_analysed", "not_analysed"]
    assert [lane["sentence"] for lane in body["lanes"]] == [
        S["prometheus_not_analysed"], S["splunk_trace_not_analysed"], S["ocp_events_not_recorded"],
        S["splunk_per_test_not_analysed"], S["ocp_per_test_not_analysed"]]
    assert body["analysis"] is None and body["axis"] is None
    assert body["run_window"] == {"start": world.run.start_time, "end": world.run.end_time}
    assert all(not (lane["series"] or lane["events"] or lane["untimed"] or lane["truncated"]) for lane in body["lanes"])


# ── Prometheus (Path B) ──────────────────────────────────────────────────────────────────────────────────────────


async def test_prometheus_reads_each_cluster_once_and_notes_the_cluster_without_data(world):
    ok = samples.app_metrics("ok", "p95 latency for checkout rose 3.1× in the failure window",
                             [samples.p95_series()])
    empty = samples.app_metrics("no_data", "No metric data for billing in the failure window.", service="billing")
    _snapshot(world, _clusters_findings(_cluster("cluster_1", app_metrics=ok), _cluster("cluster_2", app_metrics=empty)))
    lane = _lane((await _build(world))[0], "prometheus")
    assert lane["state"] == "evidence"
    assert [(s["cluster_id"], s["label"], s["unit"], s["rose"]) for s in lane["series"]] == [
        ("cluster_1", "p95 latency", "seconds", True)]          # the top-level copy of cluster_1 is not read again
    assert lane["series"][0]["points"] == samples.metric_points()
    assert lane["series"][0]["sentence"] == "p95 latency for checkout rose 3.1× in the failure window"
    assert lane["sentence"] == (f"{S['prometheus_evidence']} {S['partial']} "
                                "No metric data for billing in the failure window.")


async def test_prometheus_without_clusters_reads_the_top_level_once(world):
    block = samples.app_metrics("ok", "ok", [samples.p95_series()])
    _snapshot(world, {"status": "not_enough_evidence", "distributed_trace": {}, "cluster_findings": [],
                      "app_metrics": block, "log_summary": "Log evidence requires a scoped service and failure timestamp."})
    body, _ = await _build(world)
    lane = _lane(body, "prometheus")
    assert lane["state"] == "evidence" and [s["cluster_id"] for s in lane["series"]] == [None]
    assert lane["sentence"] == S["prometheus_evidence"]
    assert _lane(body, "splunk_trace")["state"] == "not_recorded"   # the no-context branch ran no trace


async def test_prometheus_no_data_is_none_found_with_the_stored_sentence_verbatim(world):
    block = samples.app_metrics("no_data", "No metric data for checkout in the failure window.")
    _snapshot(world, _clusters_findings(_cluster("cluster_1", app_metrics=block)))
    lane = _lane((await _build(world))[0], "prometheus")
    assert (lane["state"], lane["sentence"], lane["series"]) == (
        "none_found", "No metric data for checkout in the failure window.", [])


@pytest.mark.parametrize("outcome", ["offline_mode", "not_configured", "tool_not_allowed", "config_unavailable"])
async def test_a_prometheus_refusal_is_unavailable_with_its_vocabulary_sentence(world, outcome):
    block = samples.app_metrics(outcome, pv.OUTCOME_SENTENCES[outcome])
    _snapshot(world, _clusters_findings(_cluster("cluster_1", app_metrics=block)))
    lane = _lane((await _build(world))[0], "prometheus")
    assert (lane["state"], lane["sentence"]) == ("unavailable", pv.OUTCOME_SENTENCES[outcome])


async def test_series_that_survived_a_timeout_are_evidence_with_the_timeout_noted(world):
    block = samples.app_metrics("timed_out", pv.OUTCOME_SENTENCES["timed_out"], [samples.p95_series()])
    _snapshot(world, _clusters_findings(_cluster("cluster_1", app_metrics=block)))
    lane = _lane((await _build(world))[0], "prometheus")
    assert lane["state"] == "evidence" and len(lane["series"]) == 1
    assert lane["sentence"] == f"{S['prometheus_evidence']} {S['partial']} {pv.OUTCOME_SENTENCES['timed_out']}"


async def test_a_snapshot_whose_log_stage_was_off_is_not_recorded_on_both_path_b_lanes(world):
    _snapshot(world, {"status": "not_enough_evidence", "distributed_trace": {}, "log_anomaly": {},
                      "cluster_findings": [], "log_summary": "Log Intelligence feature flag is disabled."})
    body, _ = await _build(world)
    assert _states(body)[:2] == ["not_recorded", "not_recorded"]
    assert [lane["sentence"] for lane in body["lanes"][:2]] == [
        S["prometheus_not_recorded"], S["splunk_trace_not_recorded"]]
    assert body["analysis"]["other_analyses"] == 0


async def test_the_newest_snapshot_is_shown_and_the_others_counted_never_a_foreign_one(world):
    old = samples.app_metrics("no_data", "old")
    new = samples.app_metrics("no_data", "newest")
    _snapshot(world, _clusters_findings(_cluster("c", app_metrics=old)), minutes_ago=30)
    newest = _snapshot(world, _clusters_findings(_cluster("c", app_metrics=new)), minutes_ago=1)
    _snapshot(world, _clusters_findings(_cluster("c", app_metrics=old)), minutes_ago=10)
    # Same run id under ANOTHER project, and another run of this project: newer, never read.
    _snapshot(world, _clusters_findings(_cluster("c", app_metrics=samples.app_metrics("no_data", "foreign"))),
              project=uuid.uuid4())
    _snapshot(world, _clusters_findings(_cluster("c", app_metrics=samples.app_metrics("no_data", "other run"))),
              run=uuid.uuid4())
    body, _ = await _build(world)
    assert body["analysis"] == {"pipeline_run_id": newest["pipeline_run_id"],
                                "analysed_at": datetime(2026, 10, 20, 10, 29, tzinfo=UTC), "other_analyses": 2}
    assert _lane(body, "prometheus")["sentence"] == "newest"
    expected = {"project_id": str(world.project), "test_run_id": str(world.run.id)}
    assert world.mongo.calls == [("find", expected), ("count_documents", expected)]


async def test_a_mongo_outage_is_unavailable_with_the_store_sentence_never_none_found(world):
    world.mongo_down = True
    world.run.ocp_metadata = {"pod_name": samples.POD, "events": []}
    body, _ = await _build(world)
    assert _states(body) == ["unavailable", "unavailable", "none_found", "not_analysed", "not_analysed"]
    assert [lane["sentence"] for lane in body["lanes"][:2]] == [S["store_unreadable"]] * 2
    assert body["analysis"] is None


async def test_prometheus_is_cut_to_the_stored_caps_and_says_so(world):
    many = [samples.p95_series(samples.metric_points(count=130), template_id=f"t{i}", label=f"m{i}") for i in range(5)]
    block = {"outcome": "ok", "sentence": "", "service": "checkout", "series": many}   # a hand-edited, oversized block
    _snapshot(world, _clusters_findings(*[_cluster(f"cluster_{i}", app_metrics=block) for i in range(6)]))
    lane = _lane((await _build(world))[0], "prometheus")
    assert lane["truncated"] is True and len(lane["series"]) == 5 * 4
    assert {len(s["points"]) for s in lane["series"]} == {120}
    assert {s["cluster_id"] for s in lane["series"]} == {f"cluster_{i}" for i in range(5)}


# ── Splunk trace (Path B), on the trace the real tool returns from the fake ──────────────────────────────────────


async def test_the_trace_the_tool_stored_is_placed_at_splunks_own_times_without_the_host(world, monkeypatch):
    trace = await samples.distributed_trace(samples.config(samples.wire_fake(monkeypatch)))
    assert [s["timestamp"] for s in trace["trace_steps"]] == [
        "2026-10-20T10:00:05.000+00:00", "2026-10-20T10:00:07.000+00:00", "2026-10-20T10:00:09.000+00:00"]
    redacted = dict(trace["trace_steps"][0], timestamp="[REDACTED_PHONE]", message="epoch time redacted")
    missing = dict(trace["trace_steps"][1], timestamp="", message="no time at all")
    trace["trace_steps"] += [redacted, missing]
    _snapshot(world, _clusters_findings(_cluster("cluster_1", distributed_trace=trace)))
    body, _ = await _build(world)
    lane = _lane(body, "splunk_trace")
    assert (lane["state"], lane["sentence"], lane["truncated"]) == ("evidence", S["splunk_trace_evidence"], False)
    assert [(e["at"], e["level"], e["detail"], e["cluster_id"], e["zone_assumed"]) for e in lane["events"]] == [
        (datetime(2026, 10, 20, 10, 0, s, tzinfo=UTC), "ERROR", "checkout", "cluster_1", False) for s in (5, 7, 9)]
    assert lane["events"][0]["title"] == "db pool exhausted after 30000 ms"
    assert [(u["title"], u["reason"]) for u in lane["untimed"]] == [
        ("epoch time redacted", "time_unreadable"), ("no time at all", "no_time_stored")]
    text = json.dumps(body, default=str)
    assert "pay-db-proxy-1" not in text and "app-1" not in text and '"host"' not in text
    assert body["axis"] == {"start": world.run.start_time, "end": world.run.end_time}


async def test_a_trace_with_no_events_cannot_say_whether_splunk_was_reachable(world, monkeypatch):
    trace = await samples.distributed_trace(samples.config(samples.wire_fake(monkeypatch)), services=("ghost",))
    assert trace["trace_steps"] == [] and trace["causal_summary"].startswith(v.TRACE_NO_EVENTS_PREFIX)
    _snapshot(world, _clusters_findings(_cluster("cluster_1", distributed_trace=trace)))
    lane = _lane((await _build(world))[0], "splunk_trace")
    assert (lane["state"], lane["sentence"]) == ("unavailable", v.SPLUNK_TRACE_EMPTY_SENTENCE)


async def test_a_refused_trace_keeps_its_stored_sentence_and_a_failed_one_says_it_failed(world, monkeypatch):
    state = samples.wire_fake(monkeypatch)
    off = await samples.distributed_trace(dataclasses.replace(samples.config(state), splunk_enabled=False))
    assert off["causal_summary"].startswith("Splunk integration is disabled")
    _snapshot(world, _clusters_findings(_cluster("cluster_1", distributed_trace=off),
                                        _cluster("cluster_2", distributed_trace={"error": "TimeoutError"})))
    lane = _lane((await _build(world))[0], "splunk_trace")
    assert lane["state"] == "unavailable"
    assert lane["sentence"] == f"{off['causal_summary'].rstrip('. ')}; {S['splunk_trace_failed'].rstrip('. ')}."


async def test_a_trace_is_cut_to_forty_steps_and_says_so(world):
    steps = [{"service": "checkout", "timestamp": f"2026-10-20T10:00:{i:02d}.000+00:00", "level": "WARN",
              "message": f"step {i}", "host": "app-1"} for i in range(45)]
    _snapshot(world, _clusters_findings(_cluster("c1", distributed_trace={"trace_steps": steps, "causal_summary": "x"})))
    lane = _lane((await _build(world))[0], "splunk_trace")
    assert lane["truncated"] is True and len(lane["events"]) == 40 and lane["events"][-1]["title"] == "step 39"


# ── OpenShift pod events (Path C, at ingest), on the dict get_pod_metadata really returns ────────────────────────


async def test_the_ingested_pod_events_are_spans_and_instants_redacted_without_the_node(world, monkeypatch):
    meta = await samples.ocp_metadata(samples.config(samples.wire_fake(monkeypatch)))
    assert meta["node"] == samples.POD_HOST_IP                       # what is stored (unsanitized)
    meta["events"][1]["message"] = "Back-off restarting runner on 10.0.4.12 (ops@example.com)"
    world.run.ocp_metadata = meta
    body, _ = await _build(world)
    lane = _lane(body, "ocp_events")
    assert (lane["state"], lane["sentence"], lane["fetched_by"]) == ("evidence", S["ocp_events_evidence"], "ingest")
    assert [(e["title"], e["at"], e["end"], e["level"], e["count"]) for e in lane["events"]] == [
        ("OOMKilled", datetime(2026, 10, 20, 9, 55, tzinfo=UTC), datetime(2026, 10, 20, 10, 0, 2, tzinfo=UTC),
         "Warning", 2),
        ("BackOff", datetime(2026, 10, 20, 10, 0, 3, tzinfo=UTC), None, "Warning", 1),   # first == last: an instant
    ]
    assert lane["events"][1]["detail"] == "Back-off restarting runner on [REDACTED_IP] ([REDACTED_EMAIL])"
    text = json.dumps(body, default=str)
    assert samples.POD_HOST_IP not in text and samples.POD_NODE not in text and "registry.fake.test" not in text
    assert body["axis"] == {"start": datetime(2026, 10, 20, 9, 50, tzinfo=UTC), "end": world.run.end_time}


async def test_pod_data_with_no_events_is_none_found_and_none_at_all_is_not_recorded(world, monkeypatch):
    state = samples.wire_fake(monkeypatch)
    world.run.ocp_metadata = await samples.ocp_metadata(samples.config(state), samples.QUIET_POD,
                                                        samples.QUIET_NAMESPACE)
    assert world.run.ocp_metadata["events"] == []
    lane = _lane((await _build(world))[0], "ocp_events")
    assert (lane["state"], lane["sentence"]) == ("none_found", S["ocp_events_none_found"])
    world.run.ocp_metadata = None
    lane = _lane((await _build(world))[0], "ocp_events")
    assert (lane["state"], lane["sentence"]) == ("not_recorded", v.OCP_NOT_ENRICHED_SENTENCE)


@pytest.mark.parametrize("meta", [{"events": "OOMKilled"}, ["not", "a", "dict"], "text"])
async def test_unreadable_pod_data_is_unavailable(world, meta):
    world.run.ocp_metadata = meta
    lane = _lane((await _build(world))[0], "ocp_events")
    assert (lane["state"], lane["sentence"]) == ("unavailable", S["ocp_events_unavailable"])


async def test_pod_events_without_a_readable_time_are_untimed_and_the_newest_200_are_kept(world):
    events = [{"reason": f"Pulled{i}", "message": "m", "type": "Normal", "count": 1,
               "first_time": f"2026-10-20T09:{i // 60:02d}:{i % 60:02d}Z", "last_time": None} for i in range(205)]
    events += [{"reason": "NoTime", "message": "", "type": "Odd", "count": True, "first_time": None, "last_time": ""},
               {"reason": "BadTime", "message": "", "first_time": "yesterday", "last_time": None}]
    world.run.ocp_metadata = {"events": events}
    lane = _lane((await _build(world))[0], "ocp_events")
    assert lane["truncated"] is True and len(lane["events"]) == 200 and lane["untimed"] == []
    assert lane["events"][0]["title"] == "Pulled5" and lane["events"][-1]["title"] == "Pulled204"   # newest kept
    world.run.ocp_metadata = {"events": events[-2:]}
    lane = _lane((await _build(world))[0], "ocp_events")
    assert [(u["title"], u["reason"]) for u in lane["untimed"]] == [
        ("NoTime", "no_time_stored"), ("BadTime", "time_unreadable")]
    assert lane["state"] == "evidence" and lane["truncated"] is False


# ── Per-test lanes (Path A), on the evidence_references the analysis really stores ───────────────────────────────

SEED_REFS = [{"source": "splunk", "excerpt": "ERROR PaymentService: Connection timeout at 10:00:05"},   # seed_dev_data
             {"source": "ocp_events", "excerpt": "Pod restarted 3 times (OOMKilled)"}]


async def test_real_per_test_observations_become_events_and_untimed_checks_and_the_seed_never_shows(world, monkeypatch):
    cfg = samples.config(samples.wire_fake(monkeypatch))
    splunk = samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg))
    ocp = samples.references(samples.OCP_TOOL, await samples.ocp_text(cfg))
    assert splunk[0]["excerpt"].startswith(v.SPLUNK_FOUND_PREFIX) and ocp[0]["excerpt"].startswith(v.OCP_FOUND_PREFIX)
    pay = _analysis(world, "test_pay", splunk + ocp)
    _analysis(world, "test_seeded", SEED_REFS)
    _analysis(world, "test_other_run", splunk, run_id=uuid.uuid4())             # another run: never leaks
    body, _ = await _build(world)
    lane = _lane(body, "splunk_per_test")
    assert (lane["state"], lane["sentence"], lane["fetched_by"]) == (
        "evidence", S["splunk_per_test_evidence"], "per_test_analysis")
    assert [(e["at"], e["level"], e["title"], e["test_case_id"], e["test_name"]) for e in lane["events"]] == [
        (datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC), "ERROR", "db pool exhausted after 30000 ms", str(pay.case_id),
         "test_pay"),
        (datetime(2026, 10, 20, 10, 0, 7, tzinfo=UTC), "ERROR", "HTTP 503 from payment-gateway POST /authorize",
         str(pay.case_id), "test_pay"),
        (datetime(2026, 10, 20, 10, 0, 9, tzinfo=UTC), "ERROR",
         "SQLTransientConnectionException: connection is not available, request timed out", str(pay.case_id),
         "test_pay"),
    ]
    ocp_lane = _lane(body, "ocp_per_test")
    assert (ocp_lane["state"], ocp_lane["sentence"], ocp_lane["events"]) == ("evidence", S["ocp_per_test_evidence"], [])
    (check,) = ocp_lane["untimed"]
    assert check["title"] == f"Pod: {samples.POD} | Namespace: {samples.NAMESPACE} | Phase: Running"
    assert "[OOMKilled]" in check["detail"] and check["reason"] == "no_time_stored"
    text = json.dumps(body, default=str)
    assert "Connection timeout at 10:00:05" not in text and "Pod restarted 3 times" not in text
    assert "test_other_run" not in text


async def test_only_the_seeds_fabricated_rows_is_not_recorded(world):
    _analysis(world, "test_seeded", SEED_REFS + [
        {"source": "query_splunk_logs", "excerpt": "no kind: not a tool step"},
        {"source": "splunk", "kind": "tool_observation", "excerpt": "a seed source dressed as a tool step"},
        {"source": "fetch_stacktrace", "kind": "tool_observation", "excerpt": "another tool's observation"},
    ])
    body, _ = await _build(world)
    assert _states(body)[3:] == ["not_recorded", "not_recorded"]
    assert [lane["sentence"] for lane in body["lanes"][3:]] == [
        S["splunk_per_test_not_recorded"], S["ocp_per_test_not_recorded"]]


async def test_real_empty_answers_are_none_found_verbatim_and_refusals_unavailable_verbatim(world, monkeypatch):
    state = samples.wire_fake(monkeypatch)
    cfg = samples.config(state)
    no_hits = samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg, "ghost"))
    no_events = samples.references(samples.OCP_TOOL, await samples.ocp_text(cfg, samples.QUIET_POD,
                                                                             samples.QUIET_NAMESPACE))
    _analysis(world, "test_a", no_hits + no_events)
    body, _ = await _build(world)
    assert (_lane(body, "splunk_per_test")["state"], _lane(body, "splunk_per_test")["sentence"]) == (
        "none_found", no_hits[0]["excerpt"])
    assert no_hits[0]["excerpt"].startswith(v.SPLUNK_NO_HITS_PREFIX)
    assert (_lane(body, "ocp_per_test")["state"], _lane(body, "ocp_per_test")["sentence"]) == (
        "none_found", f"No events found for pod '{samples.QUIET_POD}' in namespace '{samples.QUIET_NAMESPACE}'.")
    world.analyses.clear()
    missing_pod = samples.references(samples.OCP_TOOL, await samples.ocp_text(cfg, "absent-pod-0", samples.NAMESPACE))
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    offline = samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg))
    _analysis(world, "test_b", offline + missing_pod)
    body, _ = await _build(world)
    assert (_lane(body, "splunk_per_test")["state"], _lane(body, "splunk_per_test")["sentence"]) == (
        "unavailable", offline[0]["excerpt"])
    assert offline[0]["excerpt"].startswith("Splunk lookup disabled: AI_OFFLINE_MODE=true")
    assert (_lane(body, "ocp_per_test")["state"], _lane(body, "ocp_per_test")["sentence"]) == (
        "unavailable", "OpenShift integration not configured or pod not found.")


async def test_several_different_empty_answers_read_the_lanes_own_sentence(world):
    for name, service in (("test_a", "alpha"), ("test_b", "beta")):
        text = f"{v.SPLUNK_NO_HITS_PREFIX} for service '{service}' within ±5 minutes of 2026-10-20T10:00:00Z."
        _analysis(world, name, samples.references(samples.SPLUNK_TOOL, text))
    lane = _lane((await _build(world))[0], "splunk_per_test")
    assert (lane["state"], lane["sentence"]) == ("none_found", S["splunk_per_test_none_found"])


async def test_an_unreadable_or_cut_line_is_untimed_never_placed(world):
    text = "\n".join([
        f"{v.SPLUNK_FOUND_PREFIX} (5 entries) ===",
        "[10/20/2026:10:00:01] [WARN] zone-less Splunk time",
        "[[REDACTED_PHONE]] [ERROR] an epoch the redactor masked",
        "[?] [?] the tool's placeholder for a missing _time",
        "    at com.example.Pay.charge(Pay.java:42)",
        "[2026-10-20T10:00:0",
    ])
    refs = samples.references(samples.SPLUNK_TOOL, text)
    _analysis(world, "test_cut", refs)
    lane = _lane((await _build(world))[0], "splunk_per_test")
    assert [(e["title"], e["at"], e["zone_assumed"], e["level"]) for e in lane["events"]] == [
        ("zone-less Splunk time", datetime(2026, 10, 20, 10, 0, 1, tzinfo=UTC), True, "WARN")]
    assert [(u["title"], u["reason"], u["detail"]) for u in lane["untimed"]] == [
        ("[[REDACTED_PHONE]] [ERROR] an epoch the redactor masked", "time_unreadable", None),
        ("[?] [?] the tool's placeholder for a missing _time", "no_time_stored", "at com.example.Pay.charge(Pay.java:42)"),
        ("[2026-10-20T10:00:0", "time_unreadable", None),
    ]


async def test_per_test_items_are_cut_at_200_and_say_so(world):
    lines = [f"[2026-10-20T10:{i // 60:02d}:{i % 60:02d}Z] [ERROR] e{i}" for i in range(9)]
    refs = [{"source": "query_splunk_logs", "kind": "tool_observation",
             "excerpt": "\n".join([f"{v.SPLUNK_FOUND_PREFIX} (9 entries) ===", *lines])}]
    for i in range(23):
        _analysis(world, f"test_{i:02d}", refs)                 # 23 × 9 = 207 lines
    lane = _lane((await _build(world))[0], "splunk_per_test")
    assert lane["truncated"] is True and len(lane["events"]) == 200


# ── The axis, the run window, the two "now" facts, read-only ─────────────────────────────────────────────────────


async def test_the_axis_spans_every_timed_item_and_the_run_window(world):
    first = datetime(2026, 10, 20, 9, 20, tzinfo=UTC).timestamp()   # a point before the run starts
    block = samples.app_metrics("ok", "ok", [samples.p95_series(samples.metric_points(start=first, count=4))])
    _snapshot(world, _clusters_findings(_cluster("c1", app_metrics=block)))
    world.run.ocp_metadata = {"events": [{"reason": "Late", "first_time": "2026-10-20T10:10:00Z",
                                          "last_time": "2026-10-20T10:20:00Z"}]}
    body, _ = await _build(world)
    assert body["axis"] == {"start": datetime(2026, 10, 20, 9, 20, tzinfo=UTC),
                            "end": datetime(2026, 10, 20, 10, 20, tzinfo=UTC)}


async def test_nothing_timed_means_no_axis_even_with_a_run_window_and_untimed_items(world):
    world.run.ocp_metadata = {"events": [{"reason": "NoTime", "first_time": None, "last_time": None}]}
    body, _ = await _build(world)
    assert body["run_window"] is not None and body["axis"] is None
    assert _lane(body, "ocp_events")["state"] == "evidence" and len(_lane(body, "ocp_events")["untimed"]) == 1


async def test_the_run_window_prefers_the_measured_wall_clock_and_is_null_when_an_end_is_unknown(world):
    world.run.exec_started_at = datetime(2026, 10, 20, 9, 51)                      # naive from SQLite: UTC
    world.run.exec_finished_at = datetime(2026, 10, 20, 10, 4, tzinfo=UTC)
    assert (await _build(world))[0]["run_window"] == {"start": datetime(2026, 10, 20, 9, 51, tzinfo=UTC),
                                                      "end": datetime(2026, 10, 20, 10, 4, tzinfo=UTC)}
    world.run.exec_finished_at = None
    world.run.end_time = None
    assert (await _build(world))[0]["run_window"] is None


async def test_the_now_facts_are_booleans_from_the_saved_row_and_never_a_url_or_token(world, monkeypatch):
    world.saved = {"splunk_enabled": True, "splunk_base_url": "https://splunk.corp.example:8089",
                   "splunk_api_token": "inline-legacy-value", "ocp_enabled": True, "ocp_api_url": "  ",
                   "prometheus_enabled": True, "prometheus_base_url": "https://prom.corp.example"}
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OCP_API_URL", None)
    body, _ = await _build(world)
    assert body["offline_now"] is True
    assert body["configured_now"] == {"splunk": True, "ocp": False, "prometheus": True}
    text = json.dumps(body, default=str)
    assert "corp.example" not in text and "inline-legacy-value" not in text and "8089" not in text


async def test_the_environment_counts_for_keys_never_saved_except_prometheus(world, monkeypatch):
    world.saved = None
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", True)
    monkeypatch.setattr(settings, "SPLUNK_BASE_URL", "https://env-splunk.example:8089")
    monkeypatch.setattr(settings, "OCP_ENABLED", False)
    monkeypatch.setattr(settings, "PROMETHEUS_URL", "http://prometheus:9090", raising=False)
    body, _ = await _build(world)
    assert body["offline_now"] is False
    assert body["configured_now"] == {"splunk": True, "ocp": False, "prometheus": False}


async def test_the_build_reads_and_never_writes(world, monkeypatch):
    cfg = samples.config(samples.wire_fake(monkeypatch))
    _snapshot(world, _clusters_findings(_cluster("c1", app_metrics=samples.app_metrics("ok", "ok", [samples.p95_series()]),
                                                 distributed_trace=await samples.distributed_trace(cfg))))
    _analysis(world, "test_pay", samples.references(samples.SPLUNK_TOOL, await samples.splunk_text(cfg)))
    world.run.ocp_metadata = await samples.ocp_metadata(cfg)
    body, session = await _build(world)
    assert _states(body) == ["evidence", "evidence", "evidence", "evidence", "not_recorded"]
    assert not (session.add.called or session.flush.called or session.commit.called or session.rollback.called)
    assert session.statements and all(sql.startswith("SELECT ") for sql, _p in session.statements)
    assert [call[0] for call in world.mongo.calls] == ["find", "count_documents"]
