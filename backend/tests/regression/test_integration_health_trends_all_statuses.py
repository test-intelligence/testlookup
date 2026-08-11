"""Integration-health trends must report every status the probers persist.

``integration_probe_service`` emits **five** statuses that reach the database —
``healthy``, ``degraded``, ``down``, ``timeout`` and ``auth_error`` (``skipped``
is dropped before insert by ``_persist``). ``get_health_trends`` initialised
counters for only **three** of them.

The consequence is not a missing column, it is a misleading one. ``timeout`` and
``auth_error`` probes were still added to ``total_probes``, so they still pushed
``uptime_pct`` down — but they landed in no rendered column. A provider whose
API token had expired therefore rendered as:

    Uptime 0%   Healthy 0   Degraded 0   Down 0

which reads as "this integration was never probed", not "your credentials are
being rejected" — on the one page whose whole job is to tell you which
integration is broken and why.

The same block held a second defect. ``avg_response_ms`` was assigned inside the
per-``(provider, status)`` loop::

    if row.avg_ms:
        trends[provider]["avg_response_ms"] = round(float(row.avg_ms), 0)

so the provider's "Avg Latency" was whichever *status group* the database
happened to return last — a `GROUP BY` has no defined row order — rather than an
average over its probes. The ``if row.avg_ms:`` guard also silently skipped any
group whose average was exactly ``0.0``.

On the live homelab the two defects cancelled: ollama's only non-healthy group is
``down``, which records ``response_ms=0`` as a sentinel, so the falsy guard
skipped it and the healthy group's 47ms survived. Correct by accident, from a
value that would have flipped to the ``down`` group's had the row order or the
sentinel differed.

**Limit stated honestly**: this is *not* reproducible against the homelab, where
every optional integration is disabled and only ``healthy``/``down`` rows exist.
It is proven here from the persisted status vocabulary and the handler's own
aggregation, which is why the first test below reads the prober's source rather
than trusting a hand-copied list of statuses.
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import integration_health  # noqa: E402
from app.services import integration_probe_service  # noqa: E402

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Returns canned results in call order, like the stream-service fakes.

    The handler issues the status-count query first and the latency query
    second; a pre-fix handler issues only the first, which is exactly what makes
    the latency assertion below fail before the fix rather than error out.
    """

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt):
        self.calls += 1
        return self._results.pop(0) if self._results else _Result([])


def test_persisted_status_vocabulary_is_what_we_think_it_is():
    """Read the vocabulary from the prober instead of hard-coding it.

    If a new status is added to a probe function, this fails and forces the
    aggregator to grow a counter for it — the actual class of bug, rather than
    the two instances of it.
    """
    src = inspect.getsource(integration_probe_service)
    emitted = set(re.findall(r'ProbeResult\(\s*"[a-z_]+"\s*,\s*"([a-z_]+)"', src))
    persisted = emitted - {"skipped"}  # _persist drops these before insert
    assert persisted == {"healthy", "degraded", "down", "timeout", "auth_error"}

    handler_src = inspect.getsource(integration_health.get_health_trends)
    for status in sorted(persisted):
        assert f'"{status}": 0' in handler_src, (
            f"{status} probes are counted in total_probes and drag uptime_pct "
            f"down, but the trends payload has no counter for them"
        )


@pytest.mark.asyncio
async def test_auth_error_and_timeout_reach_the_payload():
    """A provider whose token expired must not render as an empty row."""
    db = _FakeDB(
        _Result(
            [
                SimpleNamespace(provider="jira", status="auth_error", count=9),
                SimpleNamespace(provider="jira", status="timeout", count=3),
                SimpleNamespace(provider="jira", status="healthy", count=8),
            ]
        ),
        _Result([SimpleNamespace(provider="jira", avg_ms=120.0)]),
    )

    trends = await integration_health.get_health_trends(
        days=7, current_user=object(), db=db
    )
    jira = next(t for t in trends if t["provider"] == "jira")

    assert jira["auth_error"] == 9, "auth_error probes vanished from the payload"
    assert jira["timeout"] == 3, "timeout probes vanished from the payload"

    # The rendered per-status columns must account for every counted probe,
    # otherwise uptime_pct is computed against a total the table cannot explain.
    rendered = (
        jira["healthy"]
        + jira["degraded"]
        + jira["down"]
        + jira["timeout"]
        + jira["auth_error"]
    )
    assert rendered == jira["total_probes"] == 20
    assert jira["uptime_pct"] == 40.0


@pytest.mark.asyncio
async def test_avg_latency_is_not_the_last_status_group():
    """Latency comes from its own per-provider aggregate.

    Pre-fix, the value was ``round(last truthy row.avg_ms)`` — here that would
    be the 900ms ``degraded`` group or the 10ms ``healthy`` group depending on
    row order, never the 44ms actual average.
    """
    db = _FakeDB(
        _Result(
            [
                SimpleNamespace(provider="slack", status="healthy", count=95),
                SimpleNamespace(provider="slack", status="degraded", count=5),
            ]
        ),
        _Result([SimpleNamespace(provider="slack", avg_ms=54.5)]),
    )

    trends = await integration_health.get_health_trends(
        days=7, current_user=object(), db=db
    )
    slack = next(t for t in trends if t["provider"] == "slack")

    assert slack["avg_response_ms"] == 54.0, (
        "avg latency is still taken from whichever status group came last"
    )
    assert db.calls == 2, "the latency aggregate is not being queried separately"


@pytest.mark.asyncio
async def test_a_group_averaging_zero_is_no_longer_silently_skipped():
    """``if row.avg_ms:`` treated a genuine 0.0 average as "no data".

    A provider with no measurable latency must report 0, not inherit a stale
    value from another group.
    """
    db = _FakeDB(
        _Result([SimpleNamespace(provider="smtp", status="down", count=12)]),
        _Result([]),  # every probe was the response_ms=0 sentinel
    )

    trends = await integration_health.get_health_trends(
        days=7, current_user=object(), db=db
    )
    smtp = next(t for t in trends if t["provider"] == "smtp")

    assert smtp["avg_response_ms"] == 0
    assert smtp["down"] == 12
    assert smtp["uptime_pct"] == 0


def test_the_down_sentinel_is_excluded_from_the_average():
    """``ProbeResult(provider, "down", 0, ...)`` records a sentinel, not a 0ms
    measurement; averaging it in would understate real latency."""
    src = inspect.getsource(integration_health.get_health_trends)
    assert "IntegrationProbeResult.response_ms > 0" in src


def test_uptime_still_counts_only_healthy_probes():
    """The extra counters must not have widened what "up" means."""
    src = inspect.getsource(integration_health.get_health_trends)
    assert 't.get("healthy", 0) / total' in src
