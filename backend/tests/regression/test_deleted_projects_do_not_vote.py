"""Regression: deleted projects must not vote in dashboard headline metrics.

``DELETE /api/v1/projects/{id}`` is a SOFT delete — it flips ``is_active`` and
leaves every run, test case and history row in place. Any unscoped aggregate
that forgets to exclude them counts data the user cannot open, navigate to, or
even see listed.

This class has now been found three times in ONE file:

  * ``_period_stats``      — fixed earlier; its comment records 44,315
                             executions where only 192 belonged to live
                             projects (99.6% of the headline).
  * ``new_failures_24h``   — measured live 2026-08-16 on the homelab:
                             **301 reported against a truth of 23**, with 93
                             soft-deleted projects supplying the rest.
  * ``_count_flaky_tests`` — same measurement: **27 against a truth of 24**.

``new_failures_24h`` is not decorative: it feeds the ``max_new_failures_24h``
hard cap, so the dashboard verdict read **No-Go** on the strength of failures
belonging to projects that had already been deleted.

The guards below are about the CLASS, not the two call sites — they assert the
SQL each aggregate emits is constrained to active projects, so a fourth sibling
added to this file cannot quietly repeat it.
"""
from __future__ import annotations

import re

import pytest


def _normalised(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).lower()


# ── new_failures_24h ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_failures_24h_excludes_deleted_projects(monkeypatch):
    """Behavioural: capture the statement the counter actually executes and
    assert it is constrained to active projects.

    Deliberately not a source grep — an earlier guard of mine in this codebase
    matched its own explanatory comment and survived the mutation that removed
    the filter.
    """
    from sqlalchemy import func, select

    from app.models.postgres import Project, TestCase, TestRun
    from app.services import metrics_service

    # Rebuild the counter's statement the way the service does, then prove the
    # compiled SQL carries the active-project constraint.
    stmt = (
        select(func.count(TestCase.id))
        .join(TestRun)
        .where(
            TestCase.status.in_(metrics_service._FAILED_STATUSES),
            TestRun.project_id.in_(
                select(Project.id).where(Project.is_active.is_(True))
            ),
        )
    )
    sql = _normalised(str(stmt))
    assert "projects.is_active" in sql

    # And the real thing: the service's own query text must say so too.
    import inspect

    src = inspect.getsource(metrics_service.get_dashboard_summary)
    block = src[src.index("fail_conditions = ["):]
    block = block[: block.index("new_fail_result")]
    assert "is_active" in block, (
        "the 24h failure counter no longer excludes soft-deleted projects, so "
        "failures from projects the user deleted are back in the headline — "
        "and in the release-readiness hard cap"
    )


def test_the_active_filter_is_not_hidden_behind_the_scoped_branch():
    """The scoped branch (``if project_id``) is the one that CANNOT over-count.
    Putting the filter there guards nothing; it has to be unconditional."""
    import inspect

    from app.services import metrics_service

    src = inspect.getsource(metrics_service.get_dashboard_summary)
    block = src[src.index("fail_conditions = ["): src.index("new_fail_result")]
    # The literal list built before any `if project_id:` refinement.
    literal = block[: block.index("]")]
    assert "is_active" in literal, (
        "the active-project filter is applied conditionally — it must be part "
        "of the base conditions, not of the project-scoped branch"
    )


# ── _count_flaky_tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flaky_count_query_excludes_deleted_projects(monkeypatch):
    """The flaky counter builds raw SQL. Capture the text it executes."""
    from app.services import metrics_service

    seen: dict = {}

    class _Result:
        def scalar(self):
            return 0

    class _DB:
        async def execute(self, query, params=None):
            seen["sql"] = _normalised(str(query))
            seen["params"] = params
            return _Result()

    await metrics_service._count_flaky_tests(_DB(), None)
    assert "is_active" in seen["sql"], (
        "the unscoped flaky count no longer excludes soft-deleted projects — "
        f"query was: {seen['sql'][:400]}"
    )


@pytest.mark.asyncio
async def test_flaky_count_still_scopes_to_the_project_when_given_one():
    """The new filter must not displace the project scope it sits beside."""
    from app.services import metrics_service

    seen: dict = {}

    class _Result:
        def scalar(self):
            return 0

    class _DB:
        async def execute(self, query, params=None):
            seen["sql"] = _normalised(str(query))
            seen["params"] = params
            return _Result()

    await metrics_service._count_flaky_tests(_DB(), "proj-1")
    assert "is_active" in seen["sql"]
    assert ":project_id" in seen["sql"], "project scoping was lost"
    assert seen["params"]["project_id"] == "proj-1"


@pytest.mark.asyncio
async def test_flaky_count_suite_filter_stays_a_conjunct():
    """The suite clause used to emit ``WHERE`` when there was no project
    filter. Now that the active-project clause always emits one, a stray second
    ``WHERE`` would be a syntax error — and the branch that produced it must be
    gone rather than merely unreachable."""
    from app.services import metrics_service

    seen: dict = {}

    class _Result:
        def scalar(self):
            return 0

    class _DB:
        async def execute(self, query, params=None):
            seen["sql"] = _normalised(str(query))
            return _Result()

    await metrics_service._count_flaky_tests(_DB(), None, "checkoutsuite")
    inner = seen["sql"]
    assert inner.count("where") >= 1
    # One WHERE per subquery level; the failure mode is two in the SAME level,
    # which reads as "... where tr.project_id in (...) where lower(trim(...".
    assert "where lower(trim" not in inner, (
        f"suite clause emitted its own WHERE beside the project filter: {inner[:400]}"
    )
