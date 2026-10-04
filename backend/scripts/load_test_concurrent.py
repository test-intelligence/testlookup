#!/usr/bin/env python3
"""
Concurrent load test harness — measures throughput under parallel clients.

Differences from ``load_test_harness.py``:

  * **Concurrent**: fires requests through ``httpx.AsyncClient`` + ``asyncio.gather``
    so we measure both single-user latency AND the behaviour of the server
    under N parallel clients. The sequential harness could only report
    one-request-at-a-time latency.

  * **Auto token**: tries ``POST /api/v1/auth/dev-login`` when no token is
    provided via env var, so local runs against ``make dev`` just work.

  * **Wave-targeted scenarios**: the endpoints exercise the paths we
    actually changed in waves 1–4:
      - authorization guards (run-scoped endpoints)
      - search trigram indexes + project-scoped fingerprint join
      - run intelligence with cache populate on dedicated write session
      - report PDF generation off the event loop
      - flaky-coach with dedicated-session cache populate

Usage::

    # Sequential (single-client latency)
    python scripts/load_test_concurrent.py bench --base-url http://localhost:8000 --iterations 20

    # Concurrent (N parallel clients each firing M requests)
    python scripts/load_test_concurrent.py bench --base-url http://localhost:8000 \\
        --concurrency 10 --iterations 5

    # Check against budgets
    python scripts/load_test_concurrent.py bench --base-url http://localhost:8000 --check-budgets

    # Override auto token:
    TESTLOOKUP_BENCHMARK_ACCESS_TOKEN=<jwt> python scripts/load_test_concurrent.py bench ...

Viz suite (Visualization Upgrade, Phase D L1) -- ``VIZ_SCENARIOS``, a separate
list the live pytest smoke run never touches. For a disposable target only
(the homelab with ``seed_large_dataset.py --scale large``); localhost:8000 is
refused, because that is the shared dev stack::

    kubectl -n testlookup port-forward svc/testlookup-backend 18000:8000
    python scripts/load_test_concurrent.py bench --suite viz \\
        --base-url http://127.0.0.1:18000 --metrics-url http://127.0.0.1:18000/metrics \\
        --tokens-file load-users.txt --concurrency-levels 1,4,8,16 --cache both \\
        --iterations 10 --output viz.json --markdown viz.md
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import itertools
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx


# ── Scenario definitions ────────────────────────────────────────────────────

@dataclass
class Scenario:
    """A single benchmark scenario.

    ``path_fn`` is called per iteration and returns the URL path to hit,
    so scenarios that need a run_id / project_id can pick one from the
    seeded fixtures before each call (simulates real traffic).
    """
    operation: str
    method: str
    path_fn: callable  # (fixtures: dict) -> str
    description: str
    # Budget hint used to set yellow/red thresholds in the CLI output.
    budget_p95_ms: int = 0
    # Name of the THROUGHPUT_BUDGETS entry this scenario exercises, if any.
    # Set only where the mapping is real: throughput budgets describe a
    # *workload*, and several are workloads this harness does not drive at all
    # (run_ingestion, live_events_batch). Guessing a mapping would manufacture
    # pass/fail signal out of an unrelated measurement, which is the failure
    # this whole check exists to prevent — so unmapped budgets are reported as
    # uncovered instead.
    throughput_op: str = ""
    # If True, skip this scenario when running under pytest — used for
    # environment-dependent endpoints (e.g. health checks that probe
    # external services that may be degraded in dev/CI).
    env_dependent: bool = False


def _first_project_id(fixtures: dict) -> str:
    return fixtures["project_ids"][0]


def _first_run_id(fixtures: dict) -> str:
    return fixtures["run_ids"][0]


# The harness pulls budgets from the single source of truth in
# ``app.services.performance_budgets`` so harness, pytest smoke test, and
# Prometheus alerts always agree on what "fast enough" means.
# Running ``python scripts/load_test_concurrent.py`` — the invocation in this
# file's own usage string — puts *scripts/* on sys.path, NOT the backend root.
# So ``import app.…`` below raised ModuleNotFoundError, every budget resolved
# to 0, and --check-budgets silently skipped every scenario while printing
# "All budgets met". Put the backend root on the path explicitly.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# Set when the budget module cannot be imported, so --check-budgets can say so
# instead of reporting a pass it never evaluated.
_BUDGET_IMPORT_ERROR: str | None = None


def _budget(operation: str) -> int:
    """Lookup p95 budget in milliseconds. Returns 0 when the operation has no
    codified budget.

    An *import* failure is recorded rather than quietly returning 0 — that is
    what made the budget gate inert.
    """
    global _BUDGET_IMPORT_ERROR
    try:
        from app.services.performance_budgets import get_budget
    except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
        _BUDGET_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return 0
    b = get_budget(operation)
    return b.p95_ms if b else 0


def _throughput_budget(operation: str) -> float:
    """Minimum acceptable requests/sec for a workload, or 0.0 if uncodified."""
    global _BUDGET_IMPORT_ERROR
    try:
        from app.services.performance_budgets import get_throughput_budget
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        _BUDGET_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return 0.0
    b = get_throughput_budget(operation)
    return b.min_rps if b else 0.0


def _all_throughput_budgets() -> list:
    """Every codified throughput budget, so the check can name the ones no
    scenario covers rather than silently ignoring them."""
    try:
        from app.services.performance_budgets import THROUGHPUT_BUDGETS
    except Exception:  # noqa: BLE001
        return []
    return list(THROUGHPUT_BUDGETS)


# Measured 2026-08-08 against the live deployment (46,990 test_cases):
# the four pg_trgm indexes on test_cases (ix_test_cases_search,
# ix_test_cases_{name,suite,error}_trgm) have **idx_scan = 0** — never used,
# while other indexes on the same table accumulated 414k scans over the same
# window. EXPLAIN ANALYZE on the search predicate confirms a Seq Scan.
#
# This scenario previously described itself as "uses pg_trgm indexes from wave
# #5 fix". It does not, so the description is corrected rather than left
# asserting something the query plan contradicts.
#
# The planner is not obviously wrong: at this corpus the whole predicate costs
# 3.8ms and LIMIT 20 lets a seq scan stop early. Note also that the predicate is
# NOT what makes this endpoint an outlier — it measures 122.8ms p50 (5.8x the
# median endpoint) while the filter is 3.8ms and the pagination count is 30.5ms.
#
# Whether the ~20MB of unused GIN indexes (plus their write amplification on the
# hottest insert path) should be dropped is a SCALE question that 47k rows cannot
# settle — they may well be chosen on a corpus an order of magnitude larger.
# Deliberately not answered here.

SCENARIOS: list[Scenario] = [
    Scenario(
        "project_list",
        "GET",
        lambda _f: "/api/v1/projects",
        "List projects — baseline read, exercises get_accessible_project_ids cache",
        budget_p95_ms=_budget("project_list"),
    ),
    Scenario(
        "run_list",
        "GET",
        lambda _f: "/api/v1/runs?size=20",
        "Paginated run list — exercises the count + LEFT JOIN optimization",
        budget_p95_ms=_budget("run_list"),
    ),
    Scenario(
        "run_scoped_guard",
        "GET",
        lambda f: f"/api/v1/runs/{_first_run_id(f)}",
        "require_run_access guard overhead on a scoped GET",
        budget_p95_ms=_budget("run_scoped_guard"),
    ),
    Scenario(
        "run_intelligence_cached",
        "GET",
        lambda f: f"/api/v1/runs/{_first_run_id(f)}/intelligence",
        "Intelligence snapshot (warm cache after first call)",
        budget_p95_ms=_budget("run_intelligence"),
    ),
    Scenario(
        "keyword_search",
        "GET",
        lambda _f: "/api/v1/search?q=timeout&size=20",
        "ILIKE search — seq scan in practice; see the trgm note below",
        budget_p95_ms=_budget("keyword_search"),
        throughput_op="search_concurrent",
    ),
    Scenario(
        "keyword_search_long",
        "GET",
        lambda _f: "/api/v1/search?q=connection%20reset%20by%20peer&size=20",
        "Longer query across test_name/suite_name/error_message",
        budget_p95_ms=_budget("keyword_search"),
        throughput_op="search_concurrent",
    ),
    Scenario(
        "flaky_coach",
        "GET",
        lambda f: f"/api/v1/projects/{_first_project_id(f)}/flaky-coach",
        "Flaky coach with dedicated-write-session cache populate (item #4)",
        budget_p95_ms=_budget("flaky_coach"),
    ),
    Scenario(
        "health_details",
        "GET",
        lambda _f: "/health/details",
        "Deep health check — latency depends on which optional services "
        "(Ollama, ChromaDB, MinIO) are reachable. Each non-critical probe is "
        "bounded by a 1.5s httpx.Timeout. Skipped from the standard pytest "
        "smoke run since results vary by environment.",
        budget_p95_ms=_budget("health_details"),
        env_dependent=True,
    ),
]


# ── Token + fixtures ────────────────────────────────────────────────────────

async def fetch_dev_token(base_url: str) -> Optional[str]:
    """Try to grab a JWT via dev-login. Returns None if the endpoint is
    unavailable (non-development env, or 404)."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
            resp = await client.post("/api/v1/auth/dev-login")
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception:
        pass
    return None


async def fetch_fixtures(base_url: str, token: str) -> dict:
    """Pull project_ids / run_ids from the live server to feed the scenarios."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0, headers=headers) as client:
        projects_resp = await client.get("/api/v1/projects")
        projects_resp.raise_for_status()
        projects = projects_resp.json()
        project_ids = [p["id"] for p in projects]

        runs_resp = await client.get("/api/v1/runs?size=5")
        runs_resp.raise_for_status()
        runs = runs_resp.json().get("items", [])
        run_ids = [r["id"] for r in runs]

    if not project_ids or not run_ids:
        raise RuntimeError(
            f"Not enough seeded data to benchmark (projects={len(project_ids)}, "
            f"runs={len(run_ids)}). Run `make seed-data` first."
        )
    return {"project_ids": project_ids, "run_ids": run_ids}


# ── Benchmark runner ────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    operation: str
    method: str
    path_sample: str
    iterations: int
    concurrency: int
    latencies: list[float] = field(default_factory=list)
    errors: int = 0

    @property
    def p50(self) -> float:
        return _percentile(self.latencies, 50)

    @property
    def p95(self) -> float:
        return _percentile(self.latencies, 95)

    @property
    def p99(self) -> float:
        return _percentile(self.latencies, 99)

    @property
    def mean(self) -> float:
        return round(statistics.mean(self.latencies), 1) if self.latencies else 0.0

    @property
    def rps(self) -> float:
        """Throughput = total requests / total wall time.

        Stored on the result object after the batch completes so we can
        report both latency and throughput from one run.
        """
        return self._rps

    _rps: float = 0.0


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * pct / 100))
    return round(s[idx], 1)


async def _issue_one(
    client: httpx.AsyncClient,
    scenario: Scenario,
    fixtures: dict,
) -> tuple[float, bool]:
    """Fire one request. Returns (latency_ms, ok_flag)."""
    path = scenario.path_fn(fixtures)
    # perf_counter, NOT monotonic: on Windows time.monotonic() has a 15.625 ms
    # resolution, so every per-request latency snapped to a multiple of ~15.6 ms
    # and anything faster than one tick recorded as 0.0 ms. Endpoints here run
    # in single-digit to low-tens of milliseconds, i.e. entirely inside one
    # tick, so p50/p95 were quantisation buckets rather than measurements --
    # and a 30x increase in row count appeared to make the API *faster*.
    # perf_counter is 0.0001 ms here and is documented as the clock for short
    # durations.
    start = time.perf_counter()
    try:
        resp = await client.request(scenario.method, path)
        ok = resp.status_code < 500
    except Exception:
        ok = False
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, ok


async def run_scenario(
    base_url: str,
    token: str,
    fixtures: dict,
    scenario: Scenario,
    iterations: int,
    concurrency: int,
    warmup: int = 0,
) -> BenchmarkResult:
    """Run ``iterations`` requests per worker across ``concurrency`` workers.

    Uses a single ``AsyncClient`` per worker so connection pooling is
    representative of a real client (keep-alive on).

    ``warmup`` fires N requests per worker **before** the timed run. This
    removes cold-cache hits from the measurement — critical for paths
    that populate a cache on first call (run_intelligence, flaky_coach).
    """
    headers = {"Authorization": f"Bearer {token}"}
    result = BenchmarkResult(
        operation=scenario.operation,
        method=scenario.method,
        path_sample=scenario.path_fn(fixtures),
        iterations=iterations * concurrency,
        concurrency=concurrency,
    )

    async def worker():
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
            # Warmup phase — drop these latencies on the floor so the
            # reported p95/p99 reflects steady state, not cache cold-start.
            for _ in range(warmup):
                await _issue_one(client, scenario, fixtures)
            for _ in range(iterations):
                elapsed, ok = await _issue_one(client, scenario, fixtures)
                result.latencies.append(elapsed)
                if not ok:
                    result.errors += 1

    wall_start = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall_elapsed = time.perf_counter() - wall_start
    result._rps = round(result.iterations / wall_elapsed, 1) if wall_elapsed > 0 else 0.0
    return result


# ── Viz scenarios (Visualization Upgrade, Phase D: L1) ─────────────────────
#
# A SEPARATE list from ``SCENARIOS``, selected with ``bench --suite viz``.
# ``SCENARIOS`` is what ``tests/test_performance_budgets_live.py`` auto-runs
# against localhost:8000 on every pytest run; these are capacity scenarios for
# the homelab's 1M-row synthetic dataset (``scripts/seed_large_dataset.py``),
# and must never be swept into that smoke run.
#
# Every path, parameter and limit below was read off the routers
# (``routers/analytics*.py``, ``metrics.py``, ``summary_report.py``,
# ``releases.py``, ``suites.py``) on 2026-10-04. ``VIZ_ALLOWED_PARAMS`` is the
# allow-list a builder is checked against, so a renamed parameter fails loudly
# here instead of being silently ignored by FastAPI and benchmarking the
# default chart instead.

CHART_DATA = "/api/v1/analytics/chart-data"
CHART_ROWS = "/api/v1/analytics/chart-data/rows"
HEATMAP = "/api/v1/analytics/heatmap"
COVERAGE_MAP = "/api/v1/analytics/coverage-map"
FAILURE_GROUPS = "/api/v1/analytics/failure-groups"
TEST_SCATTER = "/api/v1/analytics/test-scatter"
LEGACY_COVERAGE = "/api/v1/analytics/coverage"
METRICS_SUMMARY = "/api/v1/metrics/summary"
SUMMARY_EXPORTS = "/api/v1/reports/summary/exports"
SUMMARY_PDF = "/api/v1/reports/summary/pdf"
SUMMARY_XLSX = "/api/v1/reports/summary/xlsx"

#: ``analytics_scope`` (services/analytics_scope.py): ``release_id`` and
#: ``suite_name`` REPEAT (``a=1&a=2``; a ``[]`` suffix is a different, unknown
#: parameter that FastAPI drops without a word).
_SCOPE_PARAMS = frozenset({"project_id", "release_id", "suite_name", "days"})
_ROWS_BUCKETS = frozenset(
    f"bucket_{dim}" for dim in (
        "day", "week", "project", "release", "suite", "status", "failure_category",
        "branch", "environment", "ingestion_source", "test", "error_signature",
    )
)
VIZ_ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    CHART_DATA: _SCOPE_PARAMS | {"metric", "group_by", "top_n"},
    CHART_ROWS: _SCOPE_PARAMS | {"metric", "group_by", "top_n", "page", "size"} | _ROWS_BUCKETS,
    HEATMAP: _SCOPE_PARAMS | {"kind", "rows", "runs"},
    COVERAGE_MAP: _SCOPE_PARAMS | {"depth", "suite", "class_key"},
    FAILURE_GROUPS: _SCOPE_PARAMS | {"include"},
    TEST_SCATTER: _SCOPE_PARAMS | {"min_executions", "limit", "order"},
    LEGACY_COVERAGE: _SCOPE_PARAMS,
    METRICS_SUMMARY: _SCOPE_PARAMS | {"include"},
    SUMMARY_EXPORTS: _SCOPE_PARAMS | {"format", "mode", "background"},
    SUMMARY_PDF: _SCOPE_PARAMS | {"mode"},
    SUMMARY_XLSX: _SCOPE_PARAMS | {"mode"},
}

#: Per-principal limits (``core/analytics_read_layer.py::RATE_LIMITED_ROUTES``).
#: Held in PROCESS memory: 4 workers make a pod's real ceiling up to 4x these.
VIZ_RATE_LIMITS_PER_MIN: dict[str, int] = {
    CHART_DATA: 120,
    CHART_ROWS: 120,
    HEATMAP: 60,
    COVERAGE_MAP: 60,
    FAILURE_GROUPS: 60,
    TEST_SCATTER: 60,
}

#: Epic §5.5 p95 budgets, by the ``LATENCY_BUDGETS`` names Phase D L3 will give
#: them. Until L3 codifies them, these are the values; once it does,
#: ``_viz_budget`` reads ``performance_budgets`` first so the two cannot drift.
VIZ_BUDGET_FALLBACK_MS: dict[str, int] = {
    "analytics_cached": 50,      # any read the analytics cache answers
    "chart_data_rate": 500,      # count / rate miss at 90 d
    "chart_data": 1500,          # percentile metrics (duration_p95)
    "analytics_heatmap": 800,    # heatmap miss
    "chart_rows": 300,           # rows, page 1
}
#: The budget a WARM cell of a cacheable scenario is held to.
CACHED_BUDGET_KEY = "analytics_cached"


def _viz_budget(key: str) -> int:
    if not key:
        return 0
    return _budget(key) or VIZ_BUDGET_FALLBACK_MS.get(key, 0)


def viz_path(route: str, params: list[tuple[str, object]]) -> str:
    """``route?query`` from an ORDERED list of pairs, so a repeated parameter
    is encoded the way FastAPI reads a ``list[str]``: ``release_id=a&release_id=b``.

    Refuses a parameter the route does not declare (``VIZ_ALLOWED_PARAMS``).
    ``None`` values are left out.
    """
    allowed = VIZ_ALLOWED_PARAMS.get(route)
    if allowed is None:
        raise ValueError(f"{route} is not a viz route this harness knows")
    pairs: list[tuple[str, str]] = []
    for name, value in params:
        if name not in allowed:
            raise ValueError(f"{name!r} is not a parameter of {route}")
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        pairs.append((name, str(value)))
    return f"{route}?{urllib.parse.urlencode(pairs)}" if pairs else route


class FixtureMissing(RuntimeError):
    """The dataset lacks what a scenario needs (e.g. two releases). The
    scenario is reported as skipped, never as a pass."""


def _need(f: dict, key: str, n: int = 1) -> list:
    values = f.get(key) or []
    if len(values) < n:
        raise FixtureMissing(f"needs {n} {key}, the project has {len(values)}")
    return values


def _cold_window(base_days: int, variant: int) -> tuple[int, int]:
    """``(days, cycle)`` for cache key ``variant``.

    Variant 0 is the warm key (``base_days``, cycle 0). A cold walk moves the
    window within the last third of ``base_days`` (at most 30 steps), so the
    workload stays representative of the nominal window; once those run out,
    ``cycle`` advances and the builder adds or rotates a suite filter to keep
    every key distinct.
    """
    span = max(1, min(30, (base_days + 2) // 3))
    return base_days - (variant % span), variant // span


def _scope(
    f: dict,
    days: Optional[int],
    *,
    releases: tuple = (),
    suites: tuple = (),
) -> list[tuple[str, object]]:
    pairs: list[tuple[str, object]] = [("project_id", f["project_id"])]
    pairs += [("release_id", r) for r in releases]
    pairs += [("suite_name", s) for s in suites]
    if days is not None:
        pairs.append(("days", days))
    return pairs


def _windowed(f: dict, base_days: int, variant: int, **kw) -> list[tuple[str, object]]:
    """The scope for a scenario with no filter of its own: the cold walk's
    later cycles add ONE suite filter, rotating through the project's suites."""
    days, cycle = _cold_window(base_days, variant)
    suites: tuple = ()
    if cycle:
        names = _need(f, "suite_names")
        suites = (names[(cycle - 1) % len(names)],)
    return _scope(f, days, suites=suites, **kw)


def _chart(metric: str, group_by: tuple[str, ...], base_days: int, top_n: Optional[int] = None):
    def build(f: dict, variant: int) -> str:
        params: list[tuple[str, object]] = [("metric", metric)]
        params += [("group_by", g) for g in group_by]
        params.append(("top_n", top_n))
        return viz_path(CHART_DATA, params + _windowed(f, base_days, variant))
    return build


def _chart_releases_suites(f: dict, variant: int) -> str:
    """Two releases compared (series), filtered to two suites: the repeated
    ``release_id`` / ``suite_name`` path. The cold walk rotates the suite pair."""
    releases = _need(f, "release_ids", 2)[:2]
    suites = _need(f, "suite_names", 2)
    days, cycle = _cold_window(90, variant)
    pair = (suites[(2 * cycle) % len(suites)], suites[(2 * cycle + 1) % len(suites)])
    params: list[tuple[str, object]] = [
        ("metric", "pass_rate"), ("group_by", "day"), ("group_by", "release"),
    ]
    return viz_path(CHART_DATA, params + _scope(f, days, releases=tuple(releases), suites=pair))


def _heatmap(kind: str, base_days: int):
    def build(f: dict, variant: int) -> str:
        if kind == "suite_release":
            _need(f, "release_ids")
        return viz_path(HEATMAP, [("kind", kind)] + _windowed(f, base_days, variant))
    return build


def _heatmap_test_run(f: dict, variant: int) -> str:
    """``test_run`` is suite-scoped: one suite's tests x its last runs. The
    cold walk moves to the next suite each cycle."""
    suites = _need(f, "suite_names")
    days, cycle = _cold_window(30, variant)
    return viz_path(
        HEATMAP, [("kind", "test_run")] + _scope(f, days, suites=(suites[cycle % len(suites)],))
    )


def _coverage_map_root(f: dict, variant: int) -> str:
    return viz_path(COVERAGE_MAP, [("depth", 1)] + _windowed(f, 30, variant))


def _coverage_map_suite(f: dict, variant: int) -> str:
    key = f.get("coverage_suite_key")
    if not key:
        raise FixtureMissing("no level-1 coverage-map node to open (root returned no suites)")
    return viz_path(COVERAGE_MAP, [("depth", 2), ("suite", key)] + _windowed(f, 30, variant))


def _failure_groups_edges(f: dict, variant: int) -> str:
    return viz_path(FAILURE_GROUPS, [("include", "edges")] + _windowed(f, 30, variant))


def _test_scatter(f: dict, variant: int) -> str:
    return viz_path(TEST_SCATTER, _windowed(f, 30, variant))


def _rows_page1(f: dict, variant: int) -> str:
    """Page 1 of the failing executions behind one day of a failures-by-day
    chart. A bucket is REQUIRED (``missing_parameter`` without one); the cold
    walk moves the bucket day, then the window."""
    today = f["today"]
    bucket = today - timedelta(days=1 + variant % 28)
    days = 30 - (variant // 28) % 10
    params: list[tuple[str, object]] = [
        ("metric", "failures"), ("group_by", "day"),
        ("bucket_day", bucket.isoformat()), ("page", 1), ("size", 50),
    ]
    return viz_path(CHART_ROWS, params + _scope(f, days))


def _legacy_coverage(f: dict, variant: int) -> str:
    return viz_path(LEGACY_COVERAGE, _windowed(f, 90, variant))


def _metrics_summary(f: dict, variant: int) -> str:
    return viz_path(METRICS_SUMMARY, _windowed(f, 7, variant))


def _export_request(background: bool):
    def build(f: dict, _variant: int) -> str:
        params: list[tuple[str, object]] = [
            ("format", f.get("export_format", "pdf")), ("mode", "window"),
            ("background", True if background else None),
        ]
        return viz_path(SUMMARY_EXPORTS, params + _scope(f, 7))
    return build


@dataclass
class VizScenario:
    """One viz benchmark. ``build(fixtures, variant)`` returns the path; the
    variant is the cache key index (warm: always 0, cold: a fresh one each call).
    """
    operation: str
    route: str
    build: Callable[[dict, int], str]
    description: str
    #: The §5.5 budget a COLD (miss) cell is held to; "" = no budget applies.
    budget_key: str = ""
    #: Read through ``analytics_read`` (VIZ-209 cache): a WARM cell is held to
    #: the cached-read budget. False for routes outside that layer.
    cacheable: bool = True
    #: "read", or a flow: "export_sync" / "export_background".
    kind: str = "read"
    method: str = "GET"

    def budget_for(self, cache: str) -> tuple[str, int]:
        if self.kind != "read":
            return "", 0
        key = CACHED_BUDGET_KEY if (cache == "warm" and self.cacheable) else (
            self.budget_key if cache == "cold" else ""
        )
        return key, _viz_budget(key)


VIZ_SCENARIOS: list[VizScenario] = [
    VizScenario("viz_chart_pass_rate_day_30d", CHART_DATA, _chart("pass_rate", ("day",), 30),
                "chart-data pass_rate by day, 30 d", budget_key="chart_data_rate"),
    VizScenario("viz_chart_pass_rate_day_90d", CHART_DATA, _chart("pass_rate", ("day",), 90),
                "chart-data pass_rate by day, 90 d", budget_key="chart_data_rate"),
    VizScenario("viz_chart_day_suite_top7_90d", CHART_DATA,
                _chart("pass_rate", ("day", "suite"), 90, top_n=7),
                "chart-data pass_rate day x suite, top_n=7, 90 d", budget_key="chart_data_rate"),
    VizScenario("viz_chart_2releases_2suites_90d", CHART_DATA, _chart_releases_suites,
                "chart-data pass_rate day x release, 2 releases + 2 suites (repeated params), 90 d",
                budget_key="chart_data_rate"),
    VizScenario("viz_chart_duration_p95_90d", CHART_DATA, _chart("duration_p95", ("day",), 90),
                "chart-data duration_p95 by day, 90 d (percentile)", budget_key="chart_data"),
    VizScenario("viz_chart_failures_by_suite_90d", CHART_DATA, _chart("failures", ("suite",), 90),
                "chart-data failures by suite, 90 d", budget_key="chart_data_rate"),
    VizScenario("viz_heatmap_suite_day_90d", HEATMAP, _heatmap("suite_day", 90),
                "heatmap kind=suite_day, 90 d (the kind's cap)", budget_key="analytics_heatmap"),
    VizScenario("viz_heatmap_suite_environment_30d", HEATMAP, _heatmap("suite_environment", 30),
                "heatmap kind=suite_environment, 30 d", budget_key="analytics_heatmap"),
    VizScenario("viz_heatmap_suite_release_90d", HEATMAP, _heatmap("suite_release", 90),
                "heatmap kind=suite_release, 90 d", budget_key="analytics_heatmap"),
    VizScenario("viz_heatmap_test_run_suite", HEATMAP, _heatmap_test_run,
                "heatmap kind=test_run, one suite, 30 d", budget_key="analytics_heatmap"),
    VizScenario("viz_coverage_map_root", COVERAGE_MAP, _coverage_map_root,
                "coverage-map depth=1 (root), 30 d"),
    VizScenario("viz_coverage_map_suite", COVERAGE_MAP, _coverage_map_suite,
                "coverage-map depth=2 (one suite's classes), 30 d"),
    VizScenario("viz_failure_groups_edges_30d", FAILURE_GROUPS, _failure_groups_edges,
                "failure-groups include=edges, 30 d"),
    VizScenario("viz_test_scatter_30d", TEST_SCATTER, _test_scatter, "test-scatter, 30 d"),
    VizScenario("viz_chart_rows_page1", CHART_ROWS, _rows_page1,
                "chart-data rows, failures on one day, page 1 (size 50), 30 d",
                budget_key="chart_rows"),
    VizScenario("viz_explorer_discovery", CHART_DATA, _chart("executions", ("suite", "environment"), 30),
                "Explorer discovery: executions by [suite, environment], 30 d",
                budget_key="chart_data_rate"),
    VizScenario("viz_legacy_coverage_90d", LEGACY_COVERAGE, _legacy_coverage,
                "legacy /analytics/coverage, 90 d"),
    VizScenario("viz_legacy_metrics_summary", METRICS_SUMMARY, _metrics_summary,
                "legacy /metrics/summary, 7 d (not behind the analytics read layer)",
                cacheable=False),
    VizScenario("viz_export_sync", SUMMARY_EXPORTS, _export_request(False),
                "summary export, sync: POST /exports, then GET /pdf when delivery=download "
                "(or poll when the window is over the sync cap)",
                cacheable=False, kind="export_sync", method="POST"),
    VizScenario("viz_export_background", SUMMARY_EXPORTS, _export_request(True),
                "summary export, background=true: POST /exports (202), poll GET /exports/{id} "
                "to completed", cacheable=False, kind="export_background", method="POST"),
]

#: Not a VizScenario (it measures the limiter, not latency): run last, because
#: it leaves its principal over the chart-data limit for up to a minute.
RATE_LIMIT_BURST = "rate_limit_burst"


# ── Viz: target safety ─────────────────────────────────────────────────────

#: The shared dev stack (``make dev``) serves on localhost:8000. It is never
#: load-tested: its database is the one every developer and agent is using.
LOCAL_DEV_STACK_PORT = 8000
#: The harness speaks HTTP to the API only. A database or Redis URL is refused
#: outright, whatever the flags.
FORBIDDEN_PORTS = {5432: "PostgreSQL", 6379: "Redis"}
OVERRIDE_FLAG = "--i-know-this-is-not-the-shared-dev-stack"


class UnsafeTarget(ValueError):
    pass


def _is_local_host(host: str) -> bool:
    if host in ("localhost", "localhost.") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def check_target(url: str, *, allow_local_dev_stack: bool = False, what: str = "--base-url") -> None:
    """Refuse anything but an HTTP(S) API URL, any URL on a database or Redis
    port, and localhost:8000 unless the operator passed ``OVERRIDE_FLAG``."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeTarget(
            f"{what} {url!r}: the harness speaks HTTP(S) only "
            "(never a postgres://, redis:// or other URL)"
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeTarget(f"{what} {url!r} has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeTarget(f"{what} {url!r}: {exc}") from None
    if port in FORBIDDEN_PORTS:
        raise UnsafeTarget(
            f"{what} {url!r} is the {FORBIDDEN_PORTS[port]} port: the harness "
            "talks to the API over HTTP only"
        )
    if port == LOCAL_DEV_STACK_PORT and _is_local_host(host) and not allow_local_dev_stack:
        raise UnsafeTarget(
            f"{what} {url!r} looks like the shared dev stack (localhost:{LOCAL_DEV_STACK_PORT}), "
            "which must never be load-tested. Port-forward the target to another local "
            f"port, or pass {OVERRIDE_FLAG} if this really is a disposable server."
        )


# ── Viz: principals ────────────────────────────────────────────────────────

def load_tokens_file(path: str) -> list[str]:
    """One bearer token per line; blank lines and ``#`` comments ignored."""
    with open(path, encoding="utf-8") as fh:
        tokens = [line.strip() for line in fh]
    tokens = [t for t in tokens if t and not t.startswith("#")]
    if not tokens:
        raise ValueError(f"{path} holds no tokens")
    return tokens


class TokenPool:
    """Round-robin over the load users' tokens, one per REQUEST, so N tokens
    are N principals sharing the load evenly (the rate limit is per principal).
    """

    def __init__(self, tokens: list[str]):
        if not tokens:
            raise ValueError("TokenPool needs at least one token")
        self._tokens = list(tokens)
        self._next = itertools.cycle(range(len(self._tokens)))

    def __len__(self) -> int:
        return len(self._tokens)

    def first(self) -> str:
        return self._tokens[0]

    def next(self) -> str:
        return self._tokens[next(self._next)]


# ── Viz: server-side truth from /metrics ───────────────────────────────────

ANALYTICS_HISTOGRAM = "testlookup_analytics_query_duration_seconds"
ANALYTICS_DEGRADED = "testlookup_analytics_read_degraded_total"

_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

MetricKey = tuple[str, tuple[tuple[str, str], ...]]


def parse_prometheus_text(text: str) -> dict[MetricKey, float]:
    """``{(name, sorted label pairs): value}`` for every sample line."""
    samples: dict[MetricKey, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name, raw_labels, raw_value = m.groups()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = tuple(sorted(_LABEL_RE.findall(raw_labels or "")))
        samples[(name, labels)] = value
    return samples


def _delta(after: float, before: Optional[float]) -> float:
    """A counter delta; a counter that went DOWN was reset (a worker restart),
    and its post-reset value is the best lower bound there is."""
    if before is None:
        return after
    return after - before if after >= before else after


def histogram_quantile(q: float, buckets: list[tuple[float, float]]) -> Optional[float]:
    """Prometheus' ``histogram_quantile`` over cumulative ``(le, count)``
    buckets: linear interpolation inside the bucket holding rank ``q * total``.
    ``None`` when there are no observations. A rank in the +Inf bucket returns
    the highest finite bound (Prometheus does the same)."""
    if not buckets:
        return None
    ordered = sorted(buckets, key=lambda b: b[0])
    total = ordered[-1][1]
    if total <= 0:
        return None
    rank = q * total
    prev_le, prev_count = 0.0, 0.0
    for le, count in ordered:
        if count >= rank:
            if le == float("inf"):
                return prev_le
            in_bucket = count - prev_count
            if in_bucket <= 0:
                return le
            return prev_le + (le - prev_le) * (rank - prev_count) / in_bucket
        prev_le, prev_count = le, count
    return prev_le


def diff_analytics_metrics(before: dict[MetricKey, float], after: dict[MetricKey, float]) -> dict:
    """What the server recorded between two scrapes: per route, the request
    count and p95 per outcome (and all outcomes together), the hit ratio, the
    timeouts and errors; and the degraded-read counters by reason."""
    buckets: dict[tuple[str, str], dict[float, float]] = {}
    counts: dict[tuple[str, str], float] = {}
    degraded: dict[str, float] = {}
    for key, value in after.items():
        name, labels = key
        lab = dict(labels)
        if name == f"{ANALYTICS_HISTOGRAM}_bucket":
            le = float(lab.get("le", "nan").replace("+Inf", "inf"))
            series = (lab.get("route", ""), lab.get("outcome", ""))
            buckets.setdefault(series, {})[le] = _delta(value, before.get(key))
        elif name == f"{ANALYTICS_HISTOGRAM}_count":
            counts[(lab.get("route", ""), lab.get("outcome", ""))] = _delta(value, before.get(key))
        elif name == ANALYTICS_DEGRADED:
            reason = lab.get("reason", "")
            degraded[reason] = degraded.get(reason, 0.0) + _delta(value, before.get(key))

    routes: dict[str, dict] = {}
    merged: dict[str, dict[float, float]] = {}
    for (route, outcome), by_le in buckets.items():
        n = counts.get((route, outcome), by_le.get(float("inf"), 0.0))
        if n <= 0:
            continue
        p95 = histogram_quantile(0.95, list(by_le.items()))
        routes.setdefault(route, {})[outcome] = {
            "count": int(n),
            "p95_ms": round(p95 * 1000, 1) if p95 is not None else None,
        }
        acc = merged.setdefault(route, {})
        for le, c in by_le.items():
            acc[le] = acc.get(le, 0.0) + c
    for route, by_le in merged.items():
        outcomes = routes[route]
        total = sum(o["count"] for o in outcomes.values())
        p95 = histogram_quantile(0.95, list(by_le.items()))
        hits = outcomes.get("hit", {}).get("count", 0)
        outcomes["all"] = {
            "count": total,
            "p95_ms": round(p95 * 1000, 1) if p95 is not None else None,
            "hit_ratio": round(hits / total, 3) if total else None,
            "timeouts": outcomes.get("timeout", {}).get("count", 0),
            "errors": outcomes.get("error", {}).get("count", 0),
        }
    degraded = {k: int(v) for k, v in degraded.items() if v > 0}
    return {
        "routes": routes,
        "degraded": degraded,
        "degraded_total": sum(degraded.values()),
    }


async def scrape_metrics(url: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> dict[MetricKey, float]:
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return parse_prometheus_text(resp.text)


# ── Viz: fixtures ──────────────────────────────────────────────────────────

DEFAULT_VIZ_PROJECT_SLUG = "synthetic-perf-dataset"  # seed_large_dataset.DEFAULT_SLUG


async def resolve_viz_fixtures(client: httpx.AsyncClient, project_slug: str) -> dict:
    """The project by slug, its releases (most runs first) and suites, from the
    API -- never from the database -- plus one level-1 coverage-map suite key
    so the drill scenario has a real parent to open."""
    resp = await client.get("/api/v1/projects")
    resp.raise_for_status()
    projects = resp.json()
    match = next((p for p in projects if p.get("slug") == project_slug), None)
    if match is None:
        slugs = sorted(p.get("slug", "?") for p in projects)
        raise RuntimeError(
            f"no project with slug {project_slug!r} visible to this token "
            f"({len(slugs)} visible: {', '.join(slugs[:10])}{' ...' if len(slugs) > 10 else ''}). "
            "Seed it with scripts/seed_large_dataset.py or pass --project-slug."
        )
    project_id = str(match["id"])

    resp = await client.get("/api/v1/releases", params={"project_id": project_id})
    resp.raise_for_status()
    releases = resp.json().get("items", [])
    releases.sort(key=lambda r: -(r.get("test_run_count") or 0))
    release_ids = [str(r["id"]) for r in releases]

    resp = await client.get("/api/v1/suites", params={"project_id": project_id})
    resp.raise_for_status()
    suite_names = sorted({str(s["name"]) for s in resp.json().get("items", []) if s.get("name")})

    fixtures = {
        "project_id": project_id,
        "project_slug": project_slug,
        "project_name": match.get("name", ""),
        "release_ids": release_ids,
        "suite_names": suite_names,
        "coverage_suite_key": None,
        "today": datetime.now(timezone.utc).date(),
    }
    try:
        resp = await client.get(viz_path(COVERAGE_MAP, [("depth", 1)] + _scope(fixtures, 30)))
        if resp.status_code == 200:
            for node in resp.json().get("nodes", []):
                node_id = str(node.get("id", ""))
                if node_id.startswith("s:"):
                    fixtures["coverage_suite_key"] = node_id[2:]
                    break
    except Exception:  # noqa: BLE001 - the drill scenario reports itself skipped
        pass
    return fixtures


# ── Viz: runner ────────────────────────────────────────────────────────────

@dataclass
class VizCellResult:
    """One scenario x concurrency x cache mode."""
    operation: str
    route: str
    cache: str
    concurrency: int
    path_sample: str = ""
    latencies: list[float] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    #: ``X-Analytics-Cache: hit|miss`` as the client saw it (absent in production).
    cache_headers: dict[str, int] = field(default_factory=dict)
    rps: float = 0.0
    budget_key: str = ""
    budget_p95_ms: int = 0
    server: Optional[dict] = None
    skipped: Optional[str] = None
    flow: list[dict] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.latencies)

    @property
    def p50(self) -> float:
        return _percentile(self.latencies, 50)

    @property
    def p95(self) -> float:
        return _percentile(self.latencies, 95)

    @property
    def p99(self) -> float:
        return _percentile(self.latencies, 99)

    @property
    def max(self) -> float:
        return round(max(self.latencies), 1) if self.latencies else 0.0

    @property
    def non_2xx(self) -> int:
        return sum(c for s, c in self.status_counts.items() if not s.startswith("2"))

    @property
    def server_route(self) -> Optional[dict]:
        if not self.server:
            return None
        return self.server.get("routes", {}).get(self.route, {}).get("all")

    def verdict(self) -> str:
        if self.skipped:
            return "skipped"
        if not self.budget_p95_ms:
            return "n/a"
        if self.non_2xx:
            return "FAIL"
        return "PASS" if self.p95 <= self.budget_p95_ms else "FAIL"

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "route": self.route,
            "cache": self.cache,
            "concurrency": self.concurrency,
            "path_sample": self.path_sample,
            "n": self.n,
            "p50_ms": self.p50,
            "p95_ms": self.p95,
            "p99_ms": self.p99,
            "max_ms": self.max,
            "rps": self.rps,
            "status_counts": dict(sorted(self.status_counts.items())),
            "cache_headers": self.cache_headers,
            "budget_key": self.budget_key,
            "budget_p95_ms": self.budget_p95_ms,
            "verdict": self.verdict(),
            "server": self.server,
            "skipped": self.skipped,
            "flow": self.flow,
        }


def _count(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


async def _issue_viz(client: httpx.AsyncClient, method: str, path: str, token: str) -> tuple[float, str, Optional[httpx.Response]]:
    start = time.perf_counter()
    try:
        resp = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        status = str(resp.status_code)
    except Exception as exc:  # noqa: BLE001 - a transport failure is a result
        resp, status = None, f"transport:{type(exc).__name__}"
    return (time.perf_counter() - start) * 1000, status, resp


async def run_viz_cell(
    base_url: str,
    pool: TokenPool,
    fixtures: dict,
    scenario: VizScenario,
    *,
    cache: str,
    concurrency: int,
    iterations: int,
    warmup: int,
    key_counter: Iterator[int],
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> VizCellResult:
    """``iterations`` timed reads per worker over ``concurrency`` workers.

    warm: every request asks the same key (variant 0), primed by ``warmup``
    requests per worker first. cold: each request takes the next variant from
    ``key_counter`` -- one counter per scenario for the whole run, so a key
    seen at concurrency 1 is not re-asked at concurrency 4 -- and no warmup.
    """
    budget_key, budget = scenario.budget_for(cache)
    result = VizCellResult(
        scenario.operation, scenario.route, cache, concurrency,
        budget_key=budget_key, budget_p95_ms=budget,
    )
    try:
        result.path_sample = scenario.build(fixtures, 0)
    except FixtureMissing as exc:
        result.skipped = str(exc)
        return result

    def next_path() -> str:
        return scenario.build(fixtures, 0 if cache == "warm" else next(key_counter))

    async def worker() -> None:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0, transport=transport) as client:
            if cache == "warm":
                for _ in range(warmup):
                    await _issue_viz(client, scenario.method, next_path(), pool.next())
            for _ in range(iterations):
                elapsed, status, resp = await _issue_viz(
                    client, scenario.method, next_path(), pool.next()
                )
                result.latencies.append(elapsed)
                _count(result.status_counts, status)
                header = resp.headers.get("X-Analytics-Cache") if resp is not None else None
                if header:
                    _count(result.cache_headers, header)

    wall_start = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall = time.perf_counter() - wall_start
    result.rps = round(result.n / wall, 1) if wall > 0 else 0.0
    return result


def _parse_instant(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def run_export_flow(
    client: httpx.AsyncClient,
    token: str,
    fixtures: dict,
    scenario: VizScenario,
    *,
    poll_interval: float = 1.0,
    timeout: float = 300.0,
) -> dict:
    """One export, end to end. ``total_ms`` runs from the POST to the file
    (sync) or to ``status: completed`` (background); ``job_s`` is the server's
    own ``finished_at - requested_at`` when it reports both."""
    headers = {"Authorization": f"Bearer {token}"}
    statuses: dict[str, int] = {}
    out: dict = {"statuses": statuses}
    start = time.perf_counter()
    resp = await client.post(scenario.build(fixtures, 0), headers=headers)
    out["post_ms"] = round((time.perf_counter() - start) * 1000, 1)
    _count(statuses, str(resp.status_code))
    if resp.status_code not in (200, 202):
        out.update(final_status="http_error", total_ms=out["post_ms"])
        return out
    body = resp.json()
    out["delivery"] = body.get("delivery")
    out["estimated_tests"] = body.get("estimated_tests")
    if out["delivery"] == "download":
        fmt = fixtures.get("export_format", "pdf")
        route = SUMMARY_XLSX if fmt == "xlsx" else SUMMARY_PDF
        dl_start = time.perf_counter()
        dl = await client.get(viz_path(route, [("mode", "window")] + _scope(fixtures, 7)), headers=headers)
        out["download_ms"] = round((time.perf_counter() - dl_start) * 1000, 1)
        out["size_bytes"] = len(dl.content)
        _count(statuses, str(dl.status_code))
        out["final_status"] = "completed" if dl.status_code == 200 else "http_error"
        out["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return out

    export = body.get("export") or {}
    export_id = export.get("id")
    out["export_id"] = export_id
    out["dispatched"] = body.get("dispatched")
    polls = 0
    final = export.get("status")
    while export_id and final not in ("completed", "failed"):
        if time.perf_counter() - start > timeout:
            final = "poll_timeout"
            break
        await asyncio.sleep(poll_interval)
        poll = await client.get(f"{SUMMARY_EXPORTS}/{export_id}", headers=headers)
        polls += 1
        _count(statuses, str(poll.status_code))
        if poll.status_code != 200:
            final = "http_error"
            break
        export = poll.json()
        final = export.get("status")
    out["polls"] = polls
    out["final_status"] = final
    out["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
    requested, finished = _parse_instant(export.get("requested_at")), _parse_instant(export.get("finished_at"))
    if requested and finished:
        out["job_s"] = round((finished - requested).total_seconds(), 2)
    if export.get("error"):
        out["error"] = str(export["error"])[:200]
    return out


async def run_export_cell(
    base_url: str,
    pool: TokenPool,
    fixtures: dict,
    scenario: VizScenario,
    *,
    runs: int,
    poll_interval: float,
    timeout: float,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> VizCellResult:
    """Exports run one at a time: each is a real job that writes a row (and a
    file) in the target deployment, kept until it expires."""
    result = VizCellResult(scenario.operation, scenario.route, "-", 1)
    try:
        result.path_sample = scenario.build(fixtures, 0)
    except FixtureMissing as exc:
        result.skipped = str(exc)
        return result
    wall_start = time.perf_counter()
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0, transport=transport) as client:
        for _ in range(runs):
            try:
                flow = await run_export_flow(
                    client, pool.next(), fixtures, scenario,
                    poll_interval=poll_interval, timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - one broken run is a result
                flow = {"final_status": f"transport:{type(exc).__name__}", "statuses": {}}
            result.flow.append(flow)
            if "total_ms" in flow:
                result.latencies.append(flow["total_ms"])
            for status, c in flow.get("statuses", {}).items():
                result.status_counts[status] = result.status_counts.get(status, 0) + c
            if not flow.get("statuses"):
                _count(result.status_counts, flow["final_status"])
    wall = time.perf_counter() - wall_start
    result.rps = round(result.n / wall, 2) if wall > 0 else 0.0
    return result


# ── Viz: rate-limit burst ──────────────────────────────────────────────────

@dataclass
class BurstResult:
    sent: int
    elapsed_s: float
    status_counts: dict[str, int]
    rate_limited: int
    #: Every 429 carried ``Retry-After`` (False if any lacked it; None if no 429).
    retry_after_present: Optional[bool]
    retry_after_values: list[str]
    #: The body ``code`` of the 429s (expected: ``rate_limited``).
    codes: list[str]
    first_429_at: Optional[int]
    limit_per_min: int

    def to_dict(self) -> dict:
        return {
            "sent": self.sent,
            "elapsed_s": self.elapsed_s,
            "status_counts": dict(sorted(self.status_counts.items())),
            "rate_limited": self.rate_limited,
            "retry_after_present": self.retry_after_present,
            "retry_after_values": self.retry_after_values,
            "codes": self.codes,
            "first_429_at": self.first_429_at,
            "limit_per_min": self.limit_per_min,
        }


def summarize_burst(
    responses: list[tuple[int, Mapping[str, str], Any]],
    elapsed_s: float,
    limit_per_min: int = VIZ_RATE_LIMITS_PER_MIN[CHART_DATA],
) -> BurstResult:
    """``responses`` are ``(status, headers, parsed body or None)`` in send order."""
    statuses: dict[str, int] = {}
    retry_values: set[str] = set()
    codes: set[str] = set()
    limited = 0
    all_have_retry = True
    first: Optional[int] = None
    for i, (status, headers, body) in enumerate(responses):
        _count(statuses, str(status))
        if status != 429:
            continue
        limited += 1
        if first is None:
            first = i
        retry = None
        for name, value in headers.items():
            if name.lower() == "retry-after":
                retry = value
        if retry is None:
            all_have_retry = False
        else:
            retry_values.add(str(retry))
        if isinstance(body, dict) and body.get("code"):
            codes.add(str(body["code"]))
    return BurstResult(
        sent=len(responses),
        elapsed_s=round(elapsed_s, 2),
        status_counts=statuses,
        rate_limited=limited,
        retry_after_present=(all_have_retry if limited else None),
        retry_after_values=sorted(retry_values),
        codes=sorted(codes),
        first_429_at=first,
        limit_per_min=limit_per_min,
    )


async def run_rate_limit_burst(
    base_url: str,
    token: str,
    fixtures: dict,
    *,
    requests: int = 150,
    window_s: float = 60.0,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> BurstResult:
    """ONE principal fires ``requests`` chart-data reads (one warm key, so the
    server's cost is a cache hit) inside ``window_s``, over ONE keep-alive
    connection. The limiter's storage is per process, so one connection is
    the best chance of meeting a single worker's counter; through an ingress
    that spreads requests across workers, fewer 429s than ``requests - limit``
    is the per-process design, not a missing limit."""
    path = _chart("pass_rate", ("day",), 30)(fixtures, 0)
    responses: list[tuple[int, Mapping[str, str], Any]] = []
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    start = time.perf_counter()
    async with httpx.AsyncClient(
        base_url=base_url, timeout=30.0, limits=limits, transport=transport,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        for _ in range(requests):
            if time.perf_counter() - start > window_s:
                break
            try:
                resp = await client.get(path)
            except Exception:  # noqa: BLE001 - counted as status 0
                responses.append((0, {}, None))
                continue
            body: Any = None
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = None
            responses.append((resp.status_code, dict(resp.headers), body))
    return summarize_burst(responses, time.perf_counter() - start)


# ── Viz: report ────────────────────────────────────────────────────────────

def _fmt_statuses(counts: dict[str, int]) -> str:
    return " ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "-"


def render_viz_markdown(report: dict) -> str:
    """One row per scenario x concurrency x cache mode, then the export flows
    and the burst. Client latency is the client's clock (network included);
    server p95 is ``histogram_quantile`` over the scenario route's bucket
    deltas, as coarse as the histogram's buckets (10/50/100/250/500 ms ...)."""
    lines = [
        f"# Viz load test: {report['base_url']}",
        "",
        f"- Project: {report['project']['name']} (`{report['project']['slug']}`, "
        f"{report['project']['id']}); {report['project']['releases']} releases, "
        f"{report['project']['suites']} suites",
        f"- Started {report['timestamp']}; {report['principals']} principal(s); "
        f"{report['iterations_per_worker']} requests per worker per cell",
        f"- Server metrics: {'scraped from ' + report['metrics_url'] if report.get('metrics_url') else 'not scraped (no --metrics-url)'}",
        "",
        "| Scenario | Cache | Conc | n | p50 ms | p95 ms | p99 ms | max ms | Server p95 ms | Hit ratio | rps | Statuses | Budget | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    flows = []
    for cell in report["results"]:
        if cell["flow"]:
            flows.append(cell)
        server = ((cell.get("server") or {}).get("routes", {}).get(cell["route"], {}).get("all")) or {}
        budget = f"{cell['budget_key']} {cell['budget_p95_ms']}" if cell["budget_p95_ms"] else "-"
        if cell["skipped"]:
            lines.append(
                f"| {cell['operation']} | {cell['cache']} | {cell['concurrency']} | 0 | | | | | | | | "
                f"skipped: {cell['skipped']} | {budget} | skipped |"
            )
            continue
        hit = server.get("hit_ratio")
        lines.append(
            f"| {cell['operation']} | {cell['cache']} | {cell['concurrency']} | {cell['n']} | "
            f"{cell['p50_ms']} | {cell['p95_ms']} | {cell['p99_ms']} | {cell['max_ms']} | "
            f"{server.get('p95_ms', '-') if server else '-'} | {hit if hit is not None else '-'} | "
            f"{cell['rps']} | {_fmt_statuses(cell['status_counts'])} | {budget} | {cell['verdict']} |"
        )
    if flows:
        lines += ["", "## Export flows", "",
                  "| Scenario | Run | Delivery | Final | Total ms | POST ms | Download ms | Job s | Polls |",
                  "|---|---:|---|---|---:|---:|---:|---:|---:|"]
        for cell in flows:
            for i, f in enumerate(cell["flow"], 1):
                lines.append(
                    f"| {cell['operation']} | {i} | {f.get('delivery', '-')} | {f.get('final_status', '-')} | "
                    f"{f.get('total_ms', '-')} | {f.get('post_ms', '-')} | {f.get('download_ms', '-')} | "
                    f"{f.get('job_s', '-')} | {f.get('polls', '-')} |"
                )
    burst = report.get("rate_limit_burst")
    if burst:
        lines += [
            "", "## Rate-limit burst (one principal, chart-data)", "",
            f"- Sent {burst['sent']} in {burst['elapsed_s']} s against a {burst['limit_per_min']}/min "
            "per-principal, per-process limit",
            f"- Statuses: {_fmt_statuses(burst['status_counts'])}; 429s: {burst['rate_limited']} "
            f"(first at request #{burst['first_429_at'] + 1 if burst['first_429_at'] is not None else '-'})",
            f"- Retry-After on every 429: {burst['retry_after_present']} (values {burst['retry_after_values'] or '-'}); "
            f"body code(s): {burst['codes'] or '-'}",
        ]
    totals = report.get("server_totals")
    if totals:
        lines += ["", f"Degraded reads over the run: {totals.get('degraded') or 'none'}"]
    return "\n".join(lines) + "\n"


def _parse_levels(raw: Optional[str], fallback: int) -> list[int]:
    if not raw:
        return [fallback]
    levels = [int(x) for x in raw.split(",") if x.strip()]
    if not levels or any(n < 1 for n in levels):
        raise ValueError(f"--concurrency-levels {raw!r}: positive integers, comma separated")
    return levels


def select_viz_scenarios(only: Optional[str]) -> tuple[list[VizScenario], bool]:
    """``(scenarios, run_burst)`` for ``--only a,b`` (all, and the burst, when
    absent). An unknown name is an error, not an empty run."""
    if not only:
        return list(VIZ_SCENARIOS), True
    names = [n.strip() for n in only.split(",") if n.strip()]
    known = {sc.operation: sc for sc in VIZ_SCENARIOS}
    unknown = [n for n in names if n not in known and n != RATE_LIMIT_BURST]
    if unknown:
        raise ValueError(f"unknown viz scenario(s): {', '.join(unknown)}")
    return [known[n] for n in names if n in known], RATE_LIMIT_BURST in names


async def cmd_bench_viz(args: argparse.Namespace) -> int:
    try:
        check_target(args.base_url, allow_local_dev_stack=args.i_know_this_is_not_the_shared_dev_stack)
        if args.metrics_url:
            check_target(
                args.metrics_url, what="--metrics-url",
                allow_local_dev_stack=args.i_know_this_is_not_the_shared_dev_stack,
            )
        levels = _parse_levels(args.concurrency_levels, args.concurrency)
        scenarios, run_burst = select_viz_scenarios(args.only)
    except ValueError as exc:  # UnsafeTarget included
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.skip_burst:
        run_burst = False

    if args.tokens_file:
        try:
            tokens = load_tokens_file(args.tokens_file)
        except (OSError, ValueError) as exc:
            print(f"ERROR: --tokens-file: {exc}", file=sys.stderr)
            return 2
    else:
        token = os.getenv("TESTLOOKUP_BENCHMARK_ACCESS_TOKEN") or await fetch_dev_token(args.base_url)
        if not token:
            print("ERROR: no token. Pass --tokens-file, set TESTLOOKUP_BENCHMARK_ACCESS_TOKEN, "
                  "or target a development-mode server.", file=sys.stderr)
            return 2
        tokens = [token]
    pool = TokenPool(tokens)

    try:
        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=30.0,
            headers={"Authorization": f"Bearer {pool.first()}"},
        ) as client:
            fixtures = await resolve_viz_fixtures(client, args.project_slug)
    except Exception as exc:  # noqa: BLE001 - reported, exit 2
        print(f"ERROR: could not resolve fixtures -- {exc}", file=sys.stderr)
        return 2
    fixtures["export_format"] = args.export_format

    modes = ["cold", "warm"] if args.cache == "both" else [args.cache]
    print(f"Target      : {args.base_url}")
    print(f"Project     : {fixtures['project_name']} (slug {fixtures['project_slug']}, id {fixtures['project_id']})")
    print(f"Fixtures    : {len(fixtures['release_ids'])} release(s), {len(fixtures['suite_names'])} suite(s), "
          f"coverage drill key {'found' if fixtures['coverage_suite_key'] else 'none'}")
    print(f"Principals  : {len(pool)} ({'tokens file' if args.tokens_file else 'single auto/env token'})")
    print(f"Matrix      : concurrency {levels} x cache {modes} x {args.iterations} req/worker")
    print(f"Metrics     : {args.metrics_url or 'not scraped'}")
    if len(pool) < max(levels):
        print(f"  NOTE: {len(pool)} principal(s) for up to {max(levels)} workers -- the per-principal "
              "limits (120/min chart-data, 60/min heatmap & co) will show up as 429s.")
    print()

    counters: dict[str, Iterator[int]] = {
        sc.operation: itertools.count(1 + args.cold_start) for sc in scenarios
    }
    run_started = datetime.now(timezone.utc).isoformat()
    first_scrape: Optional[dict] = None
    last_scrape: Optional[dict] = None
    results: list[VizCellResult] = []

    async def scrape() -> Optional[dict]:
        nonlocal first_scrape, last_scrape
        if not args.metrics_url:
            return None
        try:
            parsed = await scrape_metrics(args.metrics_url)
        except Exception as exc:  # noqa: BLE001 - the cell still runs, unscraped
            print(f"  (metrics scrape failed: {exc})")
            return None
        if first_scrape is None:
            first_scrape = parsed
        last_scrape = parsed
        return parsed

    for sc in scenarios:
        if sc.kind == "read":
            cells = [(lvl, mode) for lvl in levels for mode in modes]
        else:
            cells = [(1, "-")]
        for level, mode in cells:
            label = f"{sc.operation} [{mode} c={level}]"
            print(f"  {label:58s} ", end="", flush=True)
            before = await scrape()
            if sc.kind == "read":
                cell = await run_viz_cell(
                    args.base_url, pool, fixtures, sc, cache=mode, concurrency=level,
                    iterations=args.iterations, warmup=args.warmup,
                    key_counter=counters[sc.operation],
                )
            else:
                cell = await run_export_cell(
                    args.base_url, pool, fixtures, sc, runs=args.export_runs,
                    poll_interval=args.export_poll_interval, timeout=args.export_timeout,
                )
            after = await scrape()
            if before is not None and after is not None:
                cell.server = diff_analytics_metrics(before, after)
            results.append(cell)
            if cell.skipped:
                print(f"skipped: {cell.skipped}")
            else:
                srv = cell.server_route or {}
                print(
                    f"p50={cell.p50:>7.1f} p95={cell.p95:>7.1f} max={cell.max:>7.1f} "
                    f"srv_p95={srv.get('p95_ms', '-')!s:>6} rps={cell.rps:>6.1f} "
                    f"[{_fmt_statuses(cell.status_counts)}] {cell.verdict()}"
                )
            if args.cell_pause:
                await asyncio.sleep(args.cell_pause)

    burst: Optional[BurstResult] = None
    if run_burst:
        print(f"  {RATE_LIMIT_BURST:58s} ", end="", flush=True)
        burst = await run_rate_limit_burst(
            args.base_url, pool.first(), fixtures,
            requests=args.burst_requests, window_s=args.burst_window,
        )
        print(f"sent={burst.sent} 429s={burst.rate_limited} retry_after={burst.retry_after_present} "
              f"codes={burst.codes or '-'}")

    report = {
        "suite": "viz",
        "timestamp": run_started,
        "base_url": args.base_url,
        "metrics_url": args.metrics_url,
        "project": {
            "id": fixtures["project_id"],
            "slug": fixtures["project_slug"],
            "name": fixtures["project_name"],
            "releases": len(fixtures["release_ids"]),
            "suites": len(fixtures["suite_names"]),
        },
        "principals": len(pool),
        "concurrency_levels": levels,
        "cache_modes": modes,
        "iterations_per_worker": args.iterations,
        "results": [r.to_dict() for r in results],
        "rate_limit_burst": burst.to_dict() if burst else None,
        "server_totals": (
            diff_analytics_metrics(first_scrape, last_scrape)
            if first_scrape is not None and last_scrape is not None else None
        ),
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nResults saved to {args.output}")
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_viz_markdown(report))
        print(f"Markdown table saved to {args.markdown}")

    if args.check_budgets:
        failed = [r for r in results if r.verdict() == "FAIL"]
        checked = [r for r in results if r.verdict() in ("PASS", "FAIL")]
        print("\n── Viz budget compliance ──────────────────────────────────────")
        for r in checked:
            print(f"  {r.operation:40s} {r.cache:4s} c={r.concurrency:<3d} p95={r.p95:>7.1f}ms "
                  f"(budget {r.budget_key} {r.budget_p95_ms}ms) → {r.verdict()}")
        if not checked:
            print("\nERROR: --check-budgets evaluated 0 cells.")
            return 1
        if failed:
            print(f"\n{len(failed)} budget violation(s) of {len(checked)} checked.")
            return 1
        print(f"\nAll budgets met ({len(checked)} cells checked).")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────

async def cmd_bench(args: argparse.Namespace) -> int:
    if getattr(args, "suite", "default") == "viz":
        return await cmd_bench_viz(args)
    token = os.getenv("TESTLOOKUP_BENCHMARK_ACCESS_TOKEN")
    if not token:
        token = await fetch_dev_token(args.base_url)
    if not token:
        print("ERROR: no token. Set TESTLOOKUP_BENCHMARK_ACCESS_TOKEN or start the server in development mode with DEV_AUTO_LOGIN_ENABLED=true.", file=sys.stderr)
        return 2

    try:
        fixtures = await fetch_fixtures(args.base_url, token)
    except Exception as exc:
        print(f"ERROR: could not fetch fixtures — {exc}", file=sys.stderr)
        return 2

    print(f"Benchmarking {args.base_url}")
    print(f"  Concurrency : {args.concurrency} workers")
    print(f"  Iterations  : {args.iterations} per worker ({args.iterations * args.concurrency} total/scenario)")
    print(f"  Scenarios   : {len(SCENARIOS)}")
    print(f"  Fixtures    : {len(fixtures['project_ids'])} project(s), {len(fixtures['run_ids'])} run(s)")
    print()

    active_scenarios = [
        sc for sc in SCENARIOS
        if not (args.skip_env_dependent and sc.env_dependent)
    ]
    results: list[BenchmarkResult] = []
    for sc in active_scenarios:
        print(f"  {sc.operation:25s} ", end="", flush=True)
        result = await run_scenario(
            args.base_url, token, fixtures, sc,
            iterations=args.iterations,
            concurrency=args.concurrency,
            warmup=args.warmup,
        )
        results.append(result)
        status = ""
        if sc.budget_p95_ms and result.p95 > sc.budget_p95_ms:
            status = f"  ⚠ p95 exceeds budget ({sc.budget_p95_ms}ms)"
        print(
            f"p50={result.p50:>6.1f}ms  p95={result.p95:>6.1f}ms  "
            f"p99={result.p99:>6.1f}ms  rps={result.rps:>6.1f}  "
            f"errors={result.errors:>3}{status}"
        )

    if args.output:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "iterations_per_worker": args.iterations,
            "results": [
                {
                    "operation": r.operation,
                    "method": r.method,
                    "path_sample": r.path_sample,
                    "iterations": r.iterations,
                    "concurrency": r.concurrency,
                    "p50_ms": r.p50,
                    "p95_ms": r.p95,
                    "p99_ms": r.p99,
                    "mean_ms": r.mean,
                    "rps": r.rps,
                    "errors": r.errors,
                }
                for r in results
            ],
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nResults saved to {args.output}")

    # Budget check
    if args.check_budgets:
        print("\n── Budget compliance ──────────────────────────────────────────")
        budget_violations = 0
        evaluated = 0
        covered_throughput_ops: set[str] = set()
        for sc, result in zip(active_scenarios, results):
            if sc.budget_p95_ms:
                evaluated += 1
                ok = result.p95 <= sc.budget_p95_ms and result.errors == 0
                label = "PASS" if ok else "FAIL"
                if not ok:
                    budget_violations += 1
                print(
                    f"  {sc.operation:25s} p95={result.p95:>6.1f}ms "
                    f"(budget {sc.budget_p95_ms}ms) errors={result.errors}  → {label}"
                )
            # Throughput is checked separately: a scenario can be comfortably
            # inside its latency budget and still fail to sustain the required
            # rate, which is exactly what the pg-CPU regression looked like.
            min_rps = _throughput_budget(sc.throughput_op) if sc.throughput_op else 0.0
            if min_rps:
                covered_throughput_ops.add(sc.throughput_op)
                evaluated += 1
                ok = result.rps >= min_rps and result.errors == 0
                label = "PASS" if ok else "FAIL"
                if not ok:
                    budget_violations += 1
                print(
                    f"  {sc.operation:25s} rps={result.rps:>6.1f}   "
                    f"(budget {min_rps}/s as '{sc.throughput_op}' "
                    f"@ concurrency {args.concurrency}) → {label}"
                )

        # Name the throughput budgets nothing exercised. A budget no scenario
        # drives is not a pass — it is an unchecked expectation, and silence
        # about it is what let the latency gate report success while inert.
        uncovered = [
            b for b in _all_throughput_budgets()
            if b.operation not in covered_throughput_ops
        ]
        if uncovered:
            print("")
            print("  Not exercised by this harness (unchecked, not passing):")
            for b in uncovered:
                print(f"    {b.operation:25s} budget {b.min_rps}/s — {b.description}")

        if evaluated == 0:
            # A gate that could not evaluate anything must NOT report success.
            # This is what made the previous version useless: it printed
            # "All budgets met" while checking nothing at all.
            print(
                "\nERROR: --check-budgets evaluated 0 scenarios — no budgets "
                "resolved, so nothing was checked."
            )
            if _BUDGET_IMPORT_ERROR:
                print(f"       budget import failed: {_BUDGET_IMPORT_ERROR}")
            return 1
        if budget_violations:
            print(f"\n{budget_violations} budget violation(s) of {evaluated} checked.")
            return 1
        print(f"\nAll budgets met ({evaluated} scenarios checked).")


    return 0


def _use_utf8_stdio() -> None:
    """Windows consoles default to cp1252, which cannot encode this script's
    output (box-drawing ``─`` in headers, ``→`` in budget lines).

    Without this the harness crashed *after* completing every measurement but
    *before* reporting budget compliance or writing --output, and exited 1 --
    indistinguishable from a real budget breach. The expensive part had already
    succeeded; only the reporting failed.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - never let logging setup kill a run
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TestLookup concurrent load test harness")
    sub = parser.add_subparsers(dest="command")

    bench = sub.add_parser("bench", help="Run concurrent benchmarks")
    bench.add_argument("--base-url", default="http://localhost:8000")
    bench.add_argument("--iterations", type=int, default=10, help="Requests per worker per scenario")
    bench.add_argument("--concurrency", type=int, default=1, help="Number of parallel workers")
    bench.add_argument("--warmup", type=int, default=2, help="Warmup requests per worker before the timed run")
    bench.add_argument("--check-budgets", action="store_true", help="Fail with exit code 1 if p95 exceeds budget")
    bench.add_argument("--skip-env-dependent", action="store_true", help="Skip scenarios whose latency depends on optional deps (Ollama, ChromaDB)")
    bench.add_argument("--output", type=str, default=None, help="Write results to a JSON file")
    # ── --suite viz (Phase D L1). Ignored by the default suite. ──
    bench.add_argument("--suite", choices=["default", "viz"], default="default",
                       help="default: SCENARIOS (the live pytest smoke set). viz: VIZ_SCENARIOS")
    bench.add_argument("--project-slug", default=DEFAULT_VIZ_PROJECT_SLUG,
                       help="viz: the project to read (seed_large_dataset.py's slug by default)")
    bench.add_argument("--cache", choices=["cold", "warm", "both"], default="both",
                       help="viz: cold walks distinct cache keys; warm repeats one key")
    bench.add_argument("--concurrency-levels", type=str, default=None,
                       help="viz: comma-separated worker counts, e.g. 1,4,8,16 (default: --concurrency)")
    bench.add_argument("--cold-start", type=int, default=0,
                       help="viz: first cold key index, so a second run does not re-ask the first run's keys")
    bench.add_argument("--tokens-file", type=str, default=None,
                       help="viz: one bearer token per line; requests round-robin across them "
                            "(default: TESTLOOKUP_BENCHMARK_ACCESS_TOKEN, then dev-login)")
    bench.add_argument("--metrics-url", type=str, default=None,
                       help="viz: the backend's Prometheus /metrics URL, scraped before and after each cell")
    bench.add_argument("--markdown", type=str, default=None, help="viz: write the results table as Markdown")
    bench.add_argument("--only", type=str, default=None,
                       help=f"viz: comma-separated scenario names (and/or {RATE_LIMIT_BURST})")
    bench.add_argument("--skip-burst", action="store_true", help=f"viz: do not run {RATE_LIMIT_BURST}")
    bench.add_argument("--burst-requests", type=int, default=150, help="viz: requests in the burst")
    bench.add_argument("--burst-window", type=float, default=60.0, help="viz: seconds the burst may take")
    bench.add_argument("--export-runs", type=int, default=2, help="viz: runs per export flow (sequential)")
    bench.add_argument("--export-format", choices=["pdf", "xlsx"], default="pdf")
    bench.add_argument("--export-poll-interval", type=float, default=1.0)
    bench.add_argument("--export-timeout", type=float, default=300.0,
                       help="viz: seconds before a background export poll gives up")
    bench.add_argument("--cell-pause", type=float, default=0.0,
                       help="viz: seconds to wait between cells (lets per-minute limits roll over)")
    bench.add_argument(OVERRIDE_FLAG, dest="i_know_this_is_not_the_shared_dev_stack", action="store_true",
                       help="viz: allow a localhost:8000 target (refused by default: the shared dev stack)")

    return parser


def main() -> None:
    _use_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    if args.command != "bench":
        parser.print_help()
        sys.exit(0)

    exit_code = asyncio.run(cmd_bench(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
