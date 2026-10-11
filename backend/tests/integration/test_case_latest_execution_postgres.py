"""An authored test case linked to automation carries its latest result.

Owner review of the test case view (2026-10-10): every Test Management row
now opens the same case panel, whose "Open latest result" and "Run history"
links need the latest execution's ids. Automation rows always had them; an
authored case linked to automation (same project + fingerprint -- the rule
/cases already uses to hide that test's AUTO row) did not, so its panel read
"Last Executed --" while the test ran every build.

``_attach_latest_executions`` is one DISTINCT ON query per page; this pins it
against real PostgreSQL: the latest run wins, another project's run of the
same fingerprint is not borrowed, a newer manual execution keeps its own
result, and an unlinked case is left alone.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
async def world():
    from app.models.postgres import Project, TestCase, TestRun

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    pid, other_pid = uuid.uuid4(), uuid.uuid4()
    old_run, new_run, other_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    old_tc, new_tc, other_tc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fp = f"{tag}-checkout"
    now = datetime.now(timezone.utc)

    def run(rid, project, ago):
        return TestRun(id=rid, project_id=project, build_number=f"b-{rid.hex[:6]}", status="FAILED",
                       created_at=now - ago, start_time=now - ago)

    async with sessions.begin() as db:
        db.add_all([Project(id=pid, name=f"latest-{tag}", slug=f"latest-{tag}", is_active=True),
                    Project(id=other_pid, name=f"other-{tag}", slug=f"other-{tag}", is_active=True)])
        await db.flush()
        db.add_all([run(old_run, pid, timedelta(days=2)), run(new_run, pid, timedelta(hours=1)),
                    run(other_run, other_pid, timedelta(minutes=5))])
        await db.flush()
        db.add_all([
            TestCase(id=old_tc, test_run_id=old_run, test_fingerprint=fp, test_name="test_checkout", status="PASSED"),
            TestCase(id=new_tc, test_run_id=new_run, test_fingerprint=fp, test_name="test_checkout", status="FAILED"),
            # The same fingerprint in another project is another test.
            TestCase(id=other_tc, test_run_id=other_run, test_fingerprint=fp, test_name="test_checkout", status="PASSED"),
        ])
    try:
        yield SimpleNamespace(sessions=sessions, pid=pid, fp=fp, new_run=new_run, new_tc=new_tc, now=now)
    finally:
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": pid, "b": other_pid})
        await engine.dispose()


def _response(**overrides):
    values = dict(latest_run_id=None, latest_test_case_id=None, canonical_test_case_id=None,
                  last_executed_at=None, last_execution_status=None)
    values.update(overrides)
    return SimpleNamespace(**values)


async def _attach(world, pairs):
    from app.routers.test_management_cases import _attach_latest_executions

    async with world.sessions() as db:
        await _attach_latest_executions(db, pairs)


async def test_a_linked_case_carries_its_latest_run_in_its_own_project(world):
    case = SimpleNamespace(project_id=world.pid, test_fingerprint=world.fp)
    resp = _response()
    await _attach(world, [(case, resp)])

    assert resp.latest_run_id == world.new_run and resp.latest_test_case_id == world.new_tc
    assert resp.last_execution_status == "FAILED"  # the latest run, not the older pass
    assert resp.last_executed_at is not None


async def test_a_newer_manual_execution_keeps_its_own_result(world):
    case = SimpleNamespace(project_id=world.pid, test_fingerprint=world.fp)
    manual_at = world.now + timedelta(minutes=1)
    resp = _response(last_executed_at=manual_at, last_execution_status="passed")
    await _attach(world, [(case, resp)])

    assert resp.latest_test_case_id == world.new_tc  # the links still point at automation
    assert (resp.last_executed_at, resp.last_execution_status) == (manual_at, "passed")


async def test_an_unlinked_or_unmatched_case_is_left_alone(world):
    manual = SimpleNamespace(project_id=world.pid, test_fingerprint=None)
    unmatched = SimpleNamespace(project_id=world.pid, test_fingerprint=f"{world.fp}-nothing-ran")
    r1, r2 = _response(), _response()
    await _attach(world, [(manual, r1), (unmatched, r2)])

    assert r1.latest_run_id is None and r2.latest_run_id is None
