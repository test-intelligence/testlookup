"""Behaviour tests for the Prometheus investigation tool (W23d AGT-15 rewrite, contract §2.2 M-3).

The old tests patched the module-level ``_PROMETHEUS_URL`` and ``_query_prometheus``, both DELETED: the tool now
takes template ids only, resolves its config per call (the bound runtime config, else the instance block), and
answers typed outcomes. Every refusal is observed with a transport that fails the test on any request.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import settings
from app.services import app_metrics_service as ams
from app.services import prometheus_vocab as pv
from app.services import url_safety
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.tools import fetch_app_metrics as metrics_tool
from app.tools.investigation_context import bind_runtime_integrations, reset_runtime_integrations

BASE = "http://prometheus.metrics.test:9090"


def _cfg(**overrides) -> IntegrationRuntimeConfig:
    values = dict(
        project_id=None, splunk_enabled=False, splunk_base_url=None, splunk_index="", splunk_index_source="environment",
        ocp_enabled=False, ocp_api_url=None, ocp_default_namespace=None, ocp_namespace_source="environment",
        prometheus_enabled=True, prometheus_base_url=BASE,
        prometheus_templates=tuple(dict(t) for t in pv.DEFAULT_METRIC_TEMPLATES), prometheus_bearer_token=None,
    )
    values.update(overrides)
    return IntegrationRuntimeConfig(**values)


@pytest.fixture
def world(monkeypatch):
    seen: list[httpx.Request] = []
    script: dict = {"handler": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if script["handler"] is None:
            pytest.fail(f"unexpected Prometheus request: {request.url.path}")
        return script["handler"](request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ams, "get_http_client", lambda: client)

    def getaddrinfo(host, *_a, **_k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    token = bind_runtime_integrations(_cfg())
    yield type("World", (), {"seen": seen, "script": script})
    reset_runtime_integrations(token)


async def _invoke(payload) -> dict:
    raw = await metrics_tool.fetch_app_metrics.ainvoke({"params_json": json.dumps(payload)})
    return json.loads(raw)


def _ok(request: httpx.Request) -> httpx.Response:
    query = parse_qs(urlsplit(str(request.url)).query)
    start, end = float(query["start"][0]), float(query["end"][0])
    values = [[start + 60 * k, "0.62" if start + 60 * k >= end - 120 - 900 else "0.20"]
              for k in range(int((end - start) // 60) + 1)]
    return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "values": values}]}})


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([], "invalid_input"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", "window_minutes": "wide"},
         "invalid_input"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", "window_minutes": 0}, "invalid_input"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", "window_minutes": 1441},
         "invalid_input"),
        ({"service_name": "checkout", "timestamp_utc": 123}, "invalid_timestamp"),
        ({"service_name": "checkout", "timestamp_utc": "yesterday"}, "invalid_timestamp"),
        ({"service_name": "checkout"}, "invalid_timestamp"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", "template_ids": "http_p95_latency"},
         "invalid_input"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z",
          "template_ids": ["a1", "a2", "a3", "a4", "a5"]}, "too_many_templates"),
        ({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", "template_ids": ["nope_x"]},
         "unknown_template"),
    ],
)
async def test_invalid_input_is_a_tool_error_with_no_request(world, payload, code):
    result = await _invoke(payload)
    assert result["error"] == code
    assert result["message"]
    assert world.seen == []


@pytest.mark.parametrize("key", list(metrics_tool.RAW_PROMQL_KEYS))
async def test_raw_promql_is_refused_with_no_request(world, key):
    world.script["handler"] = _ok
    result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z", key: "up"})
    assert result == {"error": "raw_promql_refused", "message": pv.TOOL_ERRORS["raw_promql_refused"]}
    assert world.seen == []


async def test_the_old_custom_metrics_payload_is_refused_before_any_call(world):
    # The exact payload the pre-W23d test sent (nine custom metrics): no longer bounded -- refused outright.
    world.script["handler"] = _ok
    result = await _invoke({"metrics": ["up"] * 9})
    assert result["error"] == "raw_promql_refused"
    assert world.seen == []


async def test_offline_mode_is_refused_before_the_config_or_any_request(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    async def no_resolve(*_a, **_k):
        pytest.fail("offline mode must not resolve the configuration")

    monkeypatch.setattr(metrics_tool, "_runtime_config", no_resolve)
    result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z"})
    assert (result["outcome"], result["sentence"]) == ("offline_mode", "Prometheus lookup disabled: offline mode")
    assert result["series"] == [] and result["anomalies"] == []
    assert world.seen == []


async def test_naive_timestamp_is_utc_and_the_window_is_reported(world):
    world.script["handler"] = _ok
    result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00",
                            "window_minutes": 15, "template_ids": ["http_p95_latency"]})
    assert result["time_window"] == {"start": "2026-08-31T11:30:00+00:00", "end": "2026-08-31T12:02:00+00:00"}
    query = parse_qs(urlsplit(str(world.seen[0].url)).query)
    assert float(query["start"][0]) == datetime(2026, 8, 31, 11, 30, tzinfo=timezone.utc).timestamp()
    assert result["outcome"] == "ok"
    assert result["anomalies"] == ["p95 latency for checkout rose 3.1× in the failure window"]
    assert result["infrastructure_summary"] == "p95 latency for checkout rose 3.1× in the failure window"
    assert len(result["series"][0]["points"]) <= pv.POINTS_MAX


async def test_omitted_template_ids_query_the_first_four_configured(world):
    world.script["handler"] = _ok
    result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z"})
    assert [item["template_id"] for item in result["series"]] == [t["id"] for t in pv.DEFAULT_METRIC_TEMPLATES[:4]]
    assert len(world.seen) == 4


async def test_an_unconfigured_block_is_not_configured_never_no_anomalies(world):
    token = bind_runtime_integrations(_cfg(prometheus_base_url=None))
    try:
        result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z"})
    finally:
        reset_runtime_integrations(token)
    assert (result["outcome"], result["infrastructure_summary"]) == (
        "not_configured", "metrics unavailable: Prometheus is not configured")
    assert world.seen == []


async def test_an_unreadable_config_is_config_unavailable(world, monkeypatch):
    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(metrics_tool, "_runtime_config", none)
    result = await _invoke({"service_name": "checkout", "timestamp_utc": "2026-08-31T12:00:00Z"})
    assert result["outcome"] == "config_unavailable"
    assert world.seen == []
