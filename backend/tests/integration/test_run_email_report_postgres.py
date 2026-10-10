"""The run report an email carries, built against real PostgreSQL.

Owner request 2026-10-10: run emails carry the run -- facts, results against
the previous build, every failing test with its error and AI root cause, the
suites, and the release gate -- so a reader need not open the dashboard. The
report's reads are SQL the unit tests cannot check (grouping, the previous
run, the release preview); this seeds a project and asserts what comes back.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded():
    from app.models.postgres import AIAnalysis, Project, Release, TestCase, TestRun

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    pid, rel = uuid.uuid4(), uuid.uuid4()
    prev_id, run_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    b_id = uuid.uuid4()

    def run(rid, build, ago, totals):
        total, passed, failed, broken = totals
        return TestRun(
            id=rid, project_id=pid, build_number=f"{build}-{tag}", branch="main",
            primary_suite_name="Checkout", primary_release_id=rel, environment="staging",
            ci_provider="github_actions", ci_repo="acme/shop", pr_number=42,
            ci_run_url="https://ci.example.com/run/1", status="FAILED",
            total_tests=total, passed_tests=passed, failed_tests=failed, broken_tests=broken,
            skipped_tests=0, pass_rate=round(passed / (passed + failed + broken) * 100, 2),
            duration_ms=4200, created_at=now - ago, start_time=now - ago,
        )

    def case(rid, name, status, suite="Checkout", tags=None, cid=None, error=None, category=None):
        return TestCase(
            id=cid or uuid.uuid4(), test_run_id=rid, test_fingerprint=f"{tag}-{name}"[:64],
            test_name=name, class_name="CheckoutTest", suite_name=suite, status=status,
            error_message=error, failure_category=category, tags=tags, created_at=now,
        )

    try:
        async with sessions.begin() as db:
            db.add(Project(id=pid, name=f"mailrep-{tag}", slug=f"mailrep-{tag}", is_active=True))
            await db.flush()
            db.add(Release(id=rel, project_id=pid, name=f"R-{tag}", status="in_progress"))
            await db.flush()
            db.add_all([run(prev_id, "b1", timedelta(hours=2), (2, 1, 1, 0)),
                        run(run_id, "b2", timedelta(hours=1), (6, 3, 2, 1))])
            await db.flush()
            db.add_all([
                case(prev_id, "test_a", "FAILED", error="boom earlier"),
                case(prev_id, "test_b", "PASSED"),
                case(run_id, "test_a", "FAILED", error="AssertionError: still broken", category="PRODUCT_BUG"),
                case(run_id, "test_b", "FAILED", cid=b_id, error="TimeoutError: <b>element</b> not visible"),
                case(run_id, "test_c", "PASSED", suite="Payments"),
                case(run_id, "test_e", "PASSED", suite="Payments"),
                case(run_id, "test_f", "PASSED", suite="Payments"),
                case(run_id, "test_d", "BROKEN", suite="Payments", tags=["broken", "quarantined"]),
            ])
            await db.flush()
            db.add(AIAnalysis(test_case_id=b_id, root_cause_summary="Checkout page renders slowly under load",
                              failure_category="INFRASTRUCTURE", confidence_score=80))
        yield sessions, pid, run_id, tag
    finally:
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await engine.dispose()


async def _build(seeded, *, allowed=True):
    from types import SimpleNamespace

    from app.services.notification import run_report

    sessions, _pid, run_id, _tag = seeded
    decision = SimpleNamespace(allowed=allowed, watermark=None, audit_action=None)
    with patch("app.services.report_distribution_policy.decide_run_distribution", AsyncMock(return_value=decision)), \
            patch.object(run_report, "_ai_summary", AsyncMock(return_value=None)):
        async with sessions() as db:
            report, _ = await run_report.build_run_report(db, run_id, base_url="https://tl.example.com")
    return report


async def test_the_report_carries_the_run_its_failures_and_the_previous_build(seeded):
    report = await _build(seeded)
    _sessions, _pid, run_id, tag = seeded

    assert report["run"]["build_number"] == f"b2-{tag}"
    assert report["run"]["ci_repo"] == "acme/shop" and report["run"]["pr_number"] == 42
    assert report["counts"] == {"total": 6, "passed": 3, "failed": 2, "broken": 1, "skipped": 0, "pass_rate": 50.0}
    assert report["previous"]["build_number"] == f"b1-{tag}"

    by_name = {f["name"]: f for f in report["failures"]}
    assert set(by_name) == {"test_a", "test_b", "test_d"} and report["failing_total"] == 3
    assert by_name["test_a"]["is_new"] is False          # failed in b1 too
    assert by_name["test_b"]["is_new"] is True           # passed in b1
    assert by_name["test_d"]["quarantined"] is True
    assert by_name["test_b"]["ai_root_cause"] == "Checkout page renders slowly under load"
    assert by_name["test_b"]["category"] == "INFRASTRUCTURE"  # from the analysis when the case has none
    assert report["new_failures"] == 2                   # test_b, test_d
    assert {s["name"]: (s["total"], s["failed"], s["broken"]) for s in report["suites"]} == {
        "Checkout": (2, 2, 0), "Payments": (4, 0, 1),
    }
    assert report["categories"] == {"PRODUCT_BUG": 1, "UNKNOWN": 2}
    assert by_name["test_a"]["link"] == f"https://tl.example.com/runs/{run_id}/tests/{by_name['test_a']['id']}"


async def test_the_release_section_names_the_blocking_tests_this_run_contributes(seeded):
    report = await _build(seeded)
    release = report["release"]
    assert release["verdict"] == "NO_GO" and release["live"] is True
    # Six distinct tests clear the evidence floor (5). The quarantined failure
    # is set aside; test_a and test_b block, both failing in this run.
    assert release["blocking_count"] == 2 and release["blocking_from_this_run"] == 2
    assert release["quarantined_failures"] == 1
    assert all(" (Checkout) is FAILED" in r for r in release["blocking_reasons"]), release


async def test_a_refused_gate_withholds_every_ai_conclusion(seeded):
    from app.services.notification.run_report import render_run_report_html

    report = await _build(seeded, allowed=False)
    assert report["ai_allowed"] is False and report["ai_withheld"]
    assert not any(f.get("ai_root_cause") for f in report["failures"])
    html = render_run_report_html(report)
    assert "Checkout page renders slowly" not in html and "awaiting human review" in html
    assert "&lt;b&gt;element&lt;/b&gt;" in html  # error text is escaped
