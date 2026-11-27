"""Validate the pinned collector and its real local durable export boundary."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "infra/monitoring/otel-collector/config.yml"
BINARY = os.environ.get("OTELCOL_TEST_BINARY")


def test_collector_has_scoped_otlp_input_bounded_persistent_exports_and_private_tool_metrics():
    config = yaml.safe_load(CONFIG.read_text())
    assert set(config["receivers"]) == {"otlp"}
    assert set(config["receivers"]["otlp"]["protocols"]) == {"http"}
    assert config["receivers"]["otlp"]["protocols"]["http"]["max_request_body_size"] == 1048576
    pipelines = config["service"]["pipelines"]
    assert set(pipelines) == {"logs", "traces", "metrics"}
    assert "debug" not in config["exporters"]
    storage = config["extensions"]["file_storage"]
    assert storage["fsync"] is True and storage["max_size"] == 64 * 1024 * 1024
    for pipeline in (pipelines["logs"], pipelines["traces"]):
        assert pipeline["processors"] == ["memory_limiter", "filter/identity", "transform/privacy"]
        exporter = config["exporters"][pipeline["exporters"][0]]
        queue = exporter["sending_queue"]
        assert queue["storage"] == "file_storage"
        assert queue["sizer"] == "bytes" and queue["queue_size"] == 32 * 1024 * 1024
        assert queue["block_on_overflow"] is False and queue["wait_for_result"] is False
        assert queue["batch"]["max_size"] == 65536
        assert exporter["retry_on_failure"]["max_elapsed_time"] == "10m"
    assert pipelines["metrics"]["processors"] == ["memory_limiter", "filter/mcp_metrics", "transform/mcp_metrics"]
    assert pipelines["metrics"]["exporters"] == ["prometheus/mcp_tools"]
    assert config["exporters"]["prometheus/mcp_tools"]["endpoint"] == "0.0.0.0:9464"
    scrape = yaml.safe_load((CONFIG.parent / "scrape.yml").read_text())
    job = next(item for item in scrape["scrape_configs"] if item["job_name"] == "testlookup-mcp-tools")
    assert job["static_configs"][0]["targets"] == ["otel-collector:9464"] and job["sample_limit"] == 20000
    image = json.loads((CONFIG.parent / "image-lock.json").read_text())
    assert "@sha256:" in image["image"] and image["user"] == "10001:10001"


@pytest.mark.skipif(not BINARY, reason="requires checksum-verified pinned Collector Contrib binary")
def test_pinned_collector_validates_checked_in_configuration():
    result = subprocess.run([BINARY, "validate", "--config", str(CONFIG)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Disposable collector condition did not become true before its deadline")


@contextmanager
def _collector(tmp_path, *, retry_lifetime="30s", destinations=None):
    logs_pb = pytest.importorskip("opentelemetry.proto.collector.logs.v1.logs_service_pb2")
    traces_pb = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    state = {"failed": True, "failed_attempts": 0, "received": []}
    lock = threading.Lock()

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
            message = logs_pb.ExportLogsServiceRequest() if self.path.endswith("/logs") else traces_pb.ExportTraceServiceRequest()
            message.ParseFromString(payload)
            with lock:
                failed = state["failed"]
                if failed:
                    state["failed_attempts"] += 1
                if not failed:
                    state["received"].append(message)
            self.send_response(503 if failed else 200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=sink.serve_forever, daemon=True)
    thread.start()
    config = yaml.safe_load(CONFIG.read_text())
    otlp_port, health_port, metrics_port, mcp_metrics_port = _port(), _port(), _port(), _port()
    config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{otlp_port}"
    config["extensions"]["health_check"]["endpoint"] = f"127.0.0.1:{health_port}"
    storage = config["extensions"]["file_storage"]
    storage["directory"] = str(tmp_path / "spool")
    storage["compaction"]["directory"] = str(tmp_path / "compaction")
    metrics = config["service"]["telemetry"]["metrics"]["readers"][0]["pull"]["exporter"]["prometheus"]
    metrics.update(host="127.0.0.1", port=metrics_port)
    for name, exporter in config["exporters"].items():
        if name == "prometheus/mcp_tools":
            exporter["endpoint"] = f"127.0.0.1:{mcp_metrics_port}"
            continue
        exporter["endpoint"] = (destinations or {}).get(name, f"http://127.0.0.1:{sink.server_port}")
        exporter["timeout"] = "200ms"
        exporter["retry_on_failure"].update(initial_interval="200ms", max_interval="1s", max_elapsed_time=retry_lifetime)
        exporter["sending_queue"].update(queue_size=4096, num_consumers=1)
        exporter["sending_queue"]["batch"].update(min_size=1, max_size=1024, flush_timeout="50ms")
    output = tmp_path / "config.yml"
    output.write_text(yaml.safe_dump(config))
    opener = build_opener(ProxyHandler({}))
    process = None
    handle = None

    def get(port, path):
        with opener.open(f"http://127.0.0.1:{port}" + path, timeout=1) as response:
            return response.read().decode()

    def start():
        nonlocal process, handle
        handle = (tmp_path / "collector.log").open("ab")
        process = subprocess.Popen([BINARY, "--config", str(output)], stdout=handle, stderr=subprocess.STDOUT)

        def ready():
            if process.poll() is not None:
                raise AssertionError((tmp_path / "collector.log").read_text(errors="replace")[-5000:])
            try:
                return get(health_port, "/")
            except OSError:
                return False
        _eventually(ready)

    def kill():
        nonlocal process, handle
        if process is not None:
            process.kill()  # Deliberately ungraceful, exercising durable replay.
            process.wait(timeout=5)
            process = None
        if handle is not None:
            handle.close()
            handle = None

    def post(signal, payload):
        request = Request(f"http://127.0.0.1:{otlp_port}/v1/{signal}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=2) as response:
                return response.status
        except HTTPError as error:
            return error.code

    try:
        start()
        yield state, lock, post, lambda surface="collector": get(mcp_metrics_port if surface == "mcp" else metrics_port, "/metrics"), kill, start
    finally:
        kill()
        sink.shutdown()
        sink.server_close()
        thread.join(timeout=2)


def _attribute(key, value):
    return {"key": key, "value": {"stringValue": value}}


def _resource():
    return {"attributes": [_attribute("service.name", "testlookup"), _attribute("service.namespace", "testlookup"),
                           _attribute("deployment.environment.name", "test"),
                           _attribute("testlookup.service.role", "api"), _attribute("testlookup.build.revision", "a" * 40),
                           _attribute("testlookup.installation", "synthetic-site"), _attribute("private.payload", "synthetic-private-canary")]}


def _logs(identifier, *, event="http_request"):
    return {"resourceLogs": [{"resource": _resource(), "scopeLogs": [{"scope": {"name": "synthetic"}, "logRecords": [{
        "timeUnixNano": str(time.time_ns()), "traceId": f"{identifier:032x}", "spanId": f"{identifier:016x}",
        "eventName": event, "severityText": "INFO", "severityNumber": 9,
        "body": {"stringValue": "synthetic-private-canary"},
        "attributes": [_attribute("request_id", "a" * 64), _attribute("celery_task_id", "raw-private-id"),
                       _attribute("private.payload", "synthetic-private-canary")],
    }]}]}]}


def _traces(identifier):
    now = time.time_ns()
    return {"resourceSpans": [{"resource": _resource(), "scopeSpans": [{"scope": {"name": "synthetic"}, "spans": [{
        "traceId": f"{identifier:032x}", "spanId": f"{identifier:016x}", "name": "synthetic.operation", "kind": 2,
        "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 1000),
        "attributes": [_attribute("http.route", "/health"), _attribute("http.request.body", "synthetic-private-canary")],
        "status": {"code": 2, "message": "synthetic-private-canary"},
        "events": [{"timeUnixNano": str(now), "name": "synthetic-private-canary", "attributes": [_attribute("payload", "synthetic-private-canary")]}],
        # Links rely on the application's full export-boundary sanitization;
        # the Collector preserves that already-sanitized causal relationship.
        "links": [{"traceId": "f" * 32, "spanId": "f" * 16}],
    }]}]}]}


def _metric_values(text, prefix):
    return [float(line.rsplit(" ", 1)[1]) for line in text.splitlines() if line.startswith(prefix) and not line.startswith("#")]


def _mcp_metrics(instance, count, *, role="mcp", scope="testlookup.mcp", outcome="success"):
    attributes = [_attribute("tool", "list_test_runs"), _attribute("transport", "sse"),
                  _attribute("outcome", outcome), _attribute("request_id", "synthetic-private-canary")]
    now = time.time_ns()
    point = {"attributes": attributes, "startTimeUnixNano": str(now - 1_000_000_000), "timeUnixNano": str(now)}
    resource = _resource()
    for entry in resource["attributes"]:
        if entry["key"] == "testlookup.service.role":
            entry["value"]["stringValue"] = role
    resource["attributes"].append(_attribute("service.instance.id", instance))
    return {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{"scope": {"name": scope}, "metrics": [
        {"name": "testlookup.mcp.tool.calls", "unit": "1", "sum": {"aggregationTemporality": 2, "isMonotonic": True,
                 "dataPoints": [{**point, "asInt": str(count)}]}},
        {"name": "testlookup.mcp.tool.duration", "unit": "s", "histogram": {"aggregationTemporality": 2,
                 "dataPoints": [{**point, "count": str(count), "sum": count * 0.15, "bucketCounts": ["0", str(count), "0"], "explicitBounds": [0.1, 1.0]}]}},
        {"name": "testlookup.synthetic.unsupported", "unit": "1", "gauge": {"dataPoints": [{**point, "asInt": "1"}]}},
    ]}]}]}


@pytest.mark.skipif(not BINARY, reason="requires checksum-verified pinned Collector Contrib binary")
def test_real_mcp_export_is_bounded_and_preserves_independent_process_streams(tmp_path):
    processes = [f"00000000-0000-0000-0000-{suffix:012d}" for suffix in range(1, 6)]
    with _collector(tmp_path) as (_state, _lock, post, metrics, _kill, _start):
        for index, labels in enumerate(({}, {}, {"role": "api"}, {"scope": "synthetic.extra"}, {"outcome": "unexpected"})):
            assert post("metrics", _mcp_metrics(processes[index], index + 2, **labels)) == 200
        scrape = metrics("mcp")
        assert sorted(_metric_values(scrape, "testlookup_mcp_tool_calls_total")) == [2, 3]
        assert sorted(_metric_values(scrape, "testlookup_mcp_tool_duration_seconds_count")) == [2, 3]
        assert "testlookup_mcp_tool_duration_seconds_bucket" in scrape
        assert all(instance in scrape for instance in processes[:2])
        assert all(instance not in scrape for instance in processes[2:])
        assert 'tool="list_test_runs"' in scrape and 'testlookup_installation="synthetic-site"' in scrape
        assert "synthetic-private-canary" not in scrape and "request_id" not in scrape and "unsupported" not in scrape


@pytest.mark.skipif(not BINARY, reason="requires checksum-verified pinned Collector Contrib binary")
def test_real_queue_overflow_privacy_and_recovery_after_abrupt_restart(tmp_path):
    expected_logs, expected_traces = set(), set()
    with _collector(tmp_path) as (state, lock, post, metrics, kill, start):
        assert post("logs", _logs(9999, event="unapproved_private_event")) == 200
        rejected = []
        began = time.monotonic()
        for index in range(1, 50):
            for signal, payload, expected in (("logs", _logs(index), expected_logs), ("traces", _traces(index), expected_traces)):
                status = post(signal, payload)
                if status == 200:
                    expected.add(index)
                else:
                    assert status in (429, 500, 503)
                    rejected.append((signal, index))
        assert time.monotonic() - began < 15  # Full queues refuse; the producer is not held indefinitely.
        assert expected_logs and expected_traces and rejected
        _eventually(lambda: sum(_metric_values(metrics(), "otelcol_exporter_enqueue_failed_")) > 0)
        observed = metrics()
        sizes = _metric_values(observed, "otelcol_exporter_queue_size")
        capacities = _metric_values(observed, "otelcol_exporter_queue_capacity")
        assert sizes and max(sizes) <= 4096 and capacities and set(capacities) == {4096.0}
        # Failed attempts still retrying are not terminal send_failed metrics.
        _eventually(lambda: state["failed_attempts"] > 0)
        kill()
        spool_files = [path for path in (tmp_path / "spool").rglob("*") if path.is_file()]
        assert spool_files and all(b"synthetic-private-canary" not in path.read_bytes() for path in spool_files)
        with lock:
            state["failed"] = False
        start()

        def recovered():
            seen_logs, seen_traces = set(), set()
            with lock:
                messages = list(state["received"])
            for message in messages:
                assert "synthetic-private-canary" not in str(message)
                if hasattr(message, "resource_logs"):
                    for resource in message.resource_logs:
                        metadata = {item.key: item.value.string_value for item in resource.resource.attributes}
                        assert metadata["testlookup.build.revision"] == "a" * 40
                        assert metadata["testlookup.installation"] == "synthetic-site"
                        for scope in resource.scope_logs:
                            for record in scope.log_records:
                                seen_logs.add(int.from_bytes(record.trace_id, "big"))
                                body = json.loads(record.body.string_value)
                                assert body["event"] == "http_request" and body["request_id"] == "a" * 64
                                assert "celery_task_id" not in body
                                assert body["trace_id"] == record.trace_id.hex()
                else:
                    for resource in message.resource_spans:
                        metadata = {item.key: item.value.string_value for item in resource.resource.attributes}
                        assert metadata["testlookup.build.revision"] == "a" * 40
                        assert metadata["testlookup.installation"] == "synthetic-site"
                        for scope in resource.scope_spans:
                            for span in scope.spans:
                                seen_traces.add(int.from_bytes(span.trace_id, "big"))
                                assert len(span.links) == 1 and not span.status.message
            assert 9999 not in seen_logs
            return expected_logs <= seen_logs and expected_traces <= seen_traces
        _eventually(recovered)


@pytest.mark.skipif(not BINARY, reason="requires checksum-verified pinned Collector Contrib binary")
def test_real_retry_exhaustion_exposes_terminal_loss(tmp_path):
    with _collector(tmp_path, retry_lifetime="2s") as (state, lock, post, metrics, kill, start):
        assert post("logs", _logs(1)) == 200
        assert post("traces", _traces(1)) == 200
        _eventually(lambda: sum(_metric_values(metrics(), "otelcol_exporter_send_failed_log_records")) > 0)
        _eventually(lambda: sum(_metric_values(metrics(), "otelcol_exporter_send_failed_spans")) > 0)
        assert state["failed_attempts"] > 0 and not state["received"]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not (BINARY and os.environ.get("LOKI_TEST_BINARY") and os.environ.get("JAEGER_TEST_BINARY")), reason="requires checksum-verified Collector, Loki and Jaeger binaries")
def test_real_collector_to_both_reference_stores_and_same_id_readback(tmp_path):
    loki = _module("collector_loki_fixture", ROOT / "scripts/monitoring/tests/test_loki_reference.py")
    jaeger = _module("collector_jaeger_fixture", ROOT / "scripts/ops/verify_jaeger_persistence.py")
    (tmp_path / "loki").mkdir()
    (tmp_path / "collector").mkdir()
    with ExitStack() as stack:
        logs_url, loki_process, _ = stack.enter_context(loki._loki(tmp_path / "loki"))
        trace_config = stack.enter_context(jaeger.running_jaeger(Path(os.environ["JAEGER_TEST_BINARY"])))
        destinations = {"otlp_http/logs": logs_url + "/otlp", "otlp_http/traces": jaeger._url(trace_config, "ingest", "")}
        _, _, post, _, kill, start = stack.enter_context(_collector(tmp_path / "collector", destinations=destinations))

        def joint_trace_id(identifier):
            # Jaeger's legacy JSON renders all-zero high bits as a 64-bit ID.
            # Use full 128-bit IDs for an exact cross-store string comparison.
            return "1122334455667788" + f"{identifier:016x}"

        def read_log(identifier):
            trace_id = joint_trace_id(identifier)
            query = '{service_namespace="testlookup"} | trace_id="' + trace_id + '" | request_id="' + "a" * 64 + '"'
            parameters = {"query": query, "start": str(time.time_ns() - 120_000_000_000), "end": str(time.time_ns() + 1_000_000_000), "limit": "10"}
            response = loki._request(logs_url + "/loki/api/v1/query_range?" + urlencode(parameters))
            results = response["data"]["result"]
            if not results:
                return False
            # Query results may promote filtered structured metadata into the
            # returned stream. The series API proves what is actually indexed.
            series = loki._request(logs_url + "/loki/api/v1/series?" + urlencode({"match[]": '{service_namespace="testlookup"}'}))["data"]
            assert series == [{"deployment_environment_name": "test", "service_name": "testlookup", "service_namespace": "testlookup"}]
            body = json.loads(results[0]["values"][0][1])
            assert body["trace_id"] == trace_id and body["event"] == "http_request"
            assert "synthetic-private-canary" not in json.dumps(response)
            return True

        for identifier in (101, 102):
            traces, logs = _traces(identifier), _logs(identifier)
            traces["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] = joint_trace_id(identifier)
            logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["traceId"] = joint_trace_id(identifier)
            assert post("traces", traces) == 200
            assert post("logs", logs) == 200
            _eventually(lambda: jaeger._contains_trace(trace_config, joint_trace_id(identifier)))
            _eventually(lambda: read_log(identifier))
            assert loki_process.poll() is None
            if identifier == 101:
                kill()
                start()
