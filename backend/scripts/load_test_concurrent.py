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
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

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
        "ILIKE search — uses pg_trgm indexes from wave #5 fix",
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


# ── CLI ─────────────────────────────────────────────────────────────────────

async def cmd_bench(args: argparse.Namespace) -> int:
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


def main() -> None:
    _use_utf8_stdio()
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

    args = parser.parse_args()
    if args.command != "bench":
        parser.print_help()
        sys.exit(0)

    exit_code = asyncio.run(cmd_bench(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
