"""
Tool: Fetch application metrics from Prometheus/Grafana during test execution window.
Correlates infrastructure metrics (CPU, memory, latency, error rate) with test failures.
"""
import logging
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from app.core.config import settings
from app.core.http_client import get_http_client

logger = logging.getLogger("tools.fetch_app_metrics")

_PROMETHEUS_URL = getattr(settings, "PROMETHEUS_URL", None)
_MAX_WINDOW_MINUTES = 24 * 60
_MAX_CUSTOM_METRICS = 8
_MAX_METRIC_LENGTH = 2_000


def _promql_label_value(value: str) -> str:
    """Escape a string for a PromQL label matcher."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


async def _query_prometheus(metric: str, start: float, end: float, step: str = "30s") -> list[dict]:
    if not _PROMETHEUS_URL:
        return []
    try:
        client = get_http_client()
        resp = await client.get(
            f"{_PROMETHEUS_URL}/api/v1/query_range",
            params={
                "query": metric,
                "start": start,
                "end": end,
                "step": step,
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("status") != "success":
            return []
        results = []
        for series in data.get("data", {}).get("result", []):
            metric_name = metric
            labels = series.get("metric", {})
            values = series.get("values", [])
            if values:
                avg_val = sum(float(v[1]) for v in values) / len(values)
                max_val = max(float(v[1]) for v in values)
                results.append({
                    "metric": metric_name,
                    "labels": labels,
                    "avg": round(avg_val, 4),
                    "max": round(max_val, 4),
                    "sample_count": len(values),
                })
        return results
    except Exception as exc:
        logger.debug("Prometheus query failed: %s", exc)
        return []


@tool
async def fetch_app_metrics(params_json: str) -> str:
    """
    Fetch infrastructure and application metrics during test execution window.

    Input JSON keys:
      - service_name: name of the service under test
      - timestamp_utc: ISO timestamp of test failure
      - window_minutes: time window (default 15)
      - metrics: list of Prometheus metric expressions (optional, uses defaults if omitted)

    Returns: JSON with metric_series, anomalies detected, and infrastructure_summary.
    """
    import json

    try:
        params = json.loads(params_json)
    except (json.JSONDecodeError, AttributeError) as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    if not isinstance(params, dict):
        return json.dumps({"error": "Input JSON must be an object"})

    service_raw = params.get("service_name", "")
    timestamp_raw = params.get("timestamp_utc", "")
    window_raw = params.get("window_minutes", 15)
    metrics_raw = params.get("metrics", [])

    if not isinstance(service_raw, str):
        return json.dumps({"error": "service_name must be a string"})
    if not isinstance(timestamp_raw, str):
        return json.dumps({"error": "timestamp_utc must be a string"})
    if isinstance(window_raw, bool):
        return json.dumps({"error": "window_minutes must be an integer"})
    try:
        window_min = int(window_raw)
    except (TypeError, ValueError):
        return json.dumps({"error": "window_minutes must be an integer"})
    if not 1 <= window_min <= _MAX_WINDOW_MINUTES:
        return json.dumps({
            "error": f"window_minutes must be between 1 and {_MAX_WINDOW_MINUTES}",
        })
    if not isinstance(metrics_raw, list):
        return json.dumps({"error": "metrics must be a list of PromQL strings"})
    if len(metrics_raw) > _MAX_CUSTOM_METRICS:
        return json.dumps({"error": f"metrics must contain at most {_MAX_CUSTOM_METRICS} expressions"})

    custom_metrics: list[str] = []
    for metric in metrics_raw:
        if not isinstance(metric, str) or not metric.strip():
            return json.dumps({"error": "metrics must contain non-empty PromQL strings"})
        if len(metric) > _MAX_METRIC_LENGTH:
            return json.dumps({"error": f"each metric must be at most {_MAX_METRIC_LENGTH} characters"})
        custom_metrics.append(metric.strip())

    service = service_raw.strip()
    timestamp_str = timestamp_raw.strip()

    if not _PROMETHEUS_URL:
        return json.dumps({
            "metric_series": [],
            "anomalies": [],
            "infrastructure_summary": (
                "Prometheus integration not configured (set PROMETHEUS_URL). "
                "Cannot retrieve application metrics."
            ),
        })

    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)

    start_ts = (ts - timedelta(minutes=window_min)).timestamp()
    end_ts = (ts + timedelta(minutes=2)).timestamp()

    # Default metrics to check if none specified
    service_label = _promql_label_value(service)
    default_metrics = [
        f'rate(http_server_requests_seconds_count{{service="{service_label}",status=~"5.."}}[1m])',
        f'container_memory_working_set_bytes{{container="{service_label}"}}',
        f'rate(container_cpu_usage_seconds_total{{container="{service_label}"}}[1m])',
        f'histogram_quantile(0.99, rate(http_server_requests_seconds_bucket{{service="{service_label}"}}[1m]))',
    ]
    metrics_to_query = custom_metrics if custom_metrics else default_metrics

    all_series = []
    for metric_expr in metrics_to_query:
        series = await _query_prometheus(metric_expr, start_ts, end_ts)
        all_series.extend(series)

    # Detect anomalies: high error rate, memory pressure, CPU spike, latency spike
    anomalies = []
    for s in all_series:
        if "5.." in s["metric"] and s["avg"] > 0.1:
            anomalies.append(f"High 5xx error rate: avg {s['avg']:.3f} req/s during test window")
        if "memory" in s["metric"] and s["max"] > 1_073_741_824:  # >1GB
            anomalies.append(f"High memory usage: {s['max'] / 1_048_576:.0f} MB peak")
        if "cpu" in s["metric"] and s["avg"] > 0.8:
            anomalies.append(f"CPU saturation: avg {s['avg']*100:.1f}% usage")
        if "quantile" in s["metric"] and s["max"] > 5.0:
            anomalies.append(f"Latency spike: P99 reached {s['max']:.2f}s")

    if anomalies:
        infrastructure_summary = (
            f"INFRASTRUCTURE ISSUES DETECTED during test window for service '{service}': "
            + "; ".join(anomalies)
        )
    elif all_series:
        infrastructure_summary = f"No infrastructure anomalies detected for service '{service}' during test window."
    else:
        infrastructure_summary = f"No metrics data available for service '{service}'."

    return json.dumps({
        "service": service,
        "metric_series": all_series,
        "anomalies": anomalies,
        "infrastructure_summary": infrastructure_summary,
        "time_window": {
            "start": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
        },
    })
