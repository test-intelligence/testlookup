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

import ast
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


def test_no_alert_rule_is_structurally_unable_to_fire():
    """Every metric an alert references must have something that EMITS it.

    This test used to state that invariant and not enforce it, in two ways
    that each made it silently inert:

    1. It built its "emitted" set from ``Counter(...)`` / ``Gauge(...)``
       *declarations*. A metric declared in ``app/core/metrics.py`` and never
       incremented anywhere counted as emitted. Declaration is not emission —
       an unlabelled counter that nothing touches still appears in ``/metrics``
       as ``<name> 0.0``, which reads to a human as "this never happened"
       rather than "nothing can ever record this".
    2. It matched referenced names with ``celery_[a-z0-9_]+``. ``_`` is a
       word character, so that pattern never matches ``testlookup_celery_...``
       — it examined exactly two names, the two the author had just fixed,
       and never saw the seven ``testlookup_*`` names in the same file.

    Result: four alerts could not fire while this test was green —
    TestLookupAIPipelineFailures, TestLookupLLMCircuitBreakerOpen,
    TestLookupFlakyQuarantineMaintenanceFailing and
    TestLookupPerfBaselineRefreshFailing.

    Scope: only metrics this repo declares are judged. ``http_*`` comes from
    prometheus-fastapi-instrumentator and ``up`` / ``node_*`` from other
    exporters; they are real at runtime but never appear as ``Counter(...)``
    here, and a static check that failed them would be wrong.
    """
    if not ALERTS.exists():
        pytest.skip("alert rules not present")

    app_dir = REPO_ROOT / "backend" / "app"
    metrics_py = app_dir / "core" / "metrics.py"

    # prometheus name -> python variable, straight from the declarations.
    declared: dict[str, str] = {}
    tree = ast.parse(metrics_py.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        cls = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if cls not in {"Counter", "Gauge", "Histogram", "Summary", "Info"}:
            continue
        if not node.value.args or not isinstance(node.value.args[0], ast.Constant):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                declared[node.value.args[0].value] = target.id

    # A variable referenced anywhere outside metrics.py is one something can
    # record through. Deliberately generous: the point is to catch names with
    # NO code path at all, not to police how they are used.
    emitters: set[str] = set()
    for path in app_dir.rglob("*.py"):
        if path == metrics_py or "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        emitters |= {var for var in declared.values() if var in text}

    rules = ALERTS.read_text(encoding="utf-8")
    referenced = set(re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", rules))

    ghosts = sorted(
        name for name in referenced
        if (base := re.sub(r"_(bucket|count|sum)$", "", name)) in declared
        and declared[base] not in emitters
    )
    assert not ghosts, (
        f"alert rules reference metrics that nothing emits: {ghosts}. "
        f"The alert never fires, so the condition it exists to catch reads as "
        f"healthy forever."
    )

    # The two metrics whose absence prompted this test must never quietly
    # rejoin the ghost list.
    assert declared["celery_queue_length"] in emitters
    assert declared["celery_task_runtime_seconds"] in emitters
