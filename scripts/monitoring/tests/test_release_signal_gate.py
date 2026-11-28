"""Reject incomplete/stale/cross-candidate evidence without a real deployment."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

PATH = Path(__file__).resolve().parents[1] / "release_signal_gate.py"
SPEC = importlib.util.spec_from_file_location("release_signal_gate", PATH)
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)
NOW = datetime.now(timezone.utc)
PROJECT = str(uuid.uuid4())
SHA = "a" * 40
MANIFEST = {"schema_version": 1, "source_sha": SHA, "version": "v1.2.3", "images": {
    component: {"source_ref": f"ghcr.io/synthetic/{component}@sha256:{digit * 64}"}
    for component, digit in (("backend", "1"), ("frontend", "2"), ("mcp", "3"))
}}


def proof():
    roles = []
    for role, component in gate.ROLES.items():
        ref = MANIFEST["images"][component]["source_ref"]
        roles.append({"role": role, "component": component, "candidate": ref, "running": ref,
                      "verified_runtime_digests": [ref.rsplit("@", 1)[1]]})
    return {"schema_version": 1, "source_sha": SHA, "profile": "compose", "observed_at": NOW.isoformat(),
            "disposable": True, "project_id": PROJECT, "roles": roles}


def config(**changes):
    values = {"api": "http://api.invalid", "prometheus": "http://prometheus.invalid", "loki": "http://loki.invalid",
              "jaeger": "http://jaeger.invalid", "mcp": "http://mcp.invalid", "api_key": "synthetic-private-key",
              "project_id": PROJECT, "installation": "synthetic-site", "environment": "development", "timeout": 5}
    return gate.Config(**{**values, **changes})


def validate(value):
    return gate.validated_inputs(json.dumps(MANIFEST).encode(), json.dumps(value).encode(), now=NOW, project_id=PROJECT, profile="compose")


def test_runtime_manifest_and_descriptor_bound_config_identity():
    value = proof()
    value["roles"][0]["running"] = "ghcr.io/synthetic/backend@sha256:" + "4" * 64
    value["roles"][0]["verified_runtime_digests"].append("sha256:" + "4" * 64)
    assert validate(value)[0] == MANIFEST


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(disposable=False),
    lambda value: value.update(source_sha="b" * 40),
    lambda value: value.update(profile="hosted-production"),
    lambda value: value.update(project_id=str(uuid.uuid4())),
    lambda value: value.update(observed_at=(NOW - timedelta(minutes=11)).isoformat()),
    lambda value: value["roles"].pop(),
    lambda value: value["roles"][0].update(running="ghcr.io/synthetic/backend:latest"),
    lambda value: value["roles"][0].update(verified_runtime_digests=[]),
    lambda value: value["roles"][0].update(running="ghcr.io/synthetic/backend@sha256:" + "f" * 64),
])
def test_unproven_runtime_fails_before_any_write(mutation):
    value = proof()
    mutation(value)
    with pytest.raises(gate.GateError):
        validate(value)


class FakeHTTP:
    def __init__(self):
        self.runner = None
        self.http_count = 1
        self.task_count = 0
        self.worker_roles = ["worker", "worker-children"]
        self.calls = []
        self.collector_age = 0
        self.collector_count = 1
        self.collector_success = 1
        self.logs_missing = False
        self.worker_trace_missing = False
        self.quality_revision = SHA
        self.pending = False
        self.operations = [{"operation": name, "status": "completed"} for name in sorted(gate.REQUIRED_OPERATIONS)]
        self.run_id, self.task_id = str(uuid.uuid4()), str(uuid.uuid4())

    def request(self, method, base, path, *, body=None, headers=None):
        self.calls.append((method, base, path, body, headers))
        data, status, response_headers = None, 200, {}
        if base == "http://api.invalid":
            if path == "/health/version":
                self.http_count += 1
                data = {"build": {"revision": SHA}, "version": "1.2.3", "env": "development"}
            elif path == "/api/v1/projects/" + PROJECT:
                data = {"slug": "release-signal-synthetic"}
            elif method == "POST":
                assert body["run_ai"] is False and len(body["results"]) == 2
                assert body["project_id"] == PROJECT
                assert headers["traceparent"].split("-")[1] == self.runner.trace_id
                status, data = 202, {"run_id": self.run_id, "task_id": self.task_id}
                response_headers = {"x-request-id": headers["X-Request-ID"]}
            elif path.endswith("/downstream-status"):
                data = {"run_id": self.run_id, "status": "pending" if self.pending else "completed", "operations": self.operations}
            else:
                data = {"id": self.run_id, "project_id": PROJECT, "ingestion_complete": True, "ingestion_rejected_tests": 0,
                        "total_tests": 2, "passed_tests": 1, "failed_tests": 1}
        elif base == "http://prometheus.invalid":
            if path.startswith("/api/v1/targets"):
                targets = [{"scrapePool": "testlookup-backend", "health": "up", "labels": {"instance": "api:8000", "telemetry_role": "backend"},
                            "lastScrape": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}]
                targets.extend({"scrapePool": "testlookup-worker", "health": "up", "labels": {"instance": role + ":9100", "telemetry_role": role}} for role in self.worker_roles)
                data = {"status": "success", "data": {"activeTargets": targets}}
            else:
                query = parse_qs(urlsplit(path).query)["query"][0]
                if query.startswith("count("):
                    value = self.collector_count
                elif "time()-" in query:
                    value = self.collector_age
                elif "metric_collection_success" in query:
                    value = self.collector_success
                elif "http_requests_total" in query:
                    value = self.http_count
                else:
                    value = self.task_count
                data = {"status": "success", "data": {"resultType": "vector", "result": [{"value": [0, str(value)]}]}}
        elif base == "http://jaeger.invalid":
            processes, spans = {}, []
            roles = ["api", "worker"] if self.worker_trace_missing else ["api", "worker", "worker-children"]
            for index, role in enumerate(roles):
                processes[role] = {"serviceName": "testlookup", "tags": [{"key": key, "value": value} for key, value in {
                    "testlookup.service.role": role, "testlookup.build.revision": self.quality_revision,
                    "testlookup.installation": "synthetic-site", "service.version": "1.2.3", "deployment.environment.name": "development",
                }.items()]}
                spans.append({"processID": role, "spanID": str(index + 1) * 16, "operationName": "GET" if role == "api" else "celery.consume"})
            data = {"data": [{"traceID": self.runner.trace_id, "processes": processes, "spans": spans}]}
        elif base == "http://loki.invalid":
            query = parse_qs(urlsplit(path).query)["query"][0]
            require_text = [SHA, "synthetic-site", "1.2.3", "development"]
            assert all(value in query for value in require_text)
            role = query.split("testlookup_service_role=")[1].split('"')[1]
            field = "celery_task_id" if "celery_task_id=" in query else "request_id"
            digest = query.split(field + "=")[1].split('"')[1]
            span = {"api": "1", "worker": "2", "worker-children": "3"}.get(role, "0") * 16
            log = {"event": "mcp_request" if role == "mcp" else "http_request" if role == "api" else "celery_task_execution",
                   field: digest, "trace_id": self.runner.trace_id, "span_id": span}
            data = {"status": "success", "data": {"result": [] if self.logs_missing else [{"values": [["1", json.dumps(log)]]}]}}
        elif base == "http://mcp.invalid":
            status, response_headers = 404, {"x-request-id": headers["X-Request-ID"]}
        else:
            raise AssertionError("unexpected fixture endpoint")
        return gate.Response(status, data, response_headers)


def runner(**options):
    http = FakeHTTP()
    ticks = [0]
    def sleep(_seconds):
        ticks[0] += 1
    def send_task(name, *, queue, headers, kwargs):
        assert name == gate.PROBE_TASK and queue in {"default", "agent_children"}
        assert set(headers) == {"traceparent"}
        http.task_count += 1
        return SimpleNamespace(id=str(uuid.uuid4()), get=lambda timeout: {
            "correlation_id": kwargs["correlation_id"], "routing_key": queue, "worker": "synthetic-consumer",
        })
    value = gate.Gate(config(**options), MANIFEST, proof(), http=http,
                      celery=SimpleNamespace(send_task=send_task), clock=lambda: ticks[0], sleep=sleep)
    http.runner = value
    return value, http


def test_known_normal_operations_join_only_synthetic_client_build_business_and_signals():
    value, http = runner()
    result = value.run()
    assert result["status"] == "passed" and result["source_sha"] == SHA
    assert {"exact_api_delta", "exact_worker_deltas", "durable_business_completion", "linked_api_worker_logs_traces", "fresh_required_collectors"} <= set(result["checked"])
    assert len([call for call in http.calls if call[0] == "POST"]) == 1
    assert all(not (call[0] == "POST" and call[1] != "http://api.invalid") for call in http.calls)
    report = json.dumps(result)
    assert "synthetic-private-key" not in report and PROJECT not in report and http.task_id not in report


@pytest.mark.parametrize(("field", "value", "stage"), [
    ("worker_roles", ["worker"], "targets"),
    ("worker_trace_missing", True, "linked_api_worker_logs_traces"),
    ("logs_missing", True, "linked_api_worker_logs_traces"),
    ("quality_revision", "b" * 40, "traces"),
    ("pending", True, "durable_completion"),
    ("collector_age", 121, "collectors"),
    ("collector_success", 0, "collectors"),
    ("collector_count", 0, "collectors"),
])
def test_required_missing_bad_stale_evidence_cannot_pass(field, value, stage):
    check, http = runner()
    setattr(http, field, value)
    with pytest.raises(gate.GateError) as caught:
        check.run()
    assert caught.value.stage == stage


def test_terminal_failed_business_operation_is_refused_despite_worker_result_success():
    value, http = runner()
    http.operations[0]["status"] = "failed"
    with pytest.raises(gate.GateError, match="terminal_business_failure"):
        value.run()


def test_exact_delta_rejects_resets_and_overshoot():
    value, http = runner()
    value.inventory()
    query = "sum(" + value.selector("http_requests_total") + ")"
    with pytest.raises(gate.GateError, match="counter_reset"):
        value.delta(query, 100, 5)
    http.http_count = 106
    with pytest.raises(gate.GateError, match="overshot"):
        value.delta(query, 100, 5)


@pytest.mark.parametrize("endpoint", ["https://user:secret@api.invalid", "http://api.invalid/?token=private", "file:///tmp/test", "http://api.invalid/path"])
def test_credentials_in_urls_and_nonbase_endpoints_are_refused(endpoint):
    with pytest.raises(gate.GateError, match="invalid_base_url"):
        config(api=endpoint).validate()


def test_real_http_transport_keeps_credentials_at_origin_on_redirect():
    received = []
    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_args):
            pass
    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    class Redirect(Sink):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/sink")
            self.end_headers()
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (sink, origin)]
    for thread in threads:
        thread.start()
    try:
        response = gate.HTTP().request("GET", f"http://127.0.0.1:{origin.server_port}", "/probe", headers={"X-API-Key": "synthetic-private-key"})
        assert response.status == 302
        assert received == []
    finally:
        for server in (origin, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
