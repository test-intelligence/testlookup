#!/usr/bin/env python3
"""Measure a disposable loopback OTLP, Loki, Jaeger and Prometheus profile."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import re
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

_ROLES = ("api", "api", "api", "api", "api", "api", "worker", "worker", "worker-children", "mcp")
_METRIC = re.compile(r'^([^ {]+)(?:\{.*\})?\s+(\S+)(?:\s+\d+)?$')
_SPAN_NAMES = ("benchmark.request", "benchmark.sql", "benchmark.redis", "benchmark.storage")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    @classmethod
    def parse(cls, address: str) -> Endpoint:
        url = urlsplit(address)
        if (
            url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
            or url.username is not None or url.path not in {"", "/"}
            or url.query or url.fragment or not url.port
        ):
            raise ValueError("use a plain HTTP loopback origin with an explicit disposable port")
        return cls(url.hostname, url.port)

    def call(self, path: str, body: bytes | None = None) -> bytes:
        # HTTPConnection uses the supplied address directly and never follows
        # a redirect or sends these synthetic records through an ambient proxy.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=4)
        try:
            connection.request("GET" if body is None else "POST", path, body=body,
                               headers={"Content-Type": "application/json"} if body is not None else {})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"{path.split('?')[0]} returned HTTP {response.status}")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError("benchmark response exceeded its size limit")
            return payload
        finally:
            connection.close()


@dataclass(frozen=True)
class Sample:
    trace_id: str
    request_id: str
    role: str
    has_trace: bool
    is_error: bool


def workload(seed: str, records: int, fraction: float) -> list[Sample]:
    """Deterministic whole-trace sampling for a reproducible capacity input."""
    traced = round(records * fraction)
    samples = []
    for index in range(records):
        identifier = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()
        sampled = (index + 1) * traced // records != index * traced // records
        samples.append(Sample(identifier[:32], identifier, _ROLES[index % len(_ROLES)], sampled, index % 20 == 0))
    return samples


def attribute(key: str, value: str | int) -> dict:
    return {"key": key, "value": {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value}}


def resource(role: str) -> dict:
    return {"attributes": [attribute(key, value) for key, value in {
        "service.name": "testlookup", "service.namespace": "testlookup",
        "deployment.environment.name": "test", "testlookup.service.role": role,
    }.items()]}


def _span_id(trace_id: str, offset: int = 0) -> str:
    return f"{(int(trace_id[:16], 16) ^ offset) or 1:016x}"


def _encode(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def packets(samples: list[Sample], now: int) -> list[tuple[str, bytes]]:
    logs: dict[str, list] = {}
    spans = []
    for sample in samples:
        status = 503 if sample.is_error else 200
        event = {"api": "http_request", "mcp": "mcp_request"}.get(sample.role, "celery_task_execution")
        logs.setdefault(sample.role, []).append({
            "timeUnixNano": str(now), "traceId": sample.trace_id, "spanId": _span_id(sample.trace_id),
            "eventName": event, "severityText": "WARN" if status >= 500 else "INFO",
            "severityNumber": 13 if status >= 500 else 9,
            "body": {"stringValue": "synthetic capacity fixture"},
            "attributes": [attribute("request_id", sample.request_id), attribute("status_code", status),
                           attribute("duration_ms", 5), attribute("state", "failure" if status >= 500 else "success")],
        })
        if sample.has_trace:
            for offset, name in enumerate(_SPAN_NAMES):
                span = {
                    "traceId": sample.trace_id, "spanId": _span_id(sample.trace_id, offset),
                    "name": name, "kind": 1, "startTimeUnixNano": str(now),
                    "endTimeUnixNano": str(now + max(1, 5 - offset) * 1_000_000),
                }
                if offset:
                    span["parentSpanId"] = _span_id(sample.trace_id)
                elif sample.is_error:
                    span["status"] = {"code": 2}
                spans.append(span)
    outgoing = [("/v1/logs", _encode({"resourceLogs": [
        {"resource": resource(role), "scopeLogs": [{"scope": {"name": "testlookup.capacity"}, "logRecords": items}]}
        for role, items in logs.items()
    ]}))]
    if spans:
        outgoing.append(("/v1/traces", _encode({"resourceSpans": [{
            "resource": resource("api"), "scopeSpans": [{"scope": {"name": "testlookup.capacity"}, "spans": spans}],
        }]})))
    return outgoing


def metrics(endpoint: Endpoint) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for line in endpoint.call("/metrics").decode().splitlines():
        if not line or line.startswith("#"):
            continue
        sample = _METRIC.fullmatch(line)
        if sample:
            values.setdefault(sample[1], []).append(float(sample[2]))
    return values


def series_sum(values: dict, *names: str) -> float | None:
    for name in names:
        if name in values:
            return sum(value for value in values[name] if math.isfinite(value))
    return None


def snapshot(endpoint: Endpoint) -> dict:
    values = metrics(endpoint)
    return {
        "exposed_series": sum(len(family) for family in values.values()),
        "cpu_seconds": series_sum(values, "otelcol_process_cpu_seconds", "otelcol_process_cpu_seconds_total",
                                  "process_cpu_seconds_total"),
        "rss_bytes": series_sum(values, "otelcol_process_memory_rss", "process_resident_memory_bytes"),
        "queue_bytes": series_sum(values, "otelcol_exporter_queue_size"),
        "queue_capacity_bytes": series_sum(values, "otelcol_exporter_queue_capacity"),
        "sent_logs": series_sum(values, "otelcol_exporter_sent_log_records", "otelcol_exporter_sent_log_records_total"),
        "sent_spans": series_sum(values, "otelcol_exporter_sent_spans", "otelcol_exporter_sent_spans_total"),
    }


def logical_bytes(path: Path | None) -> int | None:
    if path is None:
        return None
    if not path.is_dir():
        raise ValueError("each supplied storage path must be a directory")
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            try:
                total += item.stat().st_size
            except FileNotFoundError:
                continue  # Concurrent compaction replaced a file.
    return total


def latency(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {"count": len(values), "p50_ms": round(ordered[math.ceil(len(ordered) * .5) - 1], 2),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 2), "max_ms": round(ordered[-1], 2)}


def _send(endpoint: Endpoint, samples: list[Sample]) -> tuple[list[float], int]:
    times, size = [], 0
    for path, body in packets(samples, time.time_ns()):
        start = time.perf_counter()
        response = json.loads(endpoint.call(path, body) or b"{}")
        times.append((time.perf_counter() - start) * 1000)
        rejected = response.get("partialSuccess", {})
        if int(rejected.get("rejectedLogRecords", 0)) or int(rejected.get("rejectedSpans", 0)):
            raise RuntimeError("collector returned partial success")
        size += len(body)
    return times, size


def send_workload(endpoint: Endpoint, scrape: Endpoint, samples: list[Sample], batch_size: int, workers: int
                  ) -> tuple[dict, int, float, float, float]:
    batches = (samples[start:start + batch_size] for start in range(0, len(samples), batch_size))
    delays, size, peak_queue, peak_rss = [], 0, 0, 0
    started = next_poll = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        running: set[Future[tuple[list[float], int]]] = set()
        for _ in range(workers):
            batch = next(batches, None)
            if batch is not None:
                running.add(executor.submit(_send, endpoint, batch))
        while running:
            complete, running = wait(running, timeout=.15, return_when=FIRST_COMPLETED)
            if time.perf_counter() >= next_poll:
                observed = snapshot(scrape)
                peak_queue = max(peak_queue, observed["queue_bytes"] or 0)
                peak_rss = max(peak_rss, observed["rss_bytes"] or 0)
                next_poll = time.perf_counter() + .15
            for finished in complete:
                durations, transferred = finished.result()
                delays.extend(durations)
                size += transferred
                batch = next(batches, None)
                if batch is not None:
                    running.add(executor.submit(_send, endpoint, batch))
    return latency(delays), size, time.perf_counter() - started, peak_queue, peak_rss


def _wait_for_delivery(scrape: Endpoint, before: dict, logs: int, spans: int) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        after = snapshot(scrape)
        log_delta = (after["sent_logs"] or 0) - (before["sent_logs"] or 0)
        span_delta = (after["sent_spans"] or 0) - (before["sent_spans"] or 0)
        if log_delta >= logs and span_delta >= spans:
            if (log_delta, span_delta) != (logs, spans):
                raise RuntimeError("delivery counts indicate duplicate or concurrent traffic")
            return after
        time.sleep(.2)
    raise RuntimeError("collector did not deliver all synthetic data within 45 seconds")


def _readback(loki: Endpoint, jaeger: Endpoint, samples: list[Sample], count: int) -> dict:
    log_times, trace_times = [], []
    for index in range(count):
        sample = samples[index * len(samples) // count]
        query = '{service_name="testlookup",service_namespace="testlookup"} | request_id="' + sample.request_id + '"'
        start = time.perf_counter()
        answer = json.loads(loki.call("/loki/api/v1/query_range?" + urlencode({
            "query": query, "limit": 2, "start": str(time.time_ns() - 120_000_000_000),
        })))
        results = answer.get("data", {}).get("result", [])
        if sum(len(stream.get("values", [])) for stream in results) != 1:
            raise RuntimeError("Loki did not return the synthetic request")
        log_times.append((time.perf_counter() - start) * 1000)
    traced = [sample for sample in samples if sample.has_trace]
    for index in range(min(len(traced), count)):
        sample = traced[index * len(traced) // min(len(traced), count)]
        start = time.perf_counter()
        traces = json.loads(jaeger.call(f"/api/traces/{sample.trace_id}")).get("data") or []
        if not any(item.get("traceID") == sample.trace_id and len(item.get("spans") or []) == len(_SPAN_NAMES) for item in traces):
            raise RuntimeError("Jaeger did not return the complete synthetic trace")
        trace_times.append((time.perf_counter() - start) * 1000)
    return {"loki": latency(log_times), "jaeger": latency(trace_times)}


def measure(args: argparse.Namespace) -> dict:
    # Warm query stores and collector before the timed interval.
    warmup = workload(uuid.uuid4().hex, 16, 1)
    count = len(warmup) * len(_SPAN_NAMES)
    starting = snapshot(args.collector_metrics)
    _send(args.collector, warmup)
    before = _wait_for_delivery(args.collector_metrics, starting, len(warmup), count)
    stores = {"collector": (args.collector_metrics, args.collector_data),
              "loki": (args.loki, args.loki_data), "jaeger": (args.jaeger_metrics, args.jaeger_data)}
    if args.prometheus is not None:
        stores["prometheus"] = (args.prometheus, args.prometheus_data)
    initial = {name: {**snapshot(endpoint), "logical_disk_bytes": logical_bytes(path)}
               for name, (endpoint, path) in stores.items()}

    samples = workload(uuid.uuid4().hex, args.records, args.trace_fraction)
    spans = sum(sample.has_trace for sample in samples) * len(_SPAN_NAMES)
    started = time.perf_counter()
    rtt, size, elapsed, queue, peak_rss = send_workload(
        args.collector, args.collector_metrics, samples, args.batch_size, args.workers,
    )
    _wait_for_delivery(args.collector_metrics, before, len(samples), spans)
    delivered = time.perf_counter() - started
    queried = _readback(args.loki, args.jaeger, samples, args.query_samples)
    final = {name: {**snapshot(endpoint), "logical_disk_bytes": logical_bytes(path)}
             for name, (endpoint, path) in stores.items()}
    head = None
    if args.prometheus is not None:
        head = json.loads(args.prometheus.call("/api/v1/status/tsdb"))["data"]["headStats"]["numSeries"]
    return {
        "input": {"records": len(samples), "spans": spans, "batch_size": args.batch_size, "workers": args.workers,
                  "trace_fraction": args.trace_fraction, "synthetic_otlp_bytes": size},
        "ingress_seconds": round(elapsed, 3), "logs_per_ingress_second": round(len(samples) / elapsed, 2),
        "delivery_seconds": round(delivered, 3), "logs_per_delivery_second": round(len(samples) / delivered, 2),
        "collector_http": rtt, "collector_peak_queue_bytes_sampled": queue,
        "collector_peak_rss_bytes_sampled": peak_rss, "queries": queried, "prometheus_head_series": head,
        "before": initial, "after": final,
    }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("collector", "collector-metrics", "loki", "jaeger", "jaeger-metrics"):
        parser.add_argument("--" + name, type=Endpoint.parse, required=True)
    parser.add_argument("--prometheus", type=Endpoint.parse)
    for name in ("collector", "loki", "jaeger", "prometheus"):
        parser.add_argument("--" + name + "-data", type=Path)
    parser.add_argument("--records", type=int, default=4096)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--trace-fraction", type=float, default=1.0)
    parser.add_argument("--query-samples", type=int, default=12)
    args = parser.parse_args()
    if not (32 <= args.records <= 8192 and 8 <= args.batch_size <= 128 and 1 <= args.workers <= 4
            and 0 <= args.trace_fraction <= 1 and 1 <= args.query_samples <= 24):
        parser.error("workload exceeds the disposable profile's record, concurrency or query bounds")
    return args


if __name__ == "__main__":
    print(json.dumps(measure(arguments()), indent=2, allow_nan=False))
