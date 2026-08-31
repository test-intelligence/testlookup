"""Behavior tests for the Prometheus investigation tool."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.tools import fetch_app_metrics as metrics_tool


async def _invoke(payload) -> dict:
    raw = await metrics_tool.fetch_app_metrics.ainvoke({"params_json": json.dumps(payload)})
    return json.loads(raw)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "must be an object"),
        ({"window_minutes": "wide"}, "window_minutes must be an integer"),
        ({"window_minutes": 0}, "window_minutes must be between"),
        ({"timestamp_utc": 123}, "timestamp_utc must be a string"),
        ({"metrics": "up"}, "metrics must be a list"),
    ],
)
async def test_model_generated_invalid_input_returns_a_tool_error(payload, message):
    result = await _invoke(payload)

    assert message in result["error"]


async def test_default_queries_escape_service_label_and_treat_naive_timestamp_as_utc(monkeypatch):
    calls: list[tuple[str, float, float]] = []

    async def fake_query(metric: str, start: float, end: float, step: str = "30s"):
        calls.append((metric, start, end))
        return []

    monkeypatch.setattr(metrics_tool, "_PROMETHEUS_URL", "https://prometheus.invalid")
    monkeypatch.setattr(metrics_tool, "_query_prometheus", fake_query)

    result = await _invoke({
        "service_name": 'api"\n\\prod',
        "timestamp_utc": "2026-08-31T12:00:00",
        "window_minutes": 15,
    })

    assert len(calls) == 4
    expected_start = datetime(2026, 8, 31, 11, 45, tzinfo=timezone.utc).timestamp()
    expected_end = datetime(2026, 8, 31, 12, 2, tzinfo=timezone.utc).timestamp()
    assert {(start, end) for _, start, end in calls} == {(expected_start, expected_end)}
    assert all('api\\"\\n\\\\prod' in metric for metric, _, _ in calls)
    assert result["time_window"]["start"] == "2026-08-31T11:45:00+00:00"
    assert result["time_window"]["end"] == "2026-08-31T12:02:00+00:00"


async def test_custom_metric_count_is_bounded_before_prometheus_call(monkeypatch):
    called = False

    async def fake_query(*_args, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(metrics_tool, "_PROMETHEUS_URL", "https://prometheus.invalid")
    monkeypatch.setattr(metrics_tool, "_query_prometheus", fake_query)

    result = await _invoke({"metrics": ["up"] * (metrics_tool._MAX_CUSTOM_METRICS + 1)})

    assert "at most" in result["error"]
    assert called is False
