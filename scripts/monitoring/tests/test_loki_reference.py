"""Private Loki persistence and Grafana's provisioned log-query contract."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
LOKI = ROOT / "infra/monitoring/loki"
DATASOURCES = ROOT / "infra/monitoring/grafana/telemetry-datasources.yml"
TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "0123456789abcdef"
QUERY = '{service_namespace="testlookup"} | trace_id="' + TRACE_ID + '" | request_id="fixture-request"'
HTTP = build_opener(ProxyHandler({}))


def test_reference_limits_persist_all_state_and_do_not_index_ids():
    config = yaml.safe_load((LOKI / "loki.yml").read_text())
    limits = config["limits_config"]
    assert config["auth_enabled"] is False  # relies on the private Compose trust boundary
    assert config["analytics"]["reporting_enabled"] is False
    assert config["server"]["grpc_listen_address"] == "127.0.0.1"
    assert config["common"]["replication_factor"] == 1
    assert config["ingester"]["wal"]["enabled"] is True
    assert config["ingester"]["wal"]["dir"] == "/loki/wal"
    assert config["compactor"]["working_directory"] == "/loki/compactor"
    assert config["compactor"]["retention_enabled"] is True
    assert config["compactor"]["delete_request_store"] == "filesystem"
    assert limits["retention_period"] == limits["max_query_lookback"] == "168h"
    assert limits["deletion_mode"] == "disabled"
    schema, = config["schema_config"]["configs"]
    assert (schema["store"], schema["schema"], schema["object_store"], schema["index"]["period"]) == ("tsdb", "v13", "filesystem", "24h")
    otlp = limits["otlp_config"]["resource_attributes"]
    assert otlp["ignore_defaults"] is True
    assert otlp["attributes_config"] == [{"action": "index_label", "attributes": [
        "service.name", "service.namespace", "deployment.environment.name",
    ]}]
    assert limits["max_global_streams_per_user"] == 500
    assert limits["max_entries_limit_per_query"] == 1000
    assert limits["query_timeout"] == "30s"
    assert limits["ingestion_rate_mb"] == 1 and limits["ingestion_burst_size_mb"] == 2
    assert limits["max_line_size"] == "64KB" and limits["max_line_size_truncate"] is False
    assert limits["max_structured_metadata_entries_count"] == 32


def test_opt_in_datasource_replacement_preserves_uids_and_bidirectional_links():
    config = yaml.safe_load(DATASOURCES.read_text())
    sources = {source["name"]: source for source in config["datasources"]}
    base = yaml.safe_load((ROOT / "infra/monitoring/grafana/provisioning/datasources/prometheus.yml").read_text())
    assert sources["Prometheus"] == base["datasources"][0]
    assert sources["Jaeger"]["uid"] == "jaeger" and sources["Loki"]["uid"] == "loki"
    assert not DATASOURCES.is_relative_to(ROOT / "infra/monitoring/grafana/provisioning")
    assert sources["Loki"]["url"] == "http://loki:3100"
    assert sources["Jaeger"]["url"] == "http://jaeger:16686"
    link = sources["Jaeger"]["jsonData"]["tracesToLogsV2"]
    assert link["datasourceUid"] == "loki" and link["customQuery"] is True
    assert link["query"] == '{service_namespace="testlookup"} | trace_id="$${__span.traceId}"'
    derived, = sources["Loki"]["jsonData"]["derivedFields"]
    assert derived["datasourceUid"] == "jaeger" and derived["url"] == "$${__value.raw}"
    body = json.dumps({"event": "fixture_event", "trace_id": TRACE_ID, "span_id": SPAN_ID})
    assert re.search(derived["matcherRegex"], body)[1] == TRACE_ID
    assert re.search(derived["matcherRegex"], json.dumps({"trace_id": "malformed"})) is None


def test_loki_image_has_an_immutable_upstream_and_verifiable_binary_pin():
    lock = json.loads((LOKI / "image-lock.json").read_text())
    assert re.fullmatch(r"docker.io/grafana/loki:3\.7\.10@sha256:[0-9a-f]{64}", lock["image"])
    assert lock["image_user"] == "10001" and lock["bundle"] == "telemetry-external"
    assert lock["digest_source"].startswith("https://registry-1.docker.io/v2/grafana/loki/manifests/")
    for binary in lock["release_binaries"].values():
        assert binary["url"].startswith("https://github.com/grafana/loki/releases/download/v3.7.10/")
        assert re.fullmatch(r"[0-9a-f]{64}", binary["sha256"])


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(url, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    with HTTP.open(request, timeout=10) as response:
        data = response.read()
        return json.loads(data) if data else None


def _eventually(operation, process, timeout=45):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        assert process.poll() is None, "reference process exited; inspect its scratch log"
        try:
            result = operation()
            if result:
                return result
        except Exception as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(f"reference endpoint did not become available: {type(last_error).__name__}")


def _stop(process, *, hard=False):
    if process.poll() is None:
        process.kill() if hard else process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@contextmanager
def _loki(tmp_path):
    binary = os.environ["LOKI_TEST_BINARY"]
    config = yaml.safe_load((LOKI / "loki.yml").read_text())
    data = tmp_path / "loki-data"
    data.mkdir()

    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        return str(data) + value[len("/loki"):] if isinstance(value, str) and value.startswith("/loki") else value

    config = relocate(config)
    port = _port()
    config["server"].update(http_listen_address="127.0.0.1", http_listen_port=port, grpc_listen_port=_port())
    path = tmp_path / "loki.yml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    subprocess.run([binary, "-config.file=" + str(path), "-verify-config=true"], check=True, capture_output=True, timeout=30)
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "loki.log").open("ab") as output:
        def start():
            process = subprocess.Popen(
                [binary, "-config.file=" + str(path)], stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            # /ready is plain text; use the raw response instead of JSON parsing.
            def ready():
                with HTTP.open(url + "/ready", timeout=2) as response:
                    return response.status == 200
            try:
                _eventually(ready, process)
            except BaseException:
                _stop(process)
                raise
            return process

        process = start()
        try:
            yield url, process, start
        finally:
            _stop(process)


def _push(url):
    body = json.dumps({"event": "fixture_event", "trace_id": TRACE_ID, "span_id": SPAN_ID, "request_id": "fixture-request"})
    attributes = {"service.name": "testlookup", "service.namespace": "testlookup", "deployment.environment.name": "test", "testlookup.service.role": "worker", "service.instance.id": "fixture-instance"}
    _request(url + "/otlp/v1/logs", {"resourceLogs": [{
        "resource": {"attributes": [{"key": key, "value": {"stringValue": value}} for key, value in attributes.items()]},
        "scopeLogs": [{"scope": {"name": "testlookup.fixture"}, "logRecords": [{
            "timeUnixNano": str(time.time_ns()), "severityNumber": 9, "severityText": "INFO",
            "traceId": TRACE_ID, "spanId": SPAN_ID, "body": {"stringValue": body},
            "attributes": [{"key": "request_id", "value": {"stringValue": "fixture-request"}}],
        }]}],
    }]})


def _query(url):
    params = {"query": QUERY, "start": str(time.time_ns() - 120_000_000_000), "end": str(time.time_ns() + 1_000_000_000), "limit": "10"}
    return _request(url + "/loki/api/v1/query_range?" + urlencode(params))["data"]["result"]


@pytest.mark.skipif(not os.getenv("LOKI_TEST_BINARY"), reason="set checksum-verified LOKI_TEST_BINARY for real ingestion/restart")
def test_actual_otlp_ingestion_metadata_query_and_wal_recovery(tmp_path):
    with _loki(tmp_path) as (url, process, restart):
        _push(url)
        result = _eventually(lambda: _query(url), process)
        assert json.loads(result[0]["values"][0][1])["trace_id"] == TRACE_ID
        series = _request(url + "/loki/api/v1/series?" + urlencode({"match[]": '{service_namespace="testlookup"}'}))["data"]
        assert series == [{"deployment_environment_name": "test", "service_name": "testlookup", "service_namespace": "testlookup"}]
        assert any((tmp_path / "loki-data/wal").iterdir())
        # No graceful flush: simulate child/container termination and recover
        # the acknowledged log from the same volume's WAL after restart.
        _stop(process, hard=True)
        replacement = restart()
        try:
            recovered = _eventually(lambda: _query(url), replacement)
            assert json.loads(recovered[0]["values"][0][1])["request_id"] == "fixture-request"
        finally:
            _stop(replacement)


@pytest.mark.skipif(not (os.getenv("LOKI_TEST_BINARY") and os.getenv("GRAFANA_TEST_HOME")), reason="set pinned Loki binary and Grafana home for real datasource query")
def test_actual_grafana_provisioning_macro_escaping_and_loki_query(tmp_path):
    with _loki(tmp_path) as (loki_url, loki_process, _), ExitStack() as dependencies:
        _push(loki_url)
        _eventually(lambda: _query(loki_url), loki_process)
        home = Path(os.environ["GRAFANA_TEST_HOME"])
        binary = Path(os.getenv("GRAFANA_TEST_BINARY", str(home / "bin/grafana")))
        provision = tmp_path / "provisioning/datasources"
        provision.mkdir(parents=True)
        config = yaml.safe_load(DATASOURCES.read_text())
        next(d for d in config["datasources"] if d.get("uid") == "loki")["url"] = loki_url
        if jaeger_binary := os.getenv("JAEGER_TEST_BINARY"):
            spec = importlib.util.spec_from_file_location("jaeger_reference", ROOT / "scripts/ops/verify_jaeger_persistence.py")
            assert spec is not None and spec.loader is not None
            jaeger = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(jaeger)
            jaeger_config = dependencies.enter_context(jaeger.running_jaeger(Path(jaeger_binary)))
            jaeger._write(jaeger_config, trace_id=TRACE_ID, span_id=SPAN_ID)
            next(d for d in config["datasources"] if d.get("uid") == "jaeger")["url"] = jaeger._url(jaeger_config, "query", "")
        (provision / "prometheus.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        port = _port()
        password = uuid.uuid4().hex
        env = os.environ | {
            "GF_PATHS_DATA": str(tmp_path / "grafana-data"), "GF_PATHS_LOGS": str(tmp_path / "grafana-logs"),
            "GF_PATHS_PLUGINS": str(tmp_path / "grafana-plugins"), "GF_PATHS_PROVISIONING": str(provision.parent),
            "GF_SERVER_HTTP_ADDR": "127.0.0.1", "GF_SERVER_HTTP_PORT": str(port),
            "GF_SECURITY_ADMIN_USER": "fixture", "GF_SECURITY_ADMIN_PASSWORD": password,
            "GF_PLUGINS_PREINSTALL_DISABLED": "true", "GF_PLUGINS_PREINSTALL_AUTO_UPDATE": "false",
            "GF_ANALYTICS_REPORTING_ENABLED": "false", "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
            "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false", "GF_UNIFIED_ALERTING_ENABLED": "false",
            "GF_FEATURE_TOGGLES_ENABLE": "jaegerEnableGrpcEndpoint",
        }
        headers = {"Authorization": "Basic " + base64.b64encode(("fixture:" + password).encode()).decode()}
        url = f"http://127.0.0.1:{port}"
        with (tmp_path / "grafana.log").open("wb") as output:
            grafana = subprocess.Popen(
                [str(binary), "server", "--homepath", str(home)], env=env, stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                _eventually(lambda: _request(url + "/api/health"), grafana, timeout=60)
                jaeger = _request(url + "/api/datasources/uid/jaeger", headers=headers)
                loki = _request(url + "/api/datasources/uid/loki", headers=headers)
                assert jaeger["jsonData"]["tracesToLogsV2"]["query"] == '{service_namespace="testlookup"} | trace_id="${__span.traceId}"'
                assert loki["jsonData"]["derivedFields"][0]["url"] == "${__value.raw}"
                assert _request(url + "/api/datasources/uid/loki/health", headers=headers)["status"] == "OK"
                payload = {"from": str(int(time.time() * 1000) - 120000), "to": str(int(time.time() * 1000)), "queries": [{
                    "refId": "A", "datasource": {"type": "loki", "uid": "loki"}, "expr": QUERY,
                    "queryType": "range", "maxLines": 10, "intervalMs": 1000, "maxDataPoints": 100,
                }]}
                result = _request(url + "/api/ds/query", payload, headers)
                assert result["results"]["A"].get("status", 200) == 200
                assert TRACE_ID in json.dumps(result) and "fixture-request" in json.dumps(result)
                if jaeger_binary:
                    assert _request(url + "/api/datasources/uid/jaeger/health", headers=headers)["status"] == "OK"
                    trace_query = {**payload, "queries": [{
                        "refId": "T", "datasource": {"type": "jaeger", "uid": "jaeger"},
                        "query": TRACE_ID, "queryType": "",
                    }]}
                    trace = _request(url + "/api/ds/query", trace_query, headers)
                    assert trace["results"]["T"].get("status", 200) == 200
                    assert TRACE_ID in json.dumps(trace)
            finally:
                _stop(grafana)
