"""Demo dataset generator — synthetic test-run history for a compelling first run.

Slice 2 of frictionless self-host adoption. A fresh install only seeds *authored*
test cases/plans/releases; the dashboards, flaky-coach, trends, and failures
pages stay empty until real runs arrive (and a single upload can't show
flakiness or trends). This module deterministically generates a series of run
payloads spanning ~30 days that embed the headline patterns so a newcomer
immediately sees populated, meaningful views:

  * STABLE tests — always pass (realistic pass rate, trend baseline)
  * a FLAKY test — alternates pass/fail with varied error signatures
    (environmental flake → flaky-coach + intermittency + step-flip)
  * a REGRESSION — passes for most of the window then fails in the latest runs
    (clean→failing → new_regression in the release gate / failures)
  * a PRODUCT BUG — fails every run with one consistent assertion
  * a PERF regression — passes but its duration spikes in recent runs

Pure: no DB, no I/O, no wall-clock reads (the caller passes ``now`` so output is
deterministic and unit-testable). The seed script feeds these payloads through
the real ingestion pipeline (backdating ``started_at``) so every derived table
— history, fingerprints, canonical cases, suites, aggregates — is correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# Rotating environmental errors for the flaky/infra test — distinct signatures
# so error_signature_diversity reads as "environmental" rather than one bug.
_ENV_ERRORS = [
    ("TimeoutError: timed out waiting for selector '#pay-btn' after 30000ms",
     "  at waitForSelector (checkout.spec.ts:42)\n  at Object.<anonymous> (checkout.spec.ts:40)"),
    ("ConnectionResetError: connection reset by peer (payment-gateway:443)",
     "  at request (http_client.py:88)\n  at charge (payment.py:51)"),
    ("DNSError: getaddrinfo ENOTFOUND payments.internal",
     "  at lookup (dns.js:71)\n  at connect (net.js:1042)"),
]
_REGRESSION_ERR = (
    "AssertionError: expected status 200 but got 500",
    "  at test_payment_capture (test_payment.py:73)\n  AssertionError: 200 != 500",
)
_PRODUCT_BUG_ERR = (
    "AssertionError: discount 0.15 != expected 0.10",
    "  at test_legacy_discount (test_pricing.py:118)\n  AssertionError: 0.15 != 0.10",
)


@dataclass(frozen=True)
class DemoRun:
    """One synthetic run: an IngestPayload-shaped body plus its backdated time."""

    build_number: str
    started_at: datetime
    branch: str
    framework: str
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self, project_id: str) -> dict[str, Any]:
        """Render as a POST /api/v1/ingest (IngestPayload) body."""
        return {
            "project_id": project_id,
            "build_number": self.build_number,
            "branch": self.branch,
            "framework": self.framework,
            "trigger_source": "demo",
            "results": list(self.results),
        }


# Stable tests: (test_name, suite, class, baseline_ms). Always PASSED.
_STABLE = [
    ("test_home_page_loads", "E2E Suite", "HomeTests", 320),
    ("test_login_with_valid_credentials", "E2E Suite", "AuthTests", 410),
    ("test_logout_clears_session", "E2E Suite", "AuthTests", 180),
    ("test_global_search_returns_results", "E2E Suite", "SearchTests", 540),
    ("test_profile_update_persists", "E2E Suite", "ProfileTests", 470),
    ("test_cart_add_item", "E2E Suite", "CartTests", 300),
    ("test_cart_remove_item", "E2E Suite", "CartTests", 280),
    ("test_settings_save", "E2E Suite", "SettingsTests", 350),
    ("test_api_health_ok", "API Suite", "HealthTests", 40),
    ("test_api_list_users", "API Suite", "UserApiTests", 95),
    ("test_api_get_user_by_id", "API Suite", "UserApiTests", 88),
    ("test_api_create_order", "API Suite", "OrderApiTests", 130),
]


def _dur(base: int, run_index: int) -> int:
    """Deterministic small jitter around a baseline (no RNG)."""
    return base + ((run_index * 37) % 60) - 30


def generate_demo_runs(
    *,
    runs: int = 14,
    now: datetime,
    span_days: int = 30,
) -> list[DemoRun]:
    """Generate ``runs`` synthetic runs from oldest (≈ now-span_days) to newest
    (≈ now). Deterministic for a given ``now``/``runs``. ``runs`` is clamped to
    a sane minimum so the flaky/regression patterns are observable.
    """
    runs = max(6, int(runs))
    step = (span_days / (runs - 1)) if runs > 1 else 0.0
    out: list[DemoRun] = []

    # Indices counted from the NEWEST run (0 = latest) make "recent N runs"
    # patterns easy to express.
    for i in range(runs):
        from_newest = runs - 1 - i
        started = now - timedelta(days=step * from_newest)
        results: list[dict[str, Any]] = []

        for name, suite, cls, base in _STABLE:
            results.append({
                "test_name": name, "suite_name": suite, "class_name": cls,
                "status": "PASSED", "duration_ms": _dur(base, i),
            })

        # FLAKY (environmental): fail on ~40% of runs with a rotating error.
        flaky_fail = (i % 5) in (1, 3)
        if flaky_fail:
            msg, trace = _ENV_ERRORS[i % len(_ENV_ERRORS)]
            results.append({
                "test_name": "test_checkout_completes", "suite_name": "E2E Suite",
                "class_name": "CheckoutTests", "status": "FAILED",
                "duration_ms": _dur(900, i), "error_message": msg, "stack_trace": trace,
            })
        else:
            results.append({
                "test_name": "test_checkout_completes", "suite_name": "E2E Suite",
                "class_name": "CheckoutTests", "status": "PASSED", "duration_ms": _dur(620, i),
            })

        # REGRESSION: clean for older runs, broken in the latest 4.
        regressed = from_newest < 4
        results.append({
            "test_name": "test_payment_capture", "suite_name": "API Suite",
            "class_name": "PaymentApiTests",
            "status": "FAILED" if regressed else "PASSED",
            "duration_ms": _dur(150, i),
            **({"error_message": _REGRESSION_ERR[0], "stack_trace": _REGRESSION_ERR[1]}
               if regressed else {}),
        })

        # PRODUCT BUG: fails every run, same assertion.
        results.append({
            "test_name": "test_legacy_discount_calc", "suite_name": "API Suite",
            "class_name": "PricingTests", "status": "FAILED", "duration_ms": _dur(60, i),
            "error_message": _PRODUCT_BUG_ERR[0], "stack_trace": _PRODUCT_BUG_ERR[1],
        })

        # PERF regression: passes, but duration spikes in the latest 3 runs.
        perf_ms = (3200 + _dur(0, i)) if from_newest < 3 else _dur(780, i)
        results.append({
            "test_name": "test_report_export", "suite_name": "API Suite",
            "class_name": "ReportTests", "status": "PASSED", "duration_ms": perf_ms,
        })

        out.append(DemoRun(
            build_number=f"ci-{1000 + i}",
            started_at=started,
            branch="main",
            framework="pytest",
            results=results,
        ))

    return out
