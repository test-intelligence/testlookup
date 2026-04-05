#!/usr/bin/env python3
"""
simulate_live_stream.py — End-to-end smoke test for the live streaming pipeline.

What it does
------------
1. Authenticates with the TestLookup server.
2. Picks the first available project (or creates one if none exist).
3. Simulates 3 concurrent test runners streaming results in parallel:
     • Runner A — 20 fast unit tests  (high pass rate)
     • Runner B — 15 integration tests (mixed results)
     • Runner C — 10 slow E2E tests   (some failures)
4. Prints a live progress table while events flow.
5. Waits for all runners to complete and prints a final summary.
6. Polls GET /api/v1/stream/active to confirm the server received the data.

Usage
-----
    # From the repo root (no extra installs needed — uses httpx already in requirements.txt)
    python scripts/simulate_live_stream.py

    # Override defaults via env vars or CLI flags
    TESTLOOKUP_PASSWORD=<your-password> python scripts/simulate_live_stream.py \\
        --url   http://localhost:3000 \\
        --user  admin \\
        --project-id <uuid>      # optional — auto-discovered if omitted

Requirements
------------
    pip install httpx   # already in backend/requirements.txt
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Allow running from anywhere without installing the package
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))
from testlookup_reporter import TestLookupReporter  # noqa: E402

import httpx  # noqa: E402

# ── ANSI colours ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def ok(msg: str)   -> str: return f"{GREEN}✔{RESET}  {msg}"
def err(msg: str)  -> str: return f"{RED}✗{RESET}  {msg}"
def info(msg: str) -> str: return f"{CYAN}ℹ{RESET}  {msg}"
def warn(msg: str) -> str: return f"{YELLOW}⚠{RESET}  {msg}"


ROLE_BY_USERNAME: dict[str, str] = {
    "admin": "admin",
    "qa_lead": "qa_lead",
    "qa_engineer": "qa_engineer",
    "tester": "tester",
    "viewer": "viewer",
}

# ── Simulated test suites ────────────────────────────────────────────────────

@dataclass
class SimTest:
    name: str
    suite: str
    duration_ms: int
    pass_prob: float          # probability of PASSED
    flaky: bool = False       # if True, occasionally reports SKIPPED

SUITES: dict[str, list[SimTest]] = {
    "Runner-A (unit)": [
        SimTest("test_user_create",           "auth",        45,  0.98),
        SimTest("test_user_login_valid",       "auth",        32,  0.99),
        SimTest("test_user_login_bad_pass",    "auth",        28,  0.97),
        SimTest("test_token_refresh",          "auth",        55,  0.96),
        SimTest("test_project_create",         "projects",    60,  0.95),
        SimTest("test_project_list",           "projects",    40,  0.99),
        SimTest("test_project_delete",         "projects",    38,  0.94),
        SimTest("test_run_ingest",             "runs",       120,  0.90),
        SimTest("test_run_list",               "runs",        48,  0.99),
        SimTest("test_test_case_status",       "runs",        35,  0.97),
        SimTest("test_metrics_pass_rate",      "metrics",     52,  0.98),
        SimTest("test_metrics_flaky_detect",   "metrics",     88,  0.85, flaky=True),
        SimTest("test_search_basic",           "search",      75,  0.96),
        SimTest("test_search_filters",         "search",      80,  0.93),
        SimTest("test_search_pagination",      "search",      65,  0.97),
        SimTest("test_analytics_summary",      "analytics",   95,  0.92),
        SimTest("test_analytics_coverage",     "analytics",  110,  0.91),
        SimTest("test_settings_update",        "settings",    42,  0.99),
        SimTest("test_notification_send",      "notify",     130,  0.88),
        SimTest("test_health_endpoint",        "health",       8,  1.00),
    ],
    "Runner-B (integration)": [
        SimTest("test_ingest_allure_report",   "ingestion",  450,  0.88),
        SimTest("test_ingest_testng_report",   "ingestion",  380,  0.85),
        SimTest("test_ingest_junit_report",    "ingestion",  310,  0.90),
        SimTest("test_webhook_minio",          "webhooks",   520,  0.80),
        SimTest("test_celery_ingest_task",     "celery",     650,  0.82),
        SimTest("test_redis_stream_publish",   "streams",    180,  0.95),
        SimTest("test_redis_stream_consume",   "streams",    220,  0.93),
        SimTest("test_postgres_test_run_row",  "db",         140,  0.96),
        SimTest("test_postgres_test_case_row", "db",         160,  0.94),
        SimTest("test_mongo_raw_payload",      "db",         190,  0.91),
        SimTest("test_minio_upload",           "storage",    320,  0.87, flaky=True),
        SimTest("test_minio_download",         "storage",    280,  0.89, flaky=True),
        SimTest("test_full_run_lifecycle",     "e2e",        980,  0.78),
        SimTest("test_jira_ticket_create",     "jira",       600,  0.75),
        SimTest("test_quality_gate_pass",      "gates",      350,  0.86),
    ],
    "Runner-C (e2e)": [
        SimTest("test_dashboard_load",         "ui",         800,  0.92),
        SimTest("test_login_flow",             "ui",         920,  0.95),
        SimTest("test_project_create_flow",    "ui",        1100,  0.88),
        SimTest("test_run_upload_flow",        "ui",        1800,  0.78),
        SimTest("test_live_streaming_flow",    "ui",        2200,  0.70, flaky=True),
        SimTest("test_ai_analysis_trigger",    "ai",        3500,  0.65),
        SimTest("test_release_gate_flow",      "ui",        1600,  0.82),
        SimTest("test_chat_agent_response",    "ai",        2800,  0.72),
        SimTest("test_defects_jira_sync",      "jira",      2100,  0.68),
        SimTest("test_full_cicd_simulation",   "e2e",       4500,  0.60),
    ],
}


def simulate_result(test: SimTest) -> tuple[str, Optional[str], Optional[str]]:
    """Return (status, error_message, stack_trace)."""
    r = random.random()
    if test.flaky and r > 0.95:
        return "SKIPPED", None, None
    if r < test.pass_prob:
        return "PASSED", None, None
    # Randomly pick FAILED or BROKEN
    if random.random() < 0.8:
        error = f"AssertionError: expected 200 OK, got 500 in {test.name}"
        stack = (
            f"Traceback (most recent call last):\n"
            f"  File \"tests/{test.suite}/test_{test.suite}.py\", line {random.randint(10, 200)}, in {test.name}\n"
            f"    assert response.status_code == 200, f\"Got {{response.status_code}}\"\n"
            f"AssertionError: {error}"
        )
        return "FAILED", error, stack
    return "BROKEN", f"RuntimeError: unexpected exception in {test.name}", None


# ── Progress tracking ────────────────────────────────────────────────────────

@dataclass
class RunnerStats:
    name: str
    total: int
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    broken: int = 0
    done: bool = False
    sent: int = 0

    @property
    def completed(self) -> int:
        return self.passed + self.failed + self.skipped + self.broken

    @property
    def pass_rate(self) -> float:
        denom = self.passed + self.failed + self.broken
        return (self.passed / denom * 100) if denom else 0.0

    def bar(self, width: int = 20) -> str:
        if self.total == 0:
            return " " * width
        pct = self.completed / self.total
        filled = int(pct * width)
        return "█" * filled + "░" * (width - filled)


def print_progress(stats_list: list[RunnerStats], elapsed: float) -> None:
    """Overwrite the last N lines with a live progress table."""
    lines = [
        f"\n{BOLD}{'Runner':<26} {'Progress':<22} {'Done':>5} {'Pass%':>6} "
        f"{'✔':>5} {'✗':>5} {'○':>5} {'Sent':>6}{RESET}",
        "─" * 80,
    ]
    for s in stats_list:
        color = GREEN if s.pass_rate >= 85 else (YELLOW if s.pass_rate >= 60 else RED)
        done_marker = f"{GREEN}✔ done{RESET}" if s.done else f"{DIM}running{RESET}"
        lines.append(
            f"  {s.name:<24} [{s.bar()}] "
            f"{s.completed:>3}/{s.total:<3} "
            f"{color}{s.pass_rate:>5.1f}%{RESET} "
            f"{s.passed:>5} {s.failed:>5} {s.skipped:>5} "
            f"{DIM}{s.sent:>6}{RESET}  {done_marker}"
        )
    lines.append(f"\n  {DIM}elapsed: {elapsed:.1f}s{RESET}")

    # Move cursor up by number of printed lines
    output = "\n".join(lines)
    sys.stdout.write(output)
    sys.stdout.flush()

    # Position cursor at start of block for next overwrite
    n_lines = output.count("\n") + 1
    sys.stdout.write(f"\033[{n_lines}A\r")


def print_final(stats_list: list[RunnerStats], elapsed: float) -> None:
    """Print the final summary without cursor tricks."""
    # Move past the progress area
    sys.stdout.write("\n" * 8)
    print(f"\n{BOLD}{'─' * 80}{RESET}")
    print(f"{BOLD}  Final Summary  ({elapsed:.1f}s total){RESET}")
    print(f"{'─' * 80}")
    total_passed = total_failed = total_skipped = total_sent = 0
    for s in stats_list:
        color = GREEN if s.pass_rate >= 85 else (YELLOW if s.pass_rate >= 60 else RED)
        print(
            f"  {s.name:<26} "
            f"{color}{s.pass_rate:>5.1f}% pass{RESET}  "
            f"{GREEN}{s.passed:>3}✔{RESET}  "
            f"{RED}{s.failed:>3}✗{RESET}  "
            f"{YELLOW}{s.skipped:>3}○{RESET}  "
            f"{DIM}{s.sent} events sent{RESET}"
        )
        total_passed  += s.passed
        total_failed  += s.failed
        total_skipped += s.skipped
        total_sent    += s.sent

    overall = (total_passed / (total_passed + total_failed) * 100) if (total_passed + total_failed) else 0
    color = GREEN if overall >= 85 else (YELLOW if overall >= 60 else RED)
    print(f"{'─' * 80}")
    print(
        f"  {'TOTAL':<26} "
        f"{color}{overall:>5.1f}% pass{RESET}  "
        f"{GREEN}{total_passed:>3}✔{RESET}  "
        f"{RED}{total_failed:>3}✗{RESET}  "
        f"{YELLOW}{total_skipped:>3}○{RESET}  "
        f"{DIM}{total_sent} events sent{RESET}"
    )
    print()


# ── Auth + project helpers ───────────────────────────────────────────────────

async def login_with_password(
    http: httpx.AsyncClient, username: str, password: str
) -> Optional[str]:
    """Authenticate with username/password and return a JWT access token."""
    resp = await http.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},  # form-encoded
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        return None
    token = resp.json()["access_token"]
    print(ok(f"Authenticated as {BOLD}{username}{RESET}"))
    return token


def infer_dev_role(username: str) -> str:
    """Map a demo username to the matching dev-login role slug."""
    return ROLE_BY_USERNAME.get(username.strip().lower(), "admin")


async def login_with_existing_dev_user(
    http: httpx.AsyncClient, username: str
) -> Optional[str]:
    """
    Reuse an existing seeded user via the development-only auth endpoint.

    The backend endpoint resolves the first active user for the requested role
    from the database, creating a temporary one only if no seeded user exists.
    """
    role = infer_dev_role(username)
    resp = await http.post(
        f"/api/v1/auth/dev-login?role={quote(role)}&username={quote(username)}"
    )
    if resp.status_code != 200:
        return None
    token = resp.json()["access_token"]
    print(ok(f"Authenticated as {BOLD}{username}{RESET} via existing {role} user"))
    return token


async def authenticate(http: httpx.AsyncClient, username: str, password: str) -> str:
    """
    Get a bearer token for the smoke test.

    Preference order:
    1. Use the provided password if one is supplied.
    2. Fall back to dev-login so demo scripts can reuse seeded users whose
       passwords are randomized at seed time.
    """
    attempted_password_login = False
    if password:
        attempted_password_login = True
        token = await login_with_password(http, username, password)
        if token:
            return token
        print(warn("Password login failed — trying development auto-login for the seeded user role…"))

    token = await login_with_existing_dev_user(http, username)
    if token:
        return token

    if attempted_password_login:
        print(err("Authentication failed. Password login was rejected and development auto-login is unavailable."))
    else:
        print(err("Authentication failed. No password was provided and development auto-login is unavailable."))
    print(info("Provide TESTLOOKUP_PASSWORD for a real account, or enable /api/v1/auth/dev-login in development."))
    sys.exit(1)


async def get_or_create_project(http: httpx.AsyncClient, project_id: Optional[str]) -> dict:
    """Return an existing project dict or create a demo one."""
    if project_id:
        resp = await http.get(f"/api/v1/projects/{project_id}")
        if resp.status_code == 200:
            p = resp.json()
            print(ok(f"Using project: {BOLD}{p['name']}{RESET}  (id={p['id']})"))
            return p
        print(warn(f"Project {project_id} not found — discovering available projects…"))

    resp = await http.get("/api/v1/projects", params={"limit": 1})
    if resp.status_code == 200:
        projects = resp.json()
        items = projects if isinstance(projects, list) else projects.get("items", [])
        if items:
            p = items[0]
            print(ok(f"Using project: {BOLD}{p['name']}{RESET}  (id={p['id']})"))
            return p

    # Create a demo project
    print(info("No projects found — creating demo project…"))
    resp = await http.post(
        "/api/v1/projects",
        json={
            "name": "Live Stream Demo",
            "slug": f"live-demo-{int(time.time())}",
            "description": "Auto-created by simulate_live_stream.py",
        },
    )
    if resp.status_code not in (200, 201):
        print(err(f"Failed to create project: {resp.text}"))
        sys.exit(1)
    p = resp.json()
    print(ok(f"Created project: {BOLD}{p['name']}{RESET}  (id={p['id']})"))
    return p


async def fetch_active_sessions(http: httpx.AsyncClient, project_id: str) -> dict:
    resp = await http.get("/api/v1/stream/active", params={"project_id": project_id})
    if resp.status_code == 200:
        return resp.json()
    return {"count": 0, "sessions": []}


# ── Single runner coroutine ──────────────────────────────────────────────────

async def run_one_runner(
    reporter: TestLookupReporter,
    runner_name: str,
    tests: list[SimTest],
    build_number: str,
    stats: RunnerStats,
    progress_lock: asyncio.Lock,
    stats_list: list[RunnerStats],
    start_time: float,
) -> None:
    """Simulate one test runner streaming events."""
    async with reporter.session(
        build_number=build_number,
        total_tests=len(tests),
    ) as session:
        for test in tests:
            # Simulate test execution time (scaled down for the demo)
            await asyncio.sleep(test.duration_ms / 500)    # ~2× real-time (visible in dashboard)

            status, error, stack = simulate_result(test)

            await session.record(
                test_name=test.name,
                status=status,
                duration_ms=test.duration_ms,
                suite_name=test.suite,
                error=error,
                stack_trace=stack,
                tags=[test.suite, runner_name.split()[0].lower()],
            )

            # Update local stats
            async with progress_lock:
                if status == "PASSED":
                    stats.passed += 1
                elif status == "FAILED":
                    stats.failed += 1
                elif status == "SKIPPED":
                    stats.skipped += 1
                elif status == "BROKEN":
                    stats.broken += 1
                stats.sent = session.stats["sent"]
                print_progress(stats_list, time.time() - start_time)

    # Final sent count after session flush
    async with progress_lock:
        stats.sent = session.stats["sent"]
        stats.done = True
        print_progress(stats_list, time.time() - start_time)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    base_url = args.url.rstrip("/")
    build_number = f"smoke-test-{int(time.time())}"

    print(f"\n{BOLD}TestLookup — Live Stream Smoke Test{RESET}")
    print(f"{'─' * 40}")
    print(info(f"Server  : {base_url}"))
    print(info(f"Build   : {build_number}"))
    print()

    # ── Step 1: authenticate ───────────────────────────────────────────────
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as plain_http:
        token = await authenticate(plain_http, args.username, args.password)

    # ── Step 2: resolve project ────────────────────────────────────────────
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as auth_http:
        project = await get_or_create_project(auth_http, args.project_id)
        project_id = project["id"]

        total_tests = sum(len(t) for t in SUITES.values())
        print(info(f"Streaming {len(SUITES)} runners × {total_tests} tests → {base_url}"))
        print(info(f"Estimated duration: ~{total_tests * 2 // 10 * 10}s (open dashboard NOW)"))
        print(info("Live Execution dashboard:"))
        print(f"  {CYAN}{args.frontend}/live{RESET}")
        print(info("Test Runs (populated after each runner finishes):"))
        print(f"  {CYAN}{args.frontend}/runs{RESET}\n")
        print(f"  {YELLOW}Tip: keep this terminal visible alongside the browser{RESET}\n")

        # ── Step 3: build reporter and stats ──────────────────────────────
        reporters: list[TestLookupReporter] = []
        stats_list: list[RunnerStats] = []
        tasks: list[asyncio.Task] = []
        progress_lock = asyncio.Lock()
        start_time = time.time()

        for runner_name, tests in SUITES.items():
            rep = TestLookupReporter(
                base_url=base_url,
                token=token,
                project_id=project_id,
                client_name=runner_name,
                framework="pytest",
                batch_size=10,           # small batches for demo visibility
                batch_interval_ms=200,
            )
            reporters.append(rep)
            stats = RunnerStats(name=runner_name, total=len(tests))
            stats_list.append(stats)
            tasks.append(
                asyncio.create_task(
                    run_one_runner(
                        reporter=rep,
                        runner_name=runner_name,
                        tests=tests,
                        build_number=build_number,
                        stats=stats,
                        progress_lock=progress_lock,
                        stats_list=stats_list,
                        start_time=start_time,
                    )
                )
            )

        # ── Step 4: run all runners concurrently ──────────────────────────
        print()  # blank line before progress table
        await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start_time
        print_final(stats_list, elapsed)

        # ── Step 5: close all HTTP clients ────────────────────────────────
        for rep in reporters:
            await rep.aclose()

        # ── Step 6: verify server received data ───────────────────────────
        print(info("Verifying server-side session state…"))
        await asyncio.sleep(1)   # allow final flushes to arrive

        await fetch_active_sessions(auth_http, project_id)

        total_sent = sum(s.sent for s in stats_list)
        total_tests = sum(s.total for s in stats_list)

        print()
        if total_sent > 0:
            print(ok(f"{BOLD}{total_sent}{RESET} events delivered to server  "
                     f"({total_tests} test results across {len(SUITES)} runners)"))
        else:
            print(warn("No events confirmed — check server logs"))

        print(ok(f"Dashboard: {CYAN}{args.frontend}/live{RESET}"))
        print(ok(f"Test runs will appear under: {CYAN}{args.frontend}/runs{RESET} once AI pipeline completes"))
        print()


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test the TestLookup live streaming pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("TESTLOOKUP_URL", "http://localhost:8000"),
        help="TestLookup API base URL",
    )
    parser.add_argument(
        "--frontend",
        default=os.environ.get("TESTLOOKUP_FRONTEND", "http://localhost:3000"),
        help="TestLookup frontend URL (for dashboard links in output)",
    )
    parser.add_argument(
        "--user",
        dest="username",
        default=os.environ.get("TESTLOOKUP_USER", "admin"),
        help="Username for authentication",
    )
    parser.add_argument(
        "--pass",
        dest="password",
        default=os.environ.get("TESTLOOKUP_PASSWORD", ""),
        help="Password for authentication",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("TESTLOOKUP_PROJECT_ID", ""),
        help="Target project UUID (auto-discovered if omitted)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible pass/fail outcomes",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted.{RESET}")
        sys.exit(0)
