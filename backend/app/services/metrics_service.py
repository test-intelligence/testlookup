"""Dashboard metrics aggregation service."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession


from app.models.postgres import Defect, TestCase, TestRun, TestStatus

logger = logging.getLogger(__name__)


async def get_dashboard_summary(db: AsyncSession, project_id: str | None, days: int = 7) -> dict:
    """Compute all Executive Dashboard KPIs for a project."""
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=days)
    prev_period_start = now - timedelta(days=days * 2)

    # ── Current period stats ──────────────────────────────
    cur = await _period_stats(db, project_id, period_start, now)
    prev = await _period_stats(db, project_id, prev_period_start, period_start)

    def trend(cur_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((cur_val - prev_val) / prev_val) * 100, 1)
        return None

    def direction(t):
        if t is None:
            return "flat"
        return "up" if t > 0 else "down"

    pass_rate = cur["pass_rate"]
    prev_pass_rate = prev["pass_rate"]
    pass_trend = trend(pass_rate, prev_pass_rate)

    total_exec = cur["total_runs"]
    total_exec_trend = trend(total_exec, prev["total_runs"])

    # Active defects (all open, not time-bounded)
    defect_conditions = [Defect.resolution_status == "OPEN"]
    if project_id:
        defect_conditions.append(Defect.project_id == project_id)
    defect_result = await db.execute(
        select(func.count(Defect.id)).where(*defect_conditions)
    )
    active_defects = defect_result.scalar() or 0

    # Flaky tests (>20% failure rate over last 10 runs)
    flaky_count = await _count_flaky_tests(db, project_id)

    # New failures in last 24h
    yesterday = now - timedelta(hours=24)
    fail_conditions = [
        TestCase.status == TestStatus.FAILED,
        TestCase.created_at >= yesterday,
    ]
    if project_id:
        fail_conditions.append(TestRun.project_id == project_id)
    new_fail_result = await db.execute(
        select(func.count(TestCase.id))
        .join(TestRun)
        .where(*fail_conditions)
    )
    new_failures_24h = new_fail_result.scalar() or 0

    # Release readiness
    readiness = _compute_readiness(pass_rate, active_defects, flaky_count)

    return {
        "total_executions_7d": {
            "value": total_exec,
            "trend": total_exec_trend,
            "trend_direction": direction(total_exec_trend),
        },
        "avg_pass_rate_7d": {
            "value": round(pass_rate, 1),
            "trend": pass_trend,
            "trend_direction": direction(pass_trend),
        },
        "active_defects": {
            "value": active_defects,
            "trend": None,
            "trend_direction": "flat",
        },
        "flaky_test_count": {
            "value": flaky_count,
            "trend": None,
            "trend_direction": "flat",
        },
        "new_failures_24h": {
            "value": new_failures_24h,
            "trend": None,
            "trend_direction": "flat",
        },
        "avg_duration_ms": {
            "value": cur["avg_duration_ms"],
            "trend": trend(cur["avg_duration_ms"] or 0, prev["avg_duration_ms"] or 0),
            "trend_direction": "flat",
        },
        "release_readiness": readiness,
    }


async def get_trend_data(db: AsyncSession, project_id: str | None, days: int = 7) -> list:
    """Return daily pass/fail/skip breakdown for the trend chart."""
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    project_filter = "AND tr.project_id = :project_id" if project_id else ""
    query = text(f"""
        SELECT
            DATE_TRUNC('day', tr.created_at) AS day,
            COALESCE(SUM(tr.passed_tests), 0)  AS passed,
            COALESCE(SUM(tr.failed_tests), 0)  AS failed,
            COALESCE(SUM(tr.skipped_tests), 0) AS skipped,
            COALESCE(SUM(tr.broken_tests), 0)  AS broken,
            COALESCE(SUM(tr.total_tests), 0)   AS total,
            COALESCE(AVG(tr.pass_rate), 0)     AS pass_rate
        FROM test_runs tr
        WHERE tr.created_at >= :period_start
          {project_filter}
        GROUP BY day
        ORDER BY day ASC
    """)
    params: dict = {"period_start": period_start}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        {
            "date": row.day.strftime("%b %d"),
            "passed": int(row.passed),
            "failed": int(row.failed),
            "skipped": int(row.skipped),
            "broken": int(row.broken),
            "total": int(row.total),
            "pass_rate": round(float(row.pass_rate), 1),
        }
        for row in rows
    ]


async def _period_stats(db: AsyncSession, project_id: str | None, start: datetime, end: datetime) -> dict:
    conditions = [TestRun.created_at >= start, TestRun.created_at < end]
    if project_id:
        conditions.append(TestRun.project_id == project_id)
    result = await db.execute(
        select(
            func.count(TestRun.id).label("total_runs"),
            func.avg(TestRun.pass_rate).label("pass_rate"),
            func.avg(TestRun.duration_ms).label("avg_duration_ms"),
        ).where(*conditions)
    )
    row = result.one()
    return {
        "total_runs": row.total_runs or 0,
        "pass_rate": float(row.pass_rate or 0),
        "avg_duration_ms": int(row.avg_duration_ms or 0),
    }


async def _count_flaky_tests(db: AsyncSession, project_id: str | None) -> int:
    """Count tests with failure rate between 10% and 90% over last 10 runs (flaky pattern)."""
    project_filter = "WHERE tr.project_id = :project_id" if project_id else ""
    query = text(f"""
        SELECT COUNT(DISTINCT fingerprint) FROM (
            SELECT
                tch.test_fingerprint AS fingerprint,
                COUNT(*) FILTER (WHERE tch.status = 'FAILED') AS fail_count,
                COUNT(*) AS total_count
            FROM test_case_history tch
            JOIN test_runs tr ON tr.id = tch.test_run_id
            {project_filter}
            GROUP BY tch.test_fingerprint
            HAVING COUNT(*) >= 5
               AND COUNT(*) FILTER (WHERE tch.status = 'FAILED') * 1.0 / COUNT(*) BETWEEN 0.1 AND 0.9
        ) flaky
    """)
    params: dict = {}
    if project_id:
        params["project_id"] = str(project_id)
    result = await db.execute(query, params)
    return result.scalar() or 0


def _compute_readiness(pass_rate: float, active_defects: int, flaky_count: int) -> str:
    if pass_rate >= 95 and active_defects == 0 and flaky_count <= 5:
        return "GREEN"
    if pass_rate >= 85 and active_defects <= 5:
        return "AMBER"
    return "RED"
