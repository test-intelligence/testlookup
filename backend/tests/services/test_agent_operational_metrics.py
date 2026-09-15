"""E2.3 scrape-time metrics and positive alert fire/clear tests."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from app.core import metrics

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[3]
ALERTS = ROOT / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, values):
        self.values = iter(values)

    async def execute(self, _statement):
        return _Scalar(next(self.values))


class _Session:
    def __init__(self, values):
        self.db = _DB(values)

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _Redis:
    def __init__(self, stream_depth: int, list_depth: int):
        self.stream_depth = stream_depth
        self.list_depth = list_depth

    async def xlen(self, _key):
        return self.stream_depth

    async def llen(self, _key):
        return self.list_depth


def _rule(name: str) -> dict:
    doc = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return next(
        rule
        for group in doc["groups"]
        for rule in group["rules"]
        if rule.get("alert") == name
    )


def _fires(rule: dict, value: float) -> bool:
    expression = " ".join(rule["expr"].split())
    match = re.fullmatch(r"[a-z0-9_]+ > ([0-9]+)", expression)
    assert match, f"test must evaluate the real scalar rule, got {expression!r}"
    return value > float(match.group(1))


def _value(gauge) -> float:
    return float(gauge._value.get())


async def _refresh(
    monkeypatch,
    *,
    oldest_run=None,
    oldest_review=None,
    stream_depth=0,
    list_depth=0,
    now,
):
    from app.db import postgres, redis_client
    from app.services.agent_operational_metrics import refresh_agent_operational_metrics

    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _Session([oldest_run, oldest_review]))
    redis = _Redis(stream_depth, list_depth)
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    await refresh_agent_operational_metrics(now=now)


async def test_overdue_pipeline_alert_fires_and_clears(monkeypatch):
    from app.core.config import settings

    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    boundary = settings.AI_PIPELINE_DEADLINE_SECONDS + settings.AGENT_PIPELINE_ALERT_GRACE_SECONDS
    rule = _rule("TestLookupAgentPipelineOverdue")

    await _refresh(monkeypatch, oldest_run=now - timedelta(seconds=boundary + 1), now=now)
    assert _value(metrics.agent_in_progress_overdue_seconds) == 1.0
    assert _fires(rule, _value(metrics.agent_in_progress_overdue_seconds))

    await _refresh(monkeypatch, oldest_run=None, now=now)
    assert not _fires(rule, _value(metrics.agent_in_progress_overdue_seconds))


async def test_dlq_depth_alert_fires_and_clears(monkeypatch):
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    rule = _rule("TestLookupAgentDLQNotEmpty")

    await _refresh(monkeypatch, list_depth=1, now=now)
    assert _fires(rule, _value(metrics.agent_dlq_depth))

    await _refresh(monkeypatch, stream_depth=0, list_depth=0, now=now)
    assert not _fires(rule, _value(metrics.agent_dlq_depth))


async def test_pending_review_age_alert_fires_and_clears(monkeypatch):
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    rule = _rule("TestLookupPendingReviewStale")

    await _refresh(monkeypatch, oldest_review=now - timedelta(seconds=86401), now=now)
    assert _value(metrics.pending_review_oldest_age_seconds) == 86401.0
    assert _fires(rule, _value(metrics.pending_review_oldest_age_seconds))

    await _refresh(monkeypatch, oldest_review=now, now=now)
    assert not _fires(rule, _value(metrics.pending_review_oldest_age_seconds))


async def test_scrape_wrapper_refreshes_agent_metrics(monkeypatch):
    from app import bootstrap
    from app.services import agent_operational_metrics

    queue_refresh = MagicMock()
    agent_refresh = MagicMock()

    async def _queue_refresh():
        queue_refresh()

    async def _agent_refresh():
        agent_refresh()

    monkeypatch.setattr(
        agent_operational_metrics, "refresh_agent_operational_metrics", _agent_refresh
    )
    monkeypatch.setattr(bootstrap, "_refresh_celery_queue_depths", _queue_refresh)

    class _Route:
        path = "/metrics"

        async def endpoint(self):
            return "metrics"

    route = _Route()
    app = MagicMock(routes=[route])
    bootstrap._install_celery_queue_depth_collector(app)

    assert await route.endpoint() == "metrics"
    queue_refresh.assert_called_once_with()
    agent_refresh.assert_called_once_with()
