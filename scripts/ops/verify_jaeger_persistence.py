"""Prove pinned Jaeger persistence/TTL with synthetic spans on loopback only.

Supply a checksum-verified Jaeger 2.23.0 release executable. The verifier
derives a private test config from the production reference, changes only
storage paths/listen addresses, and starts no container or live deployment.
All data and logs live in a temporary directory and contain synthetic data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

VERSION = "v2.23.0"
COMMIT = "d723f91447f29a345c288f37eb8d1ce7807bc0be"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "infra/monitoring/jaeger.yml"
_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_TRACE_ID = "1" * 32
_SPAN_ID = "2" * 16
_SERVICE = "testlookup-jaeger-persistence-probe"


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def local_config(source: dict, directory: Path, *, ttl: str | None = None) -> dict:
    """Keep the production pipeline/storage contract; isolate local test I/O."""
    config = deepcopy(source)
    storage = config["extensions"]["jaeger_storage"]["backends"]["incident_traces"]["badger"]
    storage["directories"] = {
        "keys": str(directory / "keys"), "values": str(directory / "values"),
    }
    if ttl is not None:
        storage["ttl"]["spans"] = ttl
    config["extensions"]["jaeger_query"]["http"]["endpoint"] = f"127.0.0.1:{_port()}"
    config["extensions"]["jaeger_query"]["grpc"]["endpoint"] = f"127.0.0.1:{_port()}"
    config["extensions"]["healthcheckv2"]["http"]["endpoint"] = f"127.0.0.1:{_port()}"
    config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{_port()}"
    prometheus = config["service"]["telemetry"]["metrics"]["readers"][0]["pull"]["exporter"]["prometheus"]
    prometheus.update(host="127.0.0.1", port=_port())
    return config


def _url(config: dict, surface: str, path: str) -> str:
    if surface == "query":
        address = config["extensions"]["jaeger_query"]["http"]["endpoint"]
    elif surface == "health":
        address = config["extensions"]["healthcheckv2"]["http"]["endpoint"]
    else:
        address = config["receivers"]["otlp"]["protocols"]["http"]["endpoint"]
    return f"http://{address}{path}"


def _request(url: str, payload: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        response = _HTTP.open(request, timeout=2)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(1_048_577)
        if len(body) > 1_048_576:
            raise RuntimeError("synthetic Jaeger response exceeded proof budget")
        return response.code, body


def _span(trace_id: str = _TRACE_ID, span_id: str = _SPAN_ID) -> dict:
    now = time.time_ns()
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": _SERVICE}},
            {"key": "service.namespace", "value": {"stringValue": "testlookup"}},
        ]},
        "scopeSpans": [{"scope": {"name": "testlookup.persistence.probe"}, "spans": [{
            "traceId": trace_id, "spanId": span_id, "name": "synthetic-restart-proof",
            "kind": 1, "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 1_000_000),
        }]}],
    }]}


def _contains_trace(config: dict, trace_id: str = _TRACE_ID) -> bool:
    status, body = _request(_url(config, "query", f"/api/traces/{trace_id}"))
    if status == 404:
        return False
    if status != 200:
        raise RuntimeError(f"synthetic Jaeger trace query returned HTTP {status}")
    data = json.loads(body).get("data") or []
    return any(trace.get("traceID") == trace_id and trace.get("spans") for trace in data)


def _wait_for(check, *, seconds: float, description: str) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if check():
                return
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for synthetic {description}")
        time.sleep(0.05)


def _start(binary: Path, config: dict, root: Path):
    config_path = root / "jaeger-test.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    log = (root / "jaeger.log").open("ab")
    process = subprocess.Popen(
        [str(binary), f"--config={config_path}"], cwd=root,
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    def ready():
        if process.poll() is not None:
            raise RuntimeError("synthetic Jaeger process exited before readiness")
        status, _ = _request(_url(config, "health", "/status"))
        return status == 200

    try:
        _wait_for(ready, seconds=20, description="Jaeger readiness")
    except BaseException:
        _stop(process)
        log.close()
        raise
    return process, log


def _stop(process, *, crash: bool = False) -> None:
    if process.poll() is None:
        process.kill() if crash else process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _write(config: dict, trace_id: str = _TRACE_ID, span_id: str = _SPAN_ID) -> None:
    status, body = _request(_url(config, "ingest", "/v1/traces"), _span(trace_id, span_id))
    if status != 200:
        raise RuntimeError(f"synthetic Jaeger OTLP write returned HTTP {status}")
    result = json.loads(body or b"{}")
    if int(result.get("partialSuccess", {}).get("rejectedSpans", 0)):
        raise RuntimeError("synthetic Jaeger OTLP write rejected spans")
    _wait_for(lambda: _contains_trace(config, trace_id), seconds=5, description="trace readback")


@contextmanager
def running_jaeger(binary: Path, *, config_path: Path = DEFAULT_CONFIG):
    """Local integration fixture for a separately verified release executable.

    The caller may emit a matching synthetic log/trace ID and point an isolated
    Grafana at ``_url(config, "query", "")``. No deployment ports are used.
    """
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="testlookup-jaeger-integration-") as temporary:
        root = Path(temporary)
        config = local_config(source, root / "data")
        process, log = _start(binary.resolve(strict=True), config, root)
        try:
            yield config
        finally:
            _stop(process)
            log.close()


def prove(binary: Path, *, config_path: Path = DEFAULT_CONFIG) -> dict:
    binary = binary.resolve(strict=True)
    version = subprocess.run([str(binary), "version"], capture_output=True, text=True, check=True, timeout=15)
    identity = json.loads(version.stdout)
    if identity.get("gitVersion") != VERSION or identity.get("gitCommit") != COMMIT:
        raise RuntimeError("Jaeger executable does not match the pinned release identity")
    subprocess.run([str(binary), "validate", f"--config={config_path.resolve()}"],
                   capture_output=True, check=True, timeout=15)
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="testlookup-jaeger-proof-") as temporary:
        root = Path(temporary)
        config = local_config(source, root / "persistent")
        process, log = _start(binary, config, root)
        try:
            _write(config)
            # The acknowledgement precedes forced loss; there is no graceful
            # shutdown flush and no replay by the test producer.
            _stop(process, crash=True)
        finally:
            _stop(process)
            log.close()
        process, log = _start(binary, config, root)
        try:
            if not _contains_trace(config):
                raise RuntimeError("acknowledged synthetic trace was lost after forced restart")
            services_status, services_body = _request(_url(config, "query", "/api/v3/services"))
            if services_status != 200 or _SERVICE not in json.loads(services_body).get("services", []):
                raise RuntimeError("Jaeger native service discovery did not retain the synthetic service")
            status, body = _request(_url(config, "query", f"/api/v3/traces/{_TRACE_ID}"))
            if status != 200 or not json.loads(body).get("result", {}).get("resourceSpans"):
                raise RuntimeError("Jaeger v3 trace-ID query failed")
            now = datetime.now(timezone.utc)
            query = urllib.parse.urlencode({
                "query.service_name": _SERVICE,
                "query.start_time_min": (now - timedelta(minutes=1)).isoformat(),
                "query.start_time_max": (now + timedelta(minutes=1)).isoformat(),
                "query.search_depth": 20,
            })
            status, body = _request(_url(config, "query", f"/api/v3/trace-summaries?{query}"))
            if status != 200 or not json.loads(body).get("summaries"):
                raise RuntimeError("Jaeger native trace search did not find the retained trace")
            # Grafana Jaeger 13.0.8 still routes Search to the removed legacy
            # endpoint even with jaegerEnableGrpcEndpoint. Record that boundary
            # explicitly; native UI discovery/search and trace-ID links work.
            legacy_search_status, _ = _request(_url(config, "query", f"/api/traces?service={_SERVICE}"))
            _write(config, "3" * 32, "4" * 16)
            data_bytes = sum(path.stat().st_size for path in (root / "persistent").rglob("*") if path.is_file())
        finally:
            _stop(process)
            log.close()

        # A short TTL is a separate disposable store. Physical reclamation
        # remains asynchronous; this proves read expiry, not a disk quota.
        config = local_config(source, root / "expiry", ttl="3s")
        process, log = _start(binary, config, root)
        try:
            _write(config)
            _wait_for(lambda: not _contains_trace(config), seconds=10, description="trace TTL expiry")
        finally:
            _stop(process)
            log.close()
    return {
        "version": VERSION, "forced_restart_retained_trace": True,
        "continued_ingestion": True, "native_service_discovery_and_search": True,
        "legacy_and_v3_trace_id_readback": True,
        "grafana_legacy_search_supported": legacy_search_status == 200,
        "short_ttl_read_expiry": True, "logical_store_bytes_before_cleanup": data_bytes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jaeger-binary", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    print(json.dumps(prove(args.jaeger_binary, config_path=args.config), sort_keys=True))


if __name__ == "__main__":
    main()
