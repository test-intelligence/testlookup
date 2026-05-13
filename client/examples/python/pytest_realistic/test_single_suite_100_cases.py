"""Submit one suite with 100 realistic test cases and rich step metadata."""

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
class Step:
    name: str
    action: str
    assertion: str
    expected_result: str


@dataclass(frozen=True)
class Case:
    id: str
    name: str
    steps: list[Step]
    should_pass: bool = True


def _configured() -> bool:
    cfg = ConfigLoader.load()
    endpoint = ConfigLoader.get(cfg, "server.url")
    api_key = ConfigLoader.get(cfg, "auth.api_key") or os.environ.get("TESTLOOKUP_API_KEY")
    return bool(endpoint and api_key and not str(api_key).endswith("REPLACE_ME"))


def _case(index: int) -> Case:
    checkout_path = ["guest", "registered", "loyalty", "mobile"][index % 4]
    payment_method = ["card", "paypal", "gift_card", "bank_transfer"][index % 4]
    should_pass = index not in {17, 42, 73}
    return Case(
        id=f"CHK-{index:03d}",
        name=f"checkout_{checkout_path}_{payment_method}_{index:03d}",
        should_pass=should_pass,
        steps=[
            Step(
                "given_cart_has_items",
                f"Create a {checkout_path} cart with two in-stock SKUs",
                "cart.total > 0",
                "Cart total is calculated with item subtotal, tax, and shipping",
            ),
            Step(
                "when_customer_submits_payment",
                f"Submit checkout with {payment_method}",
                "payment.authorization_status == 'approved'",
                "Payment provider returns an approved authorization",
            ),
            Step(
                "then_order_is_confirmed",
                "Poll order service for the created order",
                "order.status == 'CONFIRMED'",
                "Order is confirmed and confirmation email is queued",
            ),
        ],
    )


def _metadata(case: Case) -> dict:
    return {
        "case_id": case.id,
        "step_definitions": [
            {
                "name": step.name,
                "action": step.action,
                "assertion": step.assertion,
                "expected_result": step.expected_result,
            }
            for step in case.steps
        ],
        "assertions": [step.assertion for step in case.steps],
        "expected_results": [step.expected_result for step in case.steps],
    }


async def _submit_cases() -> dict:
    cases = [_case(i) for i in range(1, 101)]
    run_id = f"pytest-checkout-100-{uuid.uuid4().hex[:8]}"
    async with LiveStream(
        run_id=run_id,
        launch_name="Checkout suite - 100 cases",
        build_number=run_id,
        framework="pytest-programmatic",
        total_tests=len(cases),
        metadata={"suite_type": "single-suite-rich-steps"},
    ) as stream:
        for index, case in enumerate(cases, start=1):
            started = time.perf_counter()
            await asyncio.sleep(0)
            duration_ms = int((time.perf_counter() - started) * 1_000) + 20 + (index % 11)
            metadata = _metadata(case)

            if case.should_pass:
                await stream.record(
                    case.name,
                    "PASSED",
                    duration_ms,
                    suite_name="checkout-regression-100",
                    class_name="CheckoutRegression",
                    tags=["checkout", "regression", "rich-steps"],
                    metadata=metadata,
                )
            else:
                await stream.record(
                    case.name,
                    "FAILED",
                    duration_ms,
                    suite_name="checkout-regression-100",
                    class_name="CheckoutRegression",
                    error="AssertionError: order.status expected CONFIRMED but was PAYMENT_REVIEW",
                    stack_trace=f"{case.id}: then_order_is_confirmed failed",
                    tags=["checkout", "regression", "rich-steps"],
                    metadata={
                        **metadata,
                        "actual_result": "Order remained in PAYMENT_REVIEW after payment authorization",
                        "failed_step": "then_order_is_confirmed",
                    },
                )

    return stream.stats


@pytest.mark.integration
def test_submit_single_suite_with_100_cases_and_steps() -> None:
    if not _configured():
        pytest.skip("Set testlookup.api.key or TESTLOOKUP_API_KEY to run this live example")

    stats = asyncio.run(_submit_cases())

    assert stats["sent"] >= 100
