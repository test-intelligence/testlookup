"""Standalone live-streaming script — no pytest, no test framework.

Use this shape when you have a custom runner (Robot Framework, behave, a
homegrown harness, or just a shell loop) and want to stream results to
TestLookup. The async context manager handles batching, retry, and the
final run_complete event automatically.

Install (one of):

    # From the repo (local dev) — pyproject.toml lives in client/
    pip install -e ./client

    # From PyPI (once published)
    pip install testlookup-reporter

Run:

    export TESTLOOKUP_API_KEY=qai_...               # or in testlookup.properties
    export TESTLOOKUP_ENDPOINT=https://testlookup.local

    python livestream_script.py

Or fully programmatic (no env vars / properties file):

    python livestream_script.py --base-url https://testlookup.local \\
                                --api-key qai_... \\
                                --run-id ci-build-42 \\
                                --launch "Nightly E2E"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Run from the script's own directory so ConfigLoader picks up the
# testlookup.properties shipped alongside this file. Without this, invoking
# `python client/examples/python/livestream_script.py` from the repo root
# would search the repo root for a properties file that isn't there.
os.chdir(Path(__file__).resolve().parent)

from testlookup_reporter import LiveStream  # noqa: E402 — chdir must run first


# ── A toy "test framework" ────────────────────────────────────────────────────
# Replace this with whatever runs your tests. The takeaway is that the
# integration with TestLookup is a single context manager + ``s.record(...)``
# per test result — no per-test TestLookup imports.

@dataclass
class TestCase:
    name: str
    suite: str
    func: Callable[[], bool]   # returns True on pass, raises on failure


def passing(): return True
def failing(): raise AssertionError("expected 200, got 500")
def slow_passing():
    time.sleep(0.2)
    return True


SUITE: list[TestCase] = [
    TestCase("login_smoke",     "auth",     passing),
    TestCase("logout_smoke",    "auth",     passing),
    TestCase("checkout_smoke",  "payments", failing),
    TestCase("dashboard_load",  "ui",       slow_passing),
]


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_suite(stream: LiveStream) -> None:
    for tc in SUITE:
        started = time.perf_counter()
        try:
            tc.func()
            duration_ms = int((time.perf_counter() - started) * 1_000)
            await stream.record(tc.name, "PASSED", duration_ms, suite_name=tc.suite)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1_000)
            await stream.record(
                tc.name, "FAILED", duration_ms,
                suite_name=tc.suite,
                error=f"{type(exc).__name__}: {exc}",
                stack_trace=__import__("traceback").format_exc(),
            )


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    # Anything not passed on the CLI is picked up from testlookup.properties or
    # the canonical TESTLOOKUP_* env vars. ``run_id`` is the only required
    # piece of state the SDK can't infer — pick something stable per build.
    run_id = args.run_id or f"local-{uuid.uuid4().hex[:8]}"

    if args.insecure:
        print("⚠  TLS verification disabled (--insecure). Use only against trusted endpoints.")

    # Pass verify_ssl=False ONLY when --insecure is set. When the flag is
    # omitted the SDK resolves TLS preferences from testlookup.properties
    # (testlookup.ca_cert_path / testlookup.insecure) or env vars
    # (TESTLOOKUP_CA_CERT / TESTLOOKUP_INSECURE), so users don't have to
    # remember the flag every time they run against a homelab.
    livestream_kwargs: dict = dict(
        base_url=args.base_url,
        api_key=args.api_key,
        run_id=run_id,
        launch_name=args.launch,
        build_number=args.build,
        branch=args.branch,
        framework="custom",
        total_tests=len(SUITE),
    )
    if args.insecure:
        livestream_kwargs["verify_ssl"] = False

    async with LiveStream(**livestream_kwargs) as stream:
        await run_suite(stream)
        # Context-manager exit posts run_complete automatically — the run
        # finalises server-side and shows up in Runs / Overview / etc.

    print(f"Done. View at {args.base_url or os.environ.get('TESTLOOKUP_ENDPOINT', '')}/runs")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url",  default=None, help="TestLookup endpoint")
    p.add_argument("--api-key",   default=None, help="Project-scoped API key with stream:write")
    p.add_argument("--run-id",    default=None, help="Stable run identifier (default: local-<rand>)")
    p.add_argument("--launch",    default=None, help="Launch label shown in the UI")
    p.add_argument("--build",     default=None, help="CI build number")
    p.add_argument("--branch",    default=None, help="Git branch")
    p.add_argument(
        "--insecure", "-k", action="store_true",
        help="Skip TLS certificate verification. Use this against homelab / dev "
             "instances that serve a self-signed cert. Never in production.",
    )
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
