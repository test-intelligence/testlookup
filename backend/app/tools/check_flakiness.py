"""LangChain tool: check historical test flakiness from PostgreSQL."""
from langchain_core.tools import tool
from sqlalchemy import text

from app.db.postgres import AsyncSessionLocal
from app.services.flaky_score_service import MIN_OBSERVATIONS
from app.tools.investigation_context import get_investigation_context

#: D-0008: the fewest recorded executions that may be called a pattern (flaky,
#: consistently failing, historically stable): the Flaky score's own floor, so
#: one failed execution never reads like an established history.
MIN_EXECUTIONS_FOR_PATTERN = MIN_OBSERVATIONS


@tool
async def check_test_flakiness(test_name: str) -> str:
    """
    Query the PostgreSQL database for the historical pass/fail pattern of a test.
    This reveals whether the test is intermittently failing (flaky) or has a
    consistent failure pattern, once enough executions are recorded.

    Args:
        test_name: The exact test case name to look up.

    Returns:
        A summary of the test's recorded history in this project. With fewer
        than 5 recorded executions it names no pattern: too early to tell.
    """
    context = get_investigation_context()
    if context is None:
        return "Flakiness lookup unavailable: no authorized investigation context."
    if str(test_name) != context.test_name:
        return "Flakiness lookup denied: requested test is outside the investigation scope."
    async with AsyncSessionLocal() as db:
        query = text("""
            SELECT tch.status, COUNT(*) as cnt
            FROM test_case_history tch
            JOIN test_cases tc ON tc.id = tch.test_case_id
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tc.test_name = :test_name
              AND tr.project_id = :project_id
            GROUP BY tch.status
            ORDER BY cnt DESC
            LIMIT 10
        """)
        result = await db.execute(query, {
            "test_name": context.test_name,
            "project_id": context.project_id,
        })
        rows = result.fetchall()

        if not rows:
            return f"No historical data found for test: '{test_name}'. This may be a new test."

        total = sum(r.cnt for r in rows)
        status_counts = {r.status: r.cnt for r in rows}

        passed = status_counts.get("PASSED", 0)
        failed = status_counts.get("FAILED", 0) + status_counts.get("BROKEN", 0)
        skipped = status_counts.get("SKIPPED", 0)

        fail_pct = round((failed / total) * 100, 1) if total > 0 else 0.0
        is_flaky = 10.0 < fail_pct < 90.0

        lines = [
            f"=== Flakiness History: {test_name} ===",
            f"Total executions analysed: {total}",
            f"Passed: {passed} | Failed/Broken: {failed} | Skipped: {skipped}",
            f"Failure rate: {fail_pct}%",
        ]
        if total < MIN_EXECUTIONS_FOR_PATTERN:
            # D-0008: counts and rate stay; a pattern label needs a sample.
            lines.append(
                f"ℹ️  TOO FEW EXECUTIONS — {total} recorded; too early to tell a consistent "
                "failure from a one-off or flakiness."
            )
        elif is_flaky:
            lines.append(f"⚠️  FLAKY — test has intermittent failure pattern ({fail_pct}% failure rate)")
        elif fail_pct >= 90:
            lines.append("🔴 CONSISTENTLY FAILING — likely a genuine product bug or broken test")
        elif fail_pct == 0:
            lines.append("🟢 HISTORICALLY STABLE — this is a new failure, not flakiness")
        else:
            lines.append(f"ℹ️  Failure rate: {fail_pct}%")

        return "\n".join(lines)
