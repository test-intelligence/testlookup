"""Run multiple suites in parallel with one project-scoped API key.

This pytest example uses the programmatic LiveStream client because it mirrors
CI runners that split suites across workers. Each suite gets a distinct run_id,
but all suites reuse the same TESTLOOKUP_API_KEY or testlookup.api.key value.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

os.chdir(Path(__file__).resolve().parent)

from testlookup_reporter import ConfigLoader, LiveStream  # noqa: E402


@dataclass(frozen=True)
class SimulatedCase:
    name: str
    duration_ms: int
    should_pass: bool = True


SUITES: dict[str, list[SimulatedCase]] = {
    "auth-api": [
        SimulatedCase("login_accepts_valid_credentials", 92),
        SimulatedCase("login_rejects_locked_account", 74),
        SimulatedCase("refresh_token_rotates", 61),
    ],
    "checkout-api": [
        SimulatedCase("cart_total_includes_tax", 113),
        SimulatedCase("expired_coupon_is_rejected", 85),
        SimulatedCase("payment_gateway_timeout_is_retried", 247, should_pass=False),
    ],
    "orders-ui": [
        SimulatedCase("order_history_loads", 174),
        SimulatedCase("order_detail_shows_tracking", 143),
        SimulatedCase("cancel_button_hidden_after_ship", 96),
    ],
    "notifications": [
        SimulatedCase("email_receipt_sent", 126),
        SimulatedCase("sms_opt_out_is_honored", 108),
        SimulatedCase("webhook_signature_is_verified", 88),
    ],
}


def _configured() -> bool:
    cfg = ConfigLoader.load()
    endpoint = ConfigLoader.get(cfg, "server.url")
    api_key = ConfigLoader.get(cfg, "auth.api_key") or os.environ.get("TESTLOOKUP_API_KEY")
    return bool(endpoint and api_key and not str(api_key).endswith("REPLACE_ME"))


async def _run_suite(suite_name: str, cases: list[SimulatedCase], build_id: str) -> dict:
    async with LiveStream(
        run_id=f"{build_id}-{suite_name}",
        launch_name=f"Parallel suites - {build_id}",
        build_number=build_id,
        framework="pytest-programmatic",
        total_tests=len(cases),
        metadata={"suite_group": "nightly-regression", "parallel": True},
    ) as stream:
        for case in cases:
            started = time.perf_counter()
            await asyncio.sleep(case.duration_ms / 10_000)
            duration_ms = int((time.perf_counter() - started) * 1_000)
            if case.should_pass:
                await stream.record(
                    case.name,
                    "PASSED",
                    duration_ms,
                    suite_name=suite_name,
                    tags=["parallel", "regression"],
                    metadata={"worker": suite_name, "expected_result": "business rule holds"},
                )
            else:
                await stream.record(
                    case.name,
                    "FAILED",
                    duration_ms,
                    suite_name=suite_name,
                    error="AssertionError: expected retry_count <= 2, got 4",
                    stack_trace="checkout.retry_policy: expected retry_count <= 2, got 4",
                    tags=["parallel", "regression", "known-risk"],
                    metadata={
                        "worker": suite_name,
                        "assertion": "retry_count <= 2",
                        "expected_result": "gateway timeout is retried at most twice",
                        "actual_result": "gateway timeout retried four times",
                    },
                )

    return stream.stats


async def _run_parallel_suites(build_id: str) -> list[dict]:
    return await asyncio.gather(*(_run_suite(name, cases, build_id) for name, cases in SUITES.items()))


@pytest.mark.integration
def test_parallel_suites_share_one_api_key() -> None:
    if not _configured():
        pytest.skip("Set testlookup.api.key or TESTLOOKUP_API_KEY to run this live example")

    build_id = f"pytest-parallel-{uuid.uuid4().hex[:8]}"
    stats = asyncio.run(_run_parallel_suites(build_id))

    assert len(stats) == len(SUITES)
    assert sum(item["sent"] for item in stats) >= sum(len(cases) for cases in SUITES.values())
