"""Analytics must not aggregate over soft-deleted projects.

``_tenant_filter`` is the single scoping helper behind every analytics query
(9 call sites: flaky tests, failure categories, top-failing, coverage, defects,
…). It answers *who may see a project*. It did not answer *whether the project
still exists* — and ``DELETE /projects/{id}`` is a **soft** delete that only
flips ``is_active``.

On the admin path the tenancy clause is empty by design::

    if allowed_project_ids is None:
        return ""          # unrestricted

so the query ran with no project restriction at all and swept up every deleted
project. Measured against the live deployment (90-day window):

==============================  ===========  =========
surface                         API returns  DB, live
==============================  ===========  =========
``analytics/coverage`` execs        47,105         672
``analytics/coverage`` suites           27           7
``analytics/coverage`` tests         1,612          32
failures behind the categories       5,675          72
==============================  ===========  =========

The Coverage page was not merely inflated — it was built *entirely* from
unreachable data. All 27 suites belonged to 13 soft-deleted projects, every one
a ``ZZ … delete me`` throwaway, each rendered **with its deleted project's
name**. The deployment's two real projects did not appear on their own Coverage
page.

This is the fourth surface in this class (``my_failures`` → ``/runs`` #535 →
dashboard metrics #538 → analytics), which is why the fix goes in the shared
helper rather than on the endpoints: there is one place every analytics query
already passes through, and a tenth call site added later inherits it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services import analytics_service  # noqa: E402
from app.services.analytics_service import _tenant_filter  # noqa: E402

pytestmark = pytest.mark.regression


def _clause(project_id=None, allowed=None, **kw) -> str:
    return _tenant_filter(
        {}, project_id=project_id, allowed_project_ids=allowed, **kw
    )


class TestDeletedProjectsAreExcluded:
    def test_the_admin_path_excludes_them(self):
        """The measured bug: unrestricted tenancy meant unrestricted, full stop.

        ``allowed_project_ids=None`` is the admin/unscoped path — the one that
        produced 47,105 executions where only 672 were reachable.
        """
        clause = _clause(allowed=None)
        assert "is_active" in clause, (
            "the unscoped analytics path applies no project filter at all, so "
            "it aggregates soft-deleted projects — measured live at 47,105 "
            "executions against 672 belonging to live projects"
        )

    def test_a_membership_scope_still_excludes_them(self):
        """A member of a since-deleted project must not see it either."""
        clause = _clause(allowed=["11111111-1111-1111-1111-111111111111"])
        assert "is_active" in clause

    def test_a_pinned_project_still_excludes_them(self):
        """Consistency with /runs (#535) and dashboard metrics (#538), which
        both filter unconditionally: a deleted project reads as empty
        everywhere rather than empty on some surfaces and populated here."""
        clause = _clause(project_id="22222222-2222-2222-2222-222222222222")
        assert "is_active" in clause


class TestTenancyIsNotWeakened:
    """The life-cycle filter is *additional*. Losing a tenancy clause while
    adding it would trade an over-count for a cross-tenant leak."""

    def test_a_pin_still_binds_the_project(self):
        params: dict = {}
        clause = _tenant_filter(
            params, project_id="33333333-3333-3333-3333-333333333333",
            allowed_project_ids=None,
        )
        assert "tr.project_id = :project_id" in clause
        assert params["project_id"] == "33333333-3333-3333-3333-333333333333"

    def test_a_membership_set_still_binds_every_id(self):
        params: dict = {}
        ids = ["44444444-4444-4444-4444-444444444444",
               "55555555-5555-5555-5555-555555555555"]
        clause = _tenant_filter(
            params, project_id=None, allowed_project_ids=ids,
        )
        assert ":pid_0" in clause and ":pid_1" in clause
        assert params["pid_0"] == ids[0] and params["pid_1"] == ids[1]

    def test_an_empty_membership_set_still_fails_closed(self):
        """``AND FALSE`` returns nothing already — it must not be softened
        into a life-cycle filter that would let live projects through."""
        assert _clause(allowed=[]) == "AND FALSE"

    def test_it_honours_a_custom_alias(self):
        """``/analytics/defects`` scopes on ``d.project_id``; the life-cycle
        clause must follow the alias rather than hardcoding ``tr``."""
        clause = _clause(allowed=None, table_alias="d")
        assert "d.project_id IN (SELECT id FROM projects WHERE is_active)" in clause
        assert "tr." not in clause


def test_the_filter_keys_on_the_project_not_the_run():
    """Guards a plausible wrong fix: filtering on the run's own status."""
    clause = _clause(allowed=None)
    assert "FROM projects" in clause, (
        "the exclusion must key on the project's is_active flag"
    )


# ── Behavioural: run the real SQL, not a string comparison ─────────────────

LIVE_PROJECT = "11111111-1111-1111-1111-111111111111"
DELETED_PROJECT = "99999999-9999-9999-9999-999999999999"

DDL = [
    "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, is_active BOOLEAN DEFAULT 1)",
    "CREATE TABLE test_runs (id TEXT PRIMARY KEY, project_id TEXT, primary_suite_name TEXT)",
    """CREATE TABLE test_cases (
           id TEXT PRIMARY KEY, test_fingerprint TEXT, test_name TEXT,
           suite_name TEXT, class_name TEXT, test_run_id TEXT,
           triage_status TEXT, triage_updated_at TIMESTAMP
       )""",
    """CREATE TABLE test_case_history (
           id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id TEXT, test_run_id TEXT,
           test_fingerprint TEXT, status TEXT, created_at TIMESTAMP
       )""",
]


@pytest.fixture
async def session():
    """Two projects, one deleted, each with an identically flaky test.

    The string assertions above prove the clause is *emitted*. This proves it
    *works* — the clause is interpolated into ~20 hand-written SQL strings, so
    a syntactically valid fragment in the wrong place would satisfy every
    assertion above and still return deleted rows.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
        for pid, name, active in (
            (LIVE_PROJECT, "Checkout Service", 1),
            (DELETED_PROJECT, "ZZ Probe - delete me", 0),
        ):
            await conn.execute(
                text(
                    "INSERT INTO projects (id, name, is_active)"
                    " VALUES (:i, :n, :a)"
                ),
                {"i": pid, "n": name, "a": active},
            )
        base = datetime.now(timezone.utc) - timedelta(days=1)
        for pid, test_name in (
            (LIVE_PROJECT, "test_live_flake"),
            (DELETED_PROJECT, "test_deleted_flake"),
        ):
            case_id = str(uuid.uuid4())
            fp = f"fp_{test_name}"
            await conn.execute(
                text(
                    "INSERT INTO test_cases"
                    " (id, test_fingerprint, test_name, suite_name, class_name)"
                    " VALUES (:i, :f, :n, 'regression', 'Cls')"
                ),
                {"i": case_id, "f": fp, "n": test_name},
            )
            # p-f-f-p-p: two flips, enough to register as flaky.
            for idx, status in enumerate(
                ["PASSED", "FAILED", "FAILED", "PASSED", "PASSED"]
            ):
                run_id = str(uuid.uuid4())
                await conn.execute(
                    text(
                        "INSERT INTO test_runs (id, project_id, primary_suite_name)"
                        " VALUES (:i, :p, 'regression')"
                    ),
                    {"i": run_id, "p": pid},
                )
                await conn.execute(
                    text(
                        "INSERT INTO test_case_history"
                        " (test_case_id, test_run_id, test_fingerprint, status, created_at)"
                        " VALUES (:c, :r, :f, :st, :ts)"
                    ),
                    {
                        "c": case_id,
                        "r": run_id,
                        "f": fp,
                        "st": status,
                        "ts": base + timedelta(minutes=idx),
                    },
                )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_unscoped_query_returns_only_the_live_project(session):
    """The live reproduction, in miniature: admin, no project_id."""
    result = await analytics_service.flaky_tests(
        session, None, days=30, limit=50, allowed_project_ids=None
    )
    names = {row["test_name"] for row in result["items"]}
    assert "test_deleted_flake" not in names, (
        "the unscoped analytics query returned a test belonging to a "
        "soft-deleted project — the defect that put 13 deleted projects on "
        "the Coverage page"
    )
    assert "test_live_flake" in names, (
        "the live project's flaky test disappeared — the filter over-reached"
    )


@pytest.mark.asyncio
async def test_pinning_a_deleted_project_returns_nothing(session):
    """Consistent with /runs (#535) and metrics (#538): a deleted project
    reads as empty everywhere, not empty on some surfaces and populated here."""
    result = await analytics_service.flaky_tests(
        session, DELETED_PROJECT, days=30, limit=50, allowed_project_ids=None
    )
    assert result["items"] == []
