"""W12 SRCH-04 against real Postgres: the search box's filters through ``GET /search/global``.

The unit tests (``tests/test_global_search_filters.py``) pin the SQL shape;
this pins what it returns: ``status:failed suite:"API Regression" since:7d
checkout`` finds only failed results of that suite from the last week that
mention "checkout" -- INCLUDING a live-stream run's result whose per-event
suite is a class name (its effective suite is the run's label) -- and a
``release:`` naming another tenant's release finds nothing, with the same
sentence as an unknown one.

The handler is called directly (plain defaults) on a real session, with a
real non-admin user who is a member of project A only, so the project scope
and the release resolution run for real.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a disposable database migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import User, UserRole

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _World:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.project_a, self.project_b = uuid.uuid4(), uuid.uuid4()
        self.user = uuid.uuid4()
        self.release_a, self.release_b = uuid.uuid4(), uuid.uuid4()
        self.run_upload, self.run_live, self.run_old, self.run_b = (uuid.uuid4() for _ in range(4))
        self.cases: dict[str, uuid.UUID] = {}
        self.defect = uuid.uuid4()

    async def create(self, conn) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        for pid, name in ((self.project_a, "a"), (self.project_b, "b")):
            await conn.execute(text(
                "INSERT INTO projects (id, name, slug, is_active) VALUES (:i, :n, :n, true)"
            ), {"i": pid, "n": f"w12gb-{name}-{self.tag}"})
        await conn.execute(text(
            "INSERT INTO users (id, email, username, hashed_password, role, is_active, is_service_account, "
            "is_synthetic, must_change_password, mfa_enabled, failed_login_attempts) "
            "VALUES (:i, :e, :u, 'x', 'QA_ENGINEER', true, false, false, false, false, 0)"
        ), {"i": self.user, "e": f"w12gb-{self.tag}@example.com", "u": f"w12gb_{self.tag}"})
        await conn.execute(text(
            "INSERT INTO project_members (id, user_id, project_id, role) VALUES (:i, :u, :p, 'QA_ENGINEER')"
        ), {"i": uuid.uuid4(), "u": self.user, "p": self.project_a})
        for rid, pid, name in ((self.release_a, self.project_a, "2026.10"), (self.release_b, self.project_b, "2026.11")):
            await conn.execute(text(
                "INSERT INTO releases (id, project_id, name, status) VALUES (:i, :p, :n, 'planning')"
            ), {"i": rid, "p": pid, "n": name})
        runs = (
            (self.run_upload, self.project_a, "upload", None, "main", self.release_a, now),
            (self.run_live, self.project_a, "live_stream", "API Regression", "main", None, now),
            (self.run_old, self.project_a, "upload", None, "main", None, old),
            (self.run_b, self.project_b, "upload", None, "main", None, now),
        )
        for rid, pid, source, label, branch, release, created in runs:
            await conn.execute(text(
                "INSERT INTO test_runs (id, project_id, build_number, status, trigger_source, "
                "primary_suite_name, branch, primary_release_id, created_at) "
                "VALUES (:i, :p, :b, 'FAILED', :s, :l, :br, :r, :c)"
            ), {"i": rid, "p": pid, "b": f"b-{self.tag}", "s": source, "l": label, "br": branch,
                "r": release, "c": created})
        cases = (
            ("match_upload", self.run_upload, f"checkout-{self.tag} pays", "FAILED", "API Regression", now),
            ("passed", self.run_upload, f"checkout-{self.tag} pays", "PASSED", "API Regression", now),
            ("other_suite", self.run_upload, f"checkout-{self.tag} other", "FAILED", "UI", now),
            ("no_text", self.run_upload, f"login-{self.tag}", "FAILED", "API Regression", now),
            # The live-stream result: its per-event suite is a class name; its EFFECTIVE suite is the label.
            ("match_live", self.run_live, f"checkout-{self.tag} live", "FAILED", "com.example.CheckoutTest", now),
            ("too_old", self.run_old, f"checkout-{self.tag} old", "FAILED", "API Regression", old),
            ("other_tenant", self.run_b, f"checkout-{self.tag} b", "FAILED", "API Regression", now),
        )
        for name, run, title, status, suite, created in cases:
            case_id = uuid.uuid4()
            self.cases[name] = case_id
            await conn.execute(text(
                "INSERT INTO test_cases (id, test_run_id, test_name, status, suite_name, test_fingerprint, created_at) "
                "VALUES (:i, :r, :n, :s, :su, :f, :c)"
            ), {"i": case_id, "r": run, "n": title, "s": status, "su": suite, "f": f"fp-{name}-{self.tag}",
                "c": created})

    async def destroy(self, conn) -> None:
        runs = [self.run_upload, self.run_live, self.run_old, self.run_b]
        await conn.execute(text("DELETE FROM test_cases WHERE test_run_id = ANY(:r)"), {"r": runs})
        await conn.execute(text("DELETE FROM test_runs WHERE id = ANY(:r)"), {"r": runs})
        await conn.execute(text("DELETE FROM releases WHERE id = ANY(:r)"), {"r": [self.release_a, self.release_b]})
        await conn.execute(text("DELETE FROM project_members WHERE user_id = :u"), {"u": self.user})
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": self.user})
        await conn.execute(
            text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": [self.project_a, self.project_b]},
        )

    def member(self) -> User:
        return User(
            id=self.user, username=f"w12gb_{self.tag}", email=f"w12gb-{self.tag}@example.com",
            role=UserRole.QA_ENGINEER, is_active=True,
        )


@pytest.fixture
async def world():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = _World()
    async with engine.begin() as conn:
        await w.create(conn)
    try:
        yield w, factory
    finally:
        async with engine.begin() as conn:
            await w.destroy(conn)
        await engine.dispose()


async def _search(factory, user, q, *, project_id=None, entity_types=None):
    from app.routers.search import global_search_endpoint

    async with factory() as db:
        return await global_search_endpoint(
            q=q, project_id=project_id, entity_types=entity_types, days=None, page=1, size=50,
            db=db, current_user=user,
        )


async def test_status_suite_since_and_text_together(world):
    w, factory = world
    q = f'status:failed suite:"API Regression" since:7d checkout-{w.tag}'
    result = await _search(factory, w.member(), q, project_id=str(w.project_a), entity_types="test_case")
    found = {item["entity_id"] for item in result["items"]}
    assert found == {str(w.cases["match_upload"]), str(w.cases["match_live"])}, (
        "expected only the failed API Regression results of the last week mentioning checkout, "
        "the live-stream one (effective suite = its run's label) included"
    )
    parsed = result["parsed"]
    assert [f["raw"] for f in parsed["filters"]] == ["status:failed", 'suite:"API Regression"', "since:7d"]
    assert parsed["text"] == f"checkout-{w.tag}"
    assert parsed["notes"] == []
    assert result["scope"]["release"] == "not_applicable"


async def test_the_member_scope_still_holds_with_filters(world):
    w, factory = world
    result = await _search(factory, w.member(), f"status:failed checkout-{w.tag}", entity_types="test_case")
    found = {item["entity_id"] for item in result["items"]}
    assert str(w.cases["other_tenant"]) not in found
    assert str(w.cases["match_upload"]) in found and str(w.cases["too_old"]) in found


async def test_branch_on_defects_is_not_searched_and_says_so(world):
    w, factory = world
    result = await _search(factory, w.member(), "branch:main checkout", entity_types="defect")
    assert result["items"] == []
    assert result["parsed"]["unsupported_by"] == {"defect": ["branch"]}
    assert result["parsed"]["notes"] == ["branch: is not supported for Defects, so no defects were searched."]


async def test_an_unknown_key_is_text_and_reported(world):
    w, factory = world
    result = await _search(factory, w.member(), f"foo:bar {w.tag}", entity_types="test_case")
    assert result["items"] == []  # "foo:bar <tag>" is matched as text, and nothing holds it
    assert result["parsed"]["text"] == f"foo:bar {w.tag}"
    assert result["parsed"]["notes"] == ["unknown filter foo: searched as text"]


async def test_a_release_of_another_tenant_finds_nothing_like_an_unknown_one(world):
    w, factory = world
    foreign = await _search(factory, w.member(), f'release:"2026.11" {w.tag}')
    unknown = await _search(factory, w.member(), f'release:"9.9.9-{w.tag}" {w.tag}')
    by_id = await _search(factory, w.member(), f"release:{w.release_b} {w.tag}")
    assert foreign["items"] == [] and unknown["items"] == [] and by_id["items"] == []
    assert foreign["parsed"]["notes"][-1] == 'No release named "2026.11" in your projects.'
    assert by_id["parsed"]["notes"][-1] == f'No release named "{w.release_b}" in your projects.'
    assert unknown["parsed"]["release_unresolved"] == f"9.9.9-{w.tag}"


async def test_a_release_in_scope_filters_tests_and_releases(world):
    w, factory = world
    result = await _search(factory, w.member(), f"release:2026.10 checkout-{w.tag}")
    tests = {i["entity_id"] for i in result["items"] if i["entity_type"] == "test_case"}
    assert tests == {str(w.cases["match_upload"]), str(w.cases["passed"]), str(w.cases["other_suite"])}
    assert result["parsed"]["release_unresolved"] is None
    assert "defect" in result["parsed"]["unsupported_by"]
