"""LangChain tool: check historical test flakiness from PostgreSQL."""
from langchain_core.tools import tool
from sqlalchemy import text

from app.db.postgres import AsyncSessionLocal


@tool
async def check_test_flakiness(test_name: str) -> str:
    """
    Query the PostgreSQL database for the historical pass/fail pattern of a test.
    This reveals whether the test is intermittently failing (flaky) or has a
    consistent new failure pattern.

    Args:
        test_name: The exact test case name to look up.

    Returns:
        A summary of the test's history over the last 10 runs.
    """
    async with AsyncSessionLocal() as db:
        query = text("""
            SELECT tch.status, COUNT(*) as cnt
            FROM test_case_history tch
            JOIN test_cases tc ON tc.id = tch.test_case_id
            WHERE tc.test_name = :test_name
            GROUP BY tch.status
            ORDER BY cnt DESC
            LIMIT 10
        """)
        result = await db.execute(query, {"test_name": test_name})
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
        if is_flaky:
            lines.append(f"⚠️  FLAKY — test has intermittent failure pattern ({fail_pct}% failure rate)")
        elif fail_pct >= 90:
            lines.append("🔴 CONSISTENTLY FAILING — likely a genuine product bug or broken test")
        elif fail_pct == 0:
            lines.append("🟢 HISTORICALLY STABLE — this is a new failure, not flakiness")
        else:
            lines.append(f"ℹ️  Failure rate: {fail_pct}%")

        return "\n".join(lines)
