"""Tests for the analytics service's tenant scoping helper.

Pins the P1-1 defence-in-depth contract: ``_tenant_filter`` translates
the (project_id, allowed_project_ids) tuple into the right SQL fragment
and parameter binds for every analytics raw-SQL query.

The helper is the single chokepoint that guarantees a non-admin caller
can't pass ``project_id=None`` and get cross-tenant data — even if a
future caller (agent stage, Celery task, new router) forgets the router-
layer access check.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services.analytics_service import _tenant_filter  # noqa: E402


def test_pinned_project_id_emits_equality_clause():
    params: dict = {}
    sql = _tenant_filter(
        params,
        project_id="11111111-1111-1111-1111-111111111111",
        allowed_project_ids=None,
    )

    assert sql == "AND tr.project_id = :project_id"
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

    assert sql == "AND d.project_id = :project_id"
    assert params == {"project_id": "abc"}


def test_no_project_id_unrestricted_when_allowed_is_none():
    """allowed_project_ids=None signals 'admin / unrestricted'. This
    matches the historical behaviour and is intentional — the service
    trusts that the caller (router) has gated admin access."""
    params: dict = {"existing": "param"}
    sql = _tenant_filter(params, project_id=None, allowed_project_ids=None)

    assert sql == ""
    # No new params injected.
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

    assert sql == "AND tr.project_id IN (:pid_0, :pid_1)"
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

    assert sql == "AND d.project_id IN (:pid_0)"
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

    assert sql == "AND tr.project_id = :project_id"
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

    assert sql == "AND tr.project_id IN (:pid_0, :pid_1, :pid_2)"
    assert params == {"pid_0": "a", "pid_1": "b", "pid_2": "c"}
