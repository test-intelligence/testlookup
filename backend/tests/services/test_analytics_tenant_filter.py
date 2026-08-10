"""Tests for the analytics service's tenant scoping helper.

Pins the P1-1 defence-in-depth contract: ``_tenant_filter`` translates
the (project_id, allowed_project_ids) tuple into the right SQL fragment
and parameter binds for every analytics raw-SQL query.

The helper is the single chokepoint that guarantees a non-admin caller
can't pass ``project_id=None`` and get cross-tenant data — even if a
future caller (agent stage, Celery task, new router) forgets the router-
layer access check.

Since the deleted-project fix it carries a **second** clause: soft-deleted
projects are excluded from every branch. Tenancy asks "who may see this
project"; life-cycle asks "does it still exist". The expectations below keep
full-string equality — the tenancy fragment is asserted exactly as before, with
the life-cycle fragment spelled out alongside it, so a regression in *either*
half still fails here. See
``tests/regression/test_analytics_exclude_deleted_projects.py``.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services.analytics_service import _tenant_filter  # noqa: E402


def _live(alias: str = "tr", column: str = "project_id") -> str:
    """The life-cycle half of the clause, for readable expectations."""
    return f"AND {alias}.{column} IN (SELECT id FROM projects WHERE is_active)"


def test_pinned_project_id_emits_equality_clause():
    params: dict = {}
    sql = _tenant_filter(
        params,
        project_id="11111111-1111-1111-1111-111111111111",
        allowed_project_ids=None,
    )

    assert sql == f"AND tr.project_id = :project_id {_live()}"
    assert params == {"project_id": "11111111-1111-1111-1111-111111111111"}


def test_pinned_project_id_with_custom_alias_and_column():
    params: dict = {}
    sql = _tenant_filter(
        params,
        project_id="abc",
        allowed_project_ids=None,
        table_alias="d",
        column="project_id",
    )

    assert sql == f"AND d.project_id = :project_id {_live('d')}"
    assert params == {"project_id": "abc"}


def test_no_project_id_unrestricted_when_allowed_is_none():
    """allowed_project_ids=None signals 'admin / unrestricted' **tenancy** —
    the service trusts that the caller (router) has gated admin access.

    It does NOT mean unrestricted full stop. This branch used to return the
    empty string, which is how the unscoped Coverage page came to be built
    entirely from soft-deleted projects (measured live: 27 suites across 13
    deleted projects, 0 live). Admin still sees every *live* project and no
    tenancy predicate is added."""
    params: dict = {"existing": "param"}
    sql = _tenant_filter(params, project_id=None, allowed_project_ids=None)

    assert sql == _live()
    assert "pid_0" not in sql and ":project_id" not in sql, (
        "the admin path must not gain a tenancy predicate"
    )
    # No new params injected — the life-cycle clause is a literal subquery.
    assert params == {"existing": "param"}


def test_no_project_id_with_empty_allowed_set_fails_closed():
    """A non-admin with zero project memberships must NOT see cross-project
    data. The helper emits ``AND FALSE`` so the query returns zero rows
    rather than dropping the filter."""
    params: dict = {}
    sql = _tenant_filter(params, project_id=None, allowed_project_ids=set())

    assert sql == "AND FALSE"
    assert params == {}


def test_no_project_id_with_allowed_set_emits_in_clause():
    """A non-admin with memberships gets a parameterised IN clause —
    NOT f-string interpolation of the UUIDs into the SQL text."""
    params: dict = {}
    pid_a = uuid.UUID("11111111-1111-1111-1111-111111111111")
    pid_b = uuid.UUID("22222222-2222-2222-2222-222222222222")
    sql = _tenant_filter(
        params, project_id=None, allowed_project_ids=[pid_a, pid_b],
    )

    assert sql == f"AND tr.project_id IN (:pid_0, :pid_1) {_live()}"
    assert params == {
        "pid_0": str(pid_a),
        "pid_1": str(pid_b),
    }


def test_allowed_set_uses_table_alias_and_column():
    params: dict = {}
    sql = _tenant_filter(
        params,
        project_id=None,
        allowed_project_ids=["aaa"],
        table_alias="d",
    )

    assert sql == f"AND d.project_id IN (:pid_0) {_live('d')}"
    assert params == {"pid_0": "aaa"}


def test_pinned_project_id_takes_precedence_over_allowed_set():
    """If both are passed (e.g. resolve_project_scope returns a pinned
    UUID for a non-admin who explicitly requested their own project),
    the pin wins — allowed_project_ids is ignored. The pin is already
    proof of access."""
    params: dict = {}
    sql = _tenant_filter(
        params,
        project_id="11111111-1111-1111-1111-111111111111",
        allowed_project_ids={"22222222-2222-2222-2222-222222222222"},
    )

    assert sql == f"AND tr.project_id = :project_id {_live()}"
    assert params == {"project_id": "11111111-1111-1111-1111-111111111111"}
    # IN-clause placeholders MUST NOT leak into params on the pinned path.
    assert "pid_0" not in params


def test_allowed_set_string_uuids_also_supported():
    """resolve_project_scope returns a set[UUID]; some upstream callers
    may pass plain strings. Both must work."""
    params: dict = {}
    sql = _tenant_filter(
        params, project_id=None, allowed_project_ids=["a", "b", "c"],
    )

    assert sql == f"AND tr.project_id IN (:pid_0, :pid_1, :pid_2) {_live()}"
    assert params == {"pid_0": "a", "pid_1": "b", "pid_2": "c"}
