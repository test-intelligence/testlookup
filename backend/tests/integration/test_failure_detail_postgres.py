"""FAIL-01 against real Postgres: the latest failing row of each top-failing test.

``top_failing_tests`` (one project in scope) gains, per row, the test's newest
FAILED/BROKEN row inside the request's scope -- its run and test case, its
first error line and stack head (truncated and redacted server-side), its
owner. Only a real database proves the ``DISTINCT ON`` picks that row, that
the project pin keeps another project's newer failure of the same fingerprint
out (``test_fingerprint`` is not salted per project), that the suite and
release fragments apply, and the statement count.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips otherwise.
Everything is flushed inside one transaction and rolled back.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def ctx():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    async with factory() as db:
        try:
            yield db, statements
        finally:
            await db.rollback()
    event.remove(engine.sync_engine, "before_cursor_execute", _count)
    await engine.dispose()


class World:
    """A project, its runs and test cases, flushed on one session."""

    def __init__(self, db):
        self.db = db
        self.token = uuid.uuid4().hex[:10]
        self.now = datetime.now(timezone.utc)

    async def project(self, **extra):
        from app.models.postgres import Project

        pid = uuid.uuid4()
        self.db.add(Project(id=pid, name=f"fd {uuid.uuid4().hex[:10]}", slug=f"fd-{uuid.uuid4().hex[:10]}", **extra))
        await self.db.flush()
        return pid

    async def user(self, name, **flags):
        from app.models.postgres import User

        uid = uuid.uuid4()
        tag = uuid.uuid4().hex[:10]
        self.db.add(User(id=uid, email=f"fd-{tag}@example.com", username=f"fd_{tag}", full_name=name,
                         hashed_password="!unusable", role="qa_engineer", **flags))
        await self.db.flush()
        return uid

    async def run(self, project_id, *, hours_ago: float, release_id=None, suite="Checkout"):
        from app.models.postgres import LaunchStatus, TestRun

        rid = uuid.uuid4()
        at = self.now - timedelta(hours=hours_ago)
        self.db.add(TestRun(id=rid, project_id=project_id, build_number=f"fd-{uuid.uuid4().hex[:8]}",
                            status=LaunchStatus.FAILED, start_time=at, created_at=at,
                            primary_release_id=release_id, primary_suite_name=suite))
        await self.db.flush()
        return rid, at

    async def case(self, run, name, status="FAILED", *, suite="Checkout", **extra):
        from app.models.postgres import TestCase, TestStatus

        run_id, at = run
        cid = uuid.uuid4()
        extra.setdefault("class_name", "CheckoutTest")
        self.db.add(TestCase(id=cid, test_run_id=run_id, test_name=name,
                             suite_name=suite, test_fingerprint=f"{self.token}-{name}",
                             status=TestStatus(status), created_at=at, **extra))
        await self.db.flush()
        return cid

    def fp(self, name):
        return f"{self.token}-{name}"


def _by_name(out):
    return {i["test_name"]: i for i in out["items"]}


# ── 1. the latest FAILED row wins, inside the scope ─────────────────────────


async def test_the_newest_failing_row_wins_and_a_newer_pass_is_ignored(ctx):
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    p = await w.project()
    old_run =await w.run(p, hours_ago=30)
    new_run = await w.run(p, hours_ago=20)
    pass_run = await w.run(p, hours_ago=10)
    await w.case(old_run, "t", error_message="Old: first", stack_trace="old trace")
    newest = await w.case(new_run, "t", status="BROKEN", error_message="New: second\nline two",
                          stack_trace="new trace line 1\nnew trace line 2")
    await w.case(pass_run, "t", status="PASSED", error_message="passed rows never count")

    item = _by_name(await analytics_service.top_failing_tests(db, str(p), 30, 15))["t"]
    assert item["detail_available"] is True
    assert item["latest_test_case_id"] == str(newest)
    assert item["latest_run_id"] == str(new_run[0])
    assert item["latest_error"] == "New: second"
    assert item["latest_stack_head"] == "new trace line 1\nnew trace line 2"
    assert datetime.fromisoformat(item["latest_failed_at"]) == new_run[1]


async def test_a_row_outside_the_suite_or_release_is_not_the_latest(ctx):
    from app.models.postgres import Release
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    p = await w.project()
    rel = uuid.uuid4()
    db.add(Release(id=rel, project_id=p, name=f"fd-{w.token}", version="1.0.0", status="active"))
    await db.flush()
    in_scope = await w.run(p, hours_ago=30, release_id=rel)
    other_release = await w.run(p, hours_ago=20)
    other_suite = await w.run(p, hours_ago=10, release_id=rel, suite="Search")
    in_case = await w.case(in_scope, "t", error_message="in scope")
    await w.case(other_release, "t", error_message="another release")
    await w.case(other_suite, "t", suite="Search", error_message="another suite")

    by_release = _by_name(await analytics_service.top_failing_tests(db, str(p), 30, 15, release_id=str(rel)))
    # The release's newest failure of "t" is in the Search suite.
    assert by_release["t"]["latest_error"] == "another suite"
    scoped = _by_name(await analytics_service.top_failing_tests(
        db, str(p), 30, 15, suite_name="Checkout", release_id=str(rel),
    ))
    assert scoped["t"]["latest_test_case_id"] == str(in_case)
    assert scoped["t"]["latest_error"] == "in scope"


# ── 2. project scope ────────────────────────────────────────────────────────


async def test_another_projects_newer_failure_never_lends_its_row_error_or_owner(ctx):
    from app.models.postgres import ServiceOwnershipRule
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    mine, theirs = await w.project(), await w.project()
    theirs_owner = await w.user("Their Owner")
    db.add(ServiceOwnershipRule(project_id=theirs, match_type="suite_name", match_pattern="Checkout",
                                service_name="x", team_name="Their Team", priority=10))
    my_run = await w.run(mine, hours_ago=30)
    their_run = await w.run(theirs, hours_ago=1)
    my_case = await w.case(my_run, "shared", error_message="mine")
    await w.case(their_run, "shared", error_message="theirs", stack_trace="their trace",
                 assigned_to_user_id=theirs_owner)

    item = _by_name(await analytics_service.top_failing_tests(db, str(mine), 30, 15))["shared"]
    assert item["latest_test_case_id"] == str(my_case)
    assert item["latest_error"] == "mine"
    assert item["latest_stack_head"] is None
    assert (item["owner"], item["owner_team"]) == (None, None)


async def test_all_projects_carries_no_detail(ctx):
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    p = await w.project()
    await w.case(await w.run(p, hours_ago=5), "t", error_message="boom")
    out = await analytics_service.top_failing_tests(db, None, 30, 50, allowed_project_ids={p})
    item = _by_name(out)["t"]
    assert item["detail_available"] is False
    for key in ("latest_run_id", "latest_test_case_id", "latest_failed_at", "latest_error",
                "latest_stack_head", "owner", "owner_source", "owner_team"):
        assert item[key] is None, key


# ── 3. truncation and redaction on the stored text ──────────────────────────


async def test_truncated_and_redacted_before_it_leaves_the_server(ctx):
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    p = await w.project()
    trace = "\n".join(
        ["Authorization: Bearer abc.def.ghi", "password=hunter2", '  File "a.py", line 1234567890']
        + [f"  frame {i}" for i in range(100)]
    )
    await w.case(await w.run(p, hours_ago=2), "t", error_message="E" * 5000 + "\nsecond", stack_trace=trace)
    item = _by_name(await analytics_service.top_failing_tests(db, str(p), 30, 15))["t"]
    assert item["latest_error"] == "E" * 300
    lines = item["latest_stack_head"].split("\n")
    assert len(lines) == 20
    assert "abc.def.ghi" not in item["latest_stack_head"]
    assert "hunter2" not in item["latest_stack_head"]
    assert "line 1234567890" in item["latest_stack_head"]


# ── 4. owner precedence ─────────────────────────────────────────────────────


async def test_owner_precedence_assignee_then_rule_then_component_map_then_nobody(ctx):
    from app.models.postgres import Project, ServiceOwnershipRule
    from app.services import analytics_service

    db, _ = ctx
    w = World(db)
    p = await w.project()
    await db.execute(update(Project).where(Project.id == p).values(component_owner_map={"Cart*": {"team": "Cart Team"}}))
    db.add(ServiceOwnershipRule(project_id=p, match_type="suite_name", match_pattern="checkout*",
                                service_name="checkout", team_name="Payments", priority=10))
    priya = await w.user("Priya Nair")
    service = await w.user("CI Bot", is_service_account=True)
    synthetic = await w.user("Seed User", is_synthetic=True)
    inactive = await w.user("Gone Person", is_active=False)
    run = await w.run(p, hours_ago=3)
    await w.case(run, "assigned", assigned_to_user_id=priya)
    await w.case(run, "service", assigned_to_user_id=service)
    await w.case(run, "synthetic", assigned_to_user_id=synthetic)
    await w.case(run, "inactive", assigned_to_user_id=inactive)
    await w.case(run, "cart", suite="web", class_name="CartService")
    await w.case(run, "nobody", suite="web", class_name="Search")

    items = _by_name(await analytics_service.top_failing_tests(db, str(p), 30, 15))
    assert (items["assigned"]["owner"], items["assigned"]["owner_source"], items["assigned"]["owner_team"]) == (
        "Priya Nair", "assignee", "Payments",
    )
    for name in ("service", "synthetic", "inactive"):
        assert (items[name]["owner"], items[name]["owner_source"]) == ("Payments", "rule"), name
    assert (items["cart"]["owner"], items["cart"]["owner_source"]) == ("Cart Team", "component_map")
    assert (items["nobody"]["owner"], items["nobody"]["owner_source"], items["nobody"]["owner_team"]) == (
        None, None, None,
    )


# ── 5. statement count ──────────────────────────────────────────────────────


async def test_one_statement_when_every_row_has_an_eligible_assignee_three_when_rules_are_needed(ctx):
    from app.services.failure_detail_service import latest_failure_detail_by_fingerprint

    db, statements = ctx
    w = World(db)
    p = await w.project()
    priya = await w.user("Priya Nair")
    run = await w.run(p, hours_ago=3)
    for i in range(20):
        await w.case(run, f"owned{i}", assigned_to_user_id=priya)
        await w.case(run, f"free{i}")

    statements.clear()
    owned = await latest_failure_detail_by_fingerprint(db, p, [w.fp(f"owned{i}") for i in range(20)], days=30)
    assert len(owned) == 20 and len(statements) == 1
    statements.clear()
    free = await latest_failure_detail_by_fingerprint(db, p, [w.fp(f"free{i}") for i in range(20)], days=30)
    assert len(free) == 20 and len(statements) == 3


async def test_top_failing_adds_exactly_the_detail_statements_and_none_for_an_empty_list(ctx, monkeypatch):
    from app.services import analytics_service, failure_detail_service

    db, statements = ctx
    w = World(db)
    p = await w.project()
    priya = await w.user("Priya Nair")
    run = await w.run(p, hours_ago=3)
    for i in range(20):
        await w.case(run, f"owned{i}", assigned_to_user_id=priya)

    statements.clear()
    await analytics_service.top_failing_tests(db, str(p), 30, 20)
    with_detail = len(statements)

    async def _no_detail(*_a, **_k):
        return {}

    monkeypatch.setattr(failure_detail_service, "latest_failure_detail_by_fingerprint", _no_detail)
    statements.clear()
    await analytics_service.top_failing_tests(db, str(p), 30, 20)
    assert with_detail - len(statements) == 1
    monkeypatch.undo()

    empty = await w.project()
    statements.clear()
    out = await analytics_service.top_failing_tests(db, str(empty), 30, 20)
    assert out["items"] == [] and len(statements) == 1, "an empty top-N reads no detail"
