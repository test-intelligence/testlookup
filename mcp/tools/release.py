"""Tool: check_release_readiness."""

from __future__ import annotations

import client as api  # type: ignore[import]


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def check_release_readiness(project_id: str, days: int = 7) -> str:
        """
        Evaluate whether a project is ready to release based on current quality metrics.
        Returns a GREEN / AMBER / RED signal with a plain-language recommendation
        and the specific metrics driving the decision.

        Args:
            project_id: Project UUID.
            days: Analysis window for the assessment (default 7).
        """
        data = await api.get(
            "/api/v1/metrics/summary",
            params={"project_id": project_id, "days": days},
        )

        readiness = data.get("release_readiness", "UNKNOWN")
        icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}.get(readiness, "⚪")

        def val(key: str) -> float:
            v = data.get(key, 0)
            if isinstance(v, dict):
                return v.get("current", 0)
            return v or 0

        pass_rate = val("avg_pass_rate_7d")
        active_defects = int(val("active_defects"))
        flaky_count = int(val("flaky_test_count"))
        new_failures = int(val("new_failures_24h"))
        total_runs = int(val("total_executions_7d"))

        # Build plain-language assessment
        blockers = []
        warnings = []

        if pass_rate < 85:
            blockers.append(f"Pass rate is {pass_rate:.1f}% (threshold: 85%)")
        elif pass_rate < 95:
            warnings.append(f"Pass rate is {pass_rate:.1f}% — below ideal 95%")

        if active_defects > 5:
            blockers.append(f"{active_defects} open defects")
        elif active_defects > 0:
            warnings.append(f"{active_defects} open defect(s) — verify severity")

        if new_failures > 10:
            blockers.append(f"{new_failures} new failures in the last 24h")
        elif new_failures > 0:
            warnings.append(f"{new_failures} new failure(s) in the last 24h — investigate")

        if flaky_count > 20:
            warnings.append(f"{flaky_count} flaky tests may mask real failures")

        lines = [
            f"## {icon} Release Readiness: **{readiness}**",
            f"*Assessment based on last {days} days — {total_runs} test run(s)*",
            "",
            "### Key Metrics",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Avg Pass Rate | **{pass_rate:.1f}%** |",
            f"| Active Defects | {active_defects} |",
            f"| Flaky Tests | {flaky_count} |",
            f"| New Failures (24h) | {new_failures} |",
            f"| Total Runs ({days}d) | {total_runs} |",
        ]

        if blockers:
            lines += ["", "### ❌ Blockers (must resolve before release)"]
            for b in blockers:
                lines.append(f"- {b}")

        if warnings:
            lines += ["", "### ⚠️ Warnings (review before release)"]
            for w in warnings:
                lines.append(f"- {w}")

        lines += ["", "### Recommendation"]
        if readiness == "GREEN":
            lines.append("✅ **Go** — Quality metrics are within acceptable thresholds.")
        elif readiness == "AMBER":
            lines.append(
                "🟡 **Conditional Go** — Review warnings above. Consider deploying "
                "with monitoring alerts enabled and a rollback plan ready."
            )
        else:
            lines.append(
                "🔴 **No-Go** — Resolve all blockers before releasing. "
                "Run `get_top_failing_tests` and `get_defects` for details."
            )

        return "\n".join(lines)
