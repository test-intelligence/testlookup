"""Regression: the CRITICAL health dependencies are the same set on both sides.

The backend classifies its dependency probes into two tiers in
``backend/app/routers/health.py``: the three stores wrapped in ``_critical(...)``
(postgres / mongo / redis — the ones ``/health/ready`` fails closed on) and the
optional ones run through ``_with_budget(...)`` (minio / ollama / chromadb).

The SPA's degraded banner renders those tiers differently: an optional
dependency degrading is a lost feature and shows a reassurance that pages
"will show data from PostgreSQL where possible"; a critical store being
unreachable is a lost-data alert that drops that promise (it is self-
contradictory when PostgreSQL is the very store that is down). ``useSystemHealth``
decides which tier an outage is by matching the check NAME against its own
``CRITICAL_DEPS`` set.

Same class as ``test_health_status_vocabulary_is_shared``: **a consumer renders
from an older copy of a producer's vocabulary.** If the backend promotes a new
probe to ``_critical`` (or renames one), a stale frontend set would silently
render that critical outage with the optional-degradation copy. Python cannot
import the TypeScript, so this pins the two sides against each other textually —
a new or renamed critical probe fails this test and forces ``CRITICAL_DEPS`` to
be updated rather than the mismatch shipping.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
HEALTH_PY = REPO / "backend" / "app" / "routers" / "health.py"
HOOK_TS = REPO / "frontend" / "src" / "hooks" / "useSystemHealth.ts"

pytestmark = pytest.mark.skipif(
    not HOOK_TS.exists(), reason="frontend not present in this checkout"
)


def _backend_critical_deps() -> set[str]:
    """The labels the router wraps in ``_critical(...)`` — e.g.
    ``_critical(_check_postgres(), "postgres")`` yields ``"postgres"``."""
    text = HEALTH_PY.read_text(encoding="utf-8")
    return set(re.findall(r'_critical\([^,]+,\s*"([a-z_]+)"\)', text))


def _frontend_critical_deps() -> set[str]:
    """The ``CRITICAL_DEPS`` set literal out of the hook."""
    text = HOOK_TS.read_text(encoding="utf-8")
    m = re.search(r"CRITICAL_DEPS[^=]*=\s*new Set\(\[([^\]]*)\]\)", text)
    assert m, "could not find the CRITICAL_DEPS set in useSystemHealth.ts"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_both_sides_were_actually_parsed():
    """Every assertion below is a set operation; empty scrapes pass vacuously."""
    backend = _backend_critical_deps()
    frontend = _frontend_critical_deps()
    assert backend, f"did not parse any _critical(...) labels out of health.py: {backend}"
    assert frontend, f"did not parse CRITICAL_DEPS out of useSystemHealth.ts: {frontend}"
    # The known-critical stores, pinned so a probe silently dropping from the
    # critical tier can't make both sides agree on a smaller set.
    assert {"postgres", "mongo", "redis"} <= backend, (
        f"health.py no longer wraps all of postgres/mongo/redis in _critical: {backend}"
    )


def test_critical_deps_match_across_languages():
    """The class guard: the backend's critical tier and the frontend's
    ``CRITICAL_DEPS`` must name exactly the same dependencies."""
    backend = _backend_critical_deps()
    frontend = _frontend_critical_deps()
    assert backend == frontend, (
        f"backend _critical dependencies {sorted(backend)} != frontend "
        f"CRITICAL_DEPS {sorted(frontend)}. Update CRITICAL_DEPS in "
        f"useSystemHealth.ts so a critical-store outage escalates to the "
        f"critical banner instead of rendering as an optional degradation."
    )
