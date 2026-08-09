"""Alert rules must fire on metrics that actually exist.

`infra/monitoring/prometheus-rules/testlookup-alerts.yml` defines::

    - alert: TestLookupCeleryQueueBacklog
      expr: sum by (queue_name) (celery_queue_length) > 100
      description: "Queue {{ $labels.queue_name }} has more than 100 pending
                    tasks. Workers may need scaling."

`celery_queue_length` was emitted by nothing. Verified against the live
deployment's `/metrics` endpoint — **0 samples**. The alert could never fire, so
the exact condition it exists to catch (a backlog caused by under-scaled or
throttled workers, which is what the 2026-08-08 worker CPU fix was about) would
have passed unnoticed.

Two design points worth keeping:

* The gauge is exposed by the **backend**, because that is the process Prometheus
  scrapes. A gauge set inside a Celery worker never reaches the scrape.
* It is refreshed **at scrape time**, not on a timer. A gauge refreshed by a beat
  task keeps reporting its last value when the beat pod is itself the unhealthy
  thing — reporting a stale "queue is fine" during exactly the incident it should
  be flagging.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("app.core.metrics")

from app import bootstrap  # noqa: E402
from app.core import metrics  # noqa: E402

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERTS = REPO_ROOT / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"


class TestTheMetricExists:
    def test_gauge_is_defined_with_the_alerted_name(self):
        """The name must match the alert expression exactly — a near-miss
        (testlookup_celery_queue_length) would leave the alert just as dead."""
        assert metrics.celery_queue_length._name == "celery_queue_length"

    def test_it_is_labelled_by_queue_name(self):
        """The alert does `sum by (queue_name)`; without that label the
        expression returns nothing."""
        assert "queue_name" in metrics.celery_queue_length._labelnames

    def test_the_alert_still_references_this_name(self):
        """If someone renames the metric, this points at the alert to update."""
        if not ALERTS.exists():
            pytest.skip("alert rules not present")
        assert "celery_queue_length" in ALERTS.read_text(encoding="utf-8")


class TestItIsCollectedAtScrapeTime:
    def test_metrics_endpoint_refreshes_depths(self):
        src = __import__("inspect").getsource(bootstrap)
        assert "_install_celery_queue_depth_collector" in src
        assert "_refresh_celery_queue_depths" in src

    def test_refresh_reads_every_queue_including_shards(self, monkeypatch):
        seen: list[str] = []

        class FakeRedis:
            async def llen(self, name):
                seen.append(name)
                return 7

        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: FakeRedis(), raising=False)
        asyncio.run(bootstrap._refresh_celery_queue_depths())

        assert "critical" in seen and "ai_analysis" in seen and "default" in seen
        shards = [q for q in seen if q.startswith("ingestion.shard.")]
        assert len(shards) >= 8, (
            f"only {len(shards)} shard queues sampled — a shard nobody measures "
            f"is a shard that can back up invisibly"
        )
        assert metrics.celery_queue_length.labels(queue_name="critical")._value.get() == 7.0

    def test_a_broken_redis_never_breaks_the_scrape(self, monkeypatch):
        """Metrics collection must not take down /metrics — losing the scrape
        during an incident is worse than losing one gauge."""
        class Exploding:
            async def llen(self, name):
                raise RuntimeError("redis is down")

        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: Exploding(), raising=False)
        asyncio.run(bootstrap._refresh_celery_queue_depths())  # must not raise

    def test_one_bad_queue_does_not_hide_the_others(self, monkeypatch):
        class Picky:
            async def llen(self, name):
                if name == "critical":
                    raise RuntimeError("nope")
                return 3

        monkeypatch.setattr("app.db.redis_client.get_redis", lambda: Picky(), raising=False)
        asyncio.run(bootstrap._refresh_celery_queue_depths())
        assert metrics.celery_queue_length.labels(queue_name="default")._value.get() == 3.0


def test_no_alert_rule_fires_on_a_metric_nothing_defines():
    """Generalises the bug: every app-level metric an alert references must be
    emitted somewhere.

    Excludes metric families emitted by prometheus-fastapi-instrumentator
    (`http_*`) — those are real at runtime but never appear as `Counter(...)` in
    this repo, which is what made a purely static check misjudge them. That was
    confirmed by scraping the live endpoint, not by reading code.
    """
    if not ALERTS.exists():
        pytest.skip("alert rules not present")
    rules = ALERTS.read_text(encoding="utf-8")

    emitted: set[str] = set()
    for path in (REPO_ROOT / "backend" / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(
            r'(?:Counter|Gauge|Histogram|Summary)\(\s*["\']([a-zA-Z_:][a-zA-Z0-9_:]*)["\']', text
        ):
            name = m.group(1)
            emitted |= {name, f"{name}_total", f"{name}_sum", f"{name}_count", f"{name}_bucket"}

    # Empty again: celery_task_runtime_seconds was the last known-inert entry
    # and is now emitted by the workers' own scrape target. Kept as a mechanism
    # so a future gap can be recorded visibly instead of silently tolerated.
    KNOWN_INERT: set[str] = set()

    referenced = set(re.findall(r"\b(celery_[a-z0-9_]+)\b", rules))
    ghosts = sorted(r for r in referenced if r not in emitted and r not in KNOWN_INERT)
    assert not ghosts, (
        f"alert rules reference Celery metrics nothing emits: {ghosts}. "
        f"An alert on a non-existent metric never fires — it reads as healthy "
        f"forever."
    )
    # The queue-depth metric specifically must NOT be inert — that is the point
    # of this change, so it can never quietly join the known-inert list.
    assert "celery_queue_length" in emitted
