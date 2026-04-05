#!/usr/bin/env python3
"""
Load Test Harness — synthetic data generation and endpoint benchmarking (OPS-03-2).

Usage:
    python scripts/load_test_harness.py generate --runs 100 --tests-per-run 500
    TESTLOOKUP_BENCHMARK_ACCESS_TOKEN=<jwt> python scripts/load_test_harness.py benchmark --base-url http://localhost:8000
    python scripts/load_test_harness.py report

Requires: httpx (already in requirements.txt)
"""
import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone


def generate_synthetic_test_cases(count: int) -> list[dict]:
    """Generate synthetic test case data for load testing."""
    categories = ["PRODUCT_BUG", "INFRASTRUCTURE", "FLAKY", "AUTOMATION_DEFECT", "TEST_DATA"]
    statuses = ["PASSED", "PASSED", "PASSED", "PASSED", "FAILED", "FAILED", "BROKEN", "SKIPPED"]
    suites = [f"suite-{i}" for i in range(20)]
    cases = []
    for i in range(count):
        cases.append({
            "test_name": f"test_{i:06d}_{'_'.join(uuid.uuid4().hex[:8] for _ in range(2))}",
            "suite_name": suites[i % len(suites)],
            "status": statuses[i % len(statuses)],
            "duration_ms": 100 + (i * 7) % 5000,
            "failure_category": categories[i % len(categories)] if statuses[i % len(statuses)] in ("FAILED", "BROKEN") else None,
            "error_message": f"Error in test {i}: Connection timeout after {1000 + i}ms" if statuses[i % len(statuses)] == "FAILED" else None,
        })
    return cases


def generate_synthetic_run(project_id: str, tests_per_run: int) -> dict:
    """Generate a synthetic test run payload."""
    cases = generate_synthetic_test_cases(tests_per_run)
    passed = sum(1 for c in cases if c["status"] == "PASSED")
    failed = sum(1 for c in cases if c["status"] == "FAILED")
    return {
        "project_id": project_id,
        "build_number": f"build-{uuid.uuid4().hex[:8]}",
        "branch": "main",
        "status": "FAILED" if failed > 0 else "PASSED",
        "total_tests": len(cases),
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": sum(1 for c in cases if c["status"] == "SKIPPED"),
        "broken_tests": sum(1 for c in cases if c["status"] == "BROKEN"),
        "pass_rate": round(passed / len(cases) * 100, 1) if cases else 0,
        "test_cases": cases,
    }


def benchmark_endpoint(base_url: str, access_token: str, method: str, path: str,
                       iterations: int = 10, payload: dict | None = None) -> dict:
    """Benchmark a single endpoint and return latency statistics."""
    import httpx

    headers = {"Authorization": f"Bearer {access_token}"}
    latencies: list[float] = []

    for _ in range(iterations):
        start = time.monotonic()
        try:
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                if method == "GET":
                    client.get(path, headers=headers)
                else:
                    client.post(path, headers=headers, json=payload)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)
        except Exception as exc:
            latencies.append(-1)
            print(f"  ERROR on {path}: {exc}")

    valid = [lat for lat in latencies if lat >= 0]
    if not valid:
        return {"path": path, "error": "all requests failed"}

    valid.sort()
    return {
        "path": path,
        "method": method,
        "iterations": iterations,
        "p50_ms": round(valid[len(valid) // 2], 1),
        "p95_ms": round(valid[int(len(valid) * 0.95)], 1) if len(valid) >= 2 else round(valid[-1], 1),
        "p99_ms": round(valid[int(len(valid) * 0.99)], 1) if len(valid) >= 2 else round(valid[-1], 1),
        "mean_ms": round(statistics.mean(valid), 1),
        "min_ms": round(min(valid), 1),
        "max_ms": round(max(valid), 1),
        "errors": sum(1 for lat in latencies if lat < 0),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

BENCHMARK_ENDPOINTS = [
    ("GET", "/api/v1/projects", "run_list"),
    ("GET", "/api/v1/search?q=test&search_type=keyword&size=20", "keyword_search"),
    ("GET", "/api/v1/search?q=timeout+error&search_type=semantic&size=20", "semantic_search"),
    ("GET", "/api/v1/search?q=timeout+error&search_type=hybrid&size=20", "hybrid_search"),
    ("GET", "/api/v1/search/index-status", "search_index_status"),
    ("GET", "/health/details", "health_details"),
]


def cmd_generate(args: argparse.Namespace) -> None:
    """Generate synthetic test data JSON files."""
    project_id = args.project_id or str(uuid.uuid4())
    runs = []
    for i in range(args.runs):
        run = generate_synthetic_run(project_id, args.tests_per_run)
        runs.append(run)
        if (i + 1) % 10 == 0:
            print(f"Generated {i + 1}/{args.runs} runs")

    output = args.output or f"synthetic_data_{args.runs}runs.json"
    with open(output, "w") as f:
        json.dump({"project_id": project_id, "runs": runs}, f)
    total_tests = sum(r["total_tests"] for r in runs)
    print(f"Generated {len(runs)} runs with {total_tests} total test cases → {output}")


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Run endpoint benchmarks."""
    access_token = os.getenv("TESTLOOKUP_BENCHMARK_ACCESS_TOKEN")
    if not access_token:
        print("ERROR: TESTLOOKUP_BENCHMARK_ACCESS_TOKEN environment variable is required")
        sys.exit(1)

    print(f"Benchmarking {args.base_url} with {args.iterations} iterations per endpoint...")
    results = []
    for method, path, operation in BENCHMARK_ENDPOINTS:
        print(f"  {method} {path} ...", end=" ", flush=True)
        result = benchmark_endpoint(args.base_url, access_token, method, path, args.iterations)
        result["operation"] = operation
        results.append(result)
        if "error" in result:
            print(f"FAILED: {result['error']}")
        else:
            print(f"p50={result['p50_ms']}ms p95={result['p95_ms']}ms p99={result['p99_ms']}ms")

    # Check against budgets
    from app.services.performance_budgets import LATENCY_BUDGETS

    print("\n--- Budget Compliance ---")
    budget_map = {b.operation: b for b in LATENCY_BUDGETS}
    violations = 0
    for result in results:
        op = result.get("operation", "")
        budget = budget_map.get(op)
        if not budget or "error" in result:
            continue
        p95 = result["p95_ms"]
        status = "PASS" if p95 <= budget.p95_ms else "FAIL"
        if status == "FAIL":
            violations += 1
        print(f"  {op}: p95={p95}ms (budget: {budget.p95_ms}ms) → {status}")

    if violations:
        print(f"\n{violations} budget violation(s) detected!")
    else:
        print("\nAll budgets met.")

    output = args.output or "benchmark_results.json"
    with open(output, "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=2)
    print(f"Results saved to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TestLookup Load Test Harness")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Generate synthetic test data")
    gen.add_argument("--runs", type=int, default=10)
    gen.add_argument("--tests-per-run", type=int, default=100)
    gen.add_argument("--project-id", type=str, default=None)
    gen.add_argument("--output", type=str, default=None)

    bench = sub.add_parser("benchmark", help="Benchmark API endpoints")
    bench.add_argument("--base-url", type=str, default="http://localhost:8000")
    bench.add_argument("--iterations", type=int, default=10)
    bench.add_argument("--output", type=str, default=None)

    args = parser.parse_args()
    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
