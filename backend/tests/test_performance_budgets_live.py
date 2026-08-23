"""
Performance budget smoke test — live.

Runs the concurrent load harness against a live server (default
``http://localhost:8000``) and asserts that every scenario comes in
under its documented p95 budget. Skipped unless the backend is
reachable, so this is safe to leave in the default pytest run.

When to run:
  * Locally after ``make dev``: ``make test-backend`` picks this up
    automatically.
  * In CI after the docker-compose integration job brings the app up.
  * As a pre-release gate to catch regressions in the hot paths we
    tightened through waves 1–4 (authz guards, shared HTTP pool,
    dedicated-write-session cache populates, trigram indexes).

What this test does NOT do:
  * It is **not** a full load test. Use ``scripts/load_test_concurrent.py``
    with higher concurrency for real capacity testing.
  * It does **not** cover environment-dependent endpoints (``/health/details``
    depends on Ollama/Chroma availability; see ``env_dependent`` on the
    scenario list).
  * It runs with warmup so it measures **steady-state** latency. Cold-start
    behaviour is covered by the scenario-specific cache populate paths we
    wrote in items #2 and #4 — those are unit-tested separately.

Override via env vars:
  * ``LOAD_TEST_BASE_URL`` — target server (default ``http://localhost:8000``)
  * ``LOAD_TEST_ITERATIONS`` — timed requests per scenario (default ``15``)
  * ``LOAD_TEST_WARMUP`` — warmup requests per scenario (default ``3``)
  * ``LOAD_TEST_CONCURRENCY`` — parallel workers (default ``1``)
  * ``TESTLOOKUP_BENCHMARK_ACCESS_TOKEN`` — bypass dev-login auto-fetch
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

# Make the harness importable — scripts/ is not a normal package.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from load_test_concurrent import (  # noqa: E402  (after sys.path insert)
    SCENARIOS,
    fetch_dev_token,
    fetch_fixtures,
    run_scenario,
)


BASE_URL = os.getenv("LOAD_TEST_BASE_URL", "http://localhost:8000")
ITERATIONS = int(os.getenv("LOAD_TEST_ITERATIONS", "15"))
WARMUP = int(os.getenv("LOAD_TEST_WARMUP", "3"))
CONCURRENCY = int(os.getenv("LOAD_TEST_CONCURRENCY", "1"))


async def _server_reachable() -> bool:
    """Quick liveness probe so the suite skips cleanly when nothing is up."""
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=2.0) as client:
            resp = await client.get("/health/live")
            return resp.status_code == 200
    except Exception:
        return False


def _skip_if_unreachable() -> None:
    """Pytest-style skip wrapper — called at fixture setup time."""
    if not asyncio.run(_server_reachable()):
        pytest.skip(
            f"Load-test target {BASE_URL} not reachable — "
            "start the stack with `make dev` to run this suite."
        )


# ── Pytest parametrize over scenarios ───────────────────────────────────────


_ACTIVE_SCENARIOS = [sc for sc in SCENARIOS if not sc.env_dependent and sc.budget_p95_ms]


@pytest.fixture
def _harness_context():
    """One-time setup: reachability check + token + fixtures shared across
    every parametrized run."""
    _skip_if_unreachable()

    async def _setup():
        token = os.getenv("TESTLOOKUP_BENCHMARK_ACCESS_TOKEN")
        if not token:
            token = await fetch_dev_token(BASE_URL)
        if not token:
            pytest.skip(
                "Could not fetch a JWT — set TESTLOOKUP_BENCHMARK_ACCESS_TOKEN "
                "or start the server in development mode with DEV_AUTO_LOGIN_ENABLED=true."
            )
        try:
            fixtures = await fetch_fixtures(BASE_URL, token)
        except RuntimeError as exc:
            # A stack that is UP but EMPTY is not a budget regression — there is
            # simply nothing to benchmark. ``_skip_if_unreachable`` above only
            # answers "is anything listening"; seeded data is a *separate*
            # precondition and needs its own skip. Without this, every developer
            # running `make dev` without `make seed-data` gets 7 ERRORs that say
            # nothing about performance. Narrow on purpose: any other RuntimeError
            # still propagates, because "we could not look" and "the budget was
            # missed" must not render identically.
            if "seeded data" not in str(exc):
                raise
            pytest.skip(f"{exc}")
        return token, fixtures

    token, fixtures = asyncio.run(_setup())
    return {"token": token, "fixtures": fixtures}


@pytest.mark.parametrize(
    "scenario",
    _ACTIVE_SCENARIOS,
    ids=[sc.operation for sc in _ACTIVE_SCENARIOS],
)
def test_scenario_meets_p95_budget(scenario, _harness_context) -> None:
    """Each scenario's p95 must come in under its documented budget with
    zero errors, measured in **steady state** (after warmup)."""
    result = asyncio.run(
        run_scenario(
            BASE_URL,
            _harness_context["token"],
            _harness_context["fixtures"],
            scenario,
            iterations=ITERATIONS,
            concurrency=CONCURRENCY,
            warmup=WARMUP,
        )
    )
    # Zero tolerance for server errors — these indicate correctness regressions,
    # not capacity issues.
    assert result.errors == 0, (
        f"{scenario.operation}: {result.errors} request(s) failed — "
        f"sample path={result.path_sample}"
    )
    assert result.p95 <= scenario.budget_p95_ms, (
        f"{scenario.operation}: p95={result.p95}ms exceeds budget "
        f"{scenario.budget_p95_ms}ms (p50={result.p50}ms, p99={result.p99}ms, "
        f"{result.iterations} requests). Either investigate the regression "
        f"or update the budget on the scenario if the new baseline is "
        f"deliberate."
    )
