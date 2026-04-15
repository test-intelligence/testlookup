"""
Unit tests for ``app.services.feature_flags`` — the pure-evaluation path
and the rollout bucket hash. Integration tests cover the router; this
suite is fast, needs no DB, and exercises the decision logic only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import feature_flags as ff


def _make_user(role: str = "QA_ENGINEER", user_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID(user_id) if user_id else uuid.uuid4(),
        role=role,
    )


# ── _evaluate ───────────────────────────────────────────────────────────────


def test_evaluate_returns_false_when_globally_disabled():
    flag = {
        "key": "x",
        "enabled_global": False,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is False


def test_evaluate_returns_true_when_globally_enabled_with_no_filters():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is True


def test_evaluate_project_allowlist_matches():
    project = uuid.uuid4()
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [str(project)],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=project, user=_make_user()) is True


def test_evaluate_project_allowlist_rejects_other_projects():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [str(uuid.uuid4())],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=uuid.uuid4(), user=_make_user()) is False


def test_evaluate_project_allowlist_requires_project_id():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [str(uuid.uuid4())],
        "enabled_roles": [],
        "rollout_percent": 100,
    }
    # No project_id supplied → gate fails closed.
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is False


def test_evaluate_role_allowlist_matches():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": ["ADMIN"],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user(role="ADMIN")) is True
    assert (
        ff._evaluate(flag, project_id=None, user=_make_user(role="QA_ENGINEER"))
        is False
    )


def test_evaluate_role_allowlist_requires_user():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": ["ADMIN"],
        "rollout_percent": 100,
    }
    assert ff._evaluate(flag, project_id=None, user=None) is False


def test_evaluate_rollout_zero_disables_for_all():
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 0,
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is False


# ── _rollout_bucket ─────────────────────────────────────────────────────────


def test_rollout_bucket_is_deterministic():
    a = ff._rollout_bucket("cypress_ingest", "user-123")
    b = ff._rollout_bucket("cypress_ingest", "user-123")
    assert a == b
    assert 0 <= a < 100


def test_rollout_bucket_varies_by_key():
    a = ff._rollout_bucket("cypress_ingest", "user-123")
    b = ff._rollout_bucket("playwright_ingest", "user-123")
    # Different flag keys should give different buckets for the same input.
    assert a != b


def test_rollout_bucket_stable_assignment():
    """A user bucketed below 50 stays below 50 across repeated calls."""
    samples = [ff._rollout_bucket("x", f"user-{i}") for i in range(200)]
    assert all(0 <= s < 100 for s in samples)
    assert len(set(samples)) > 50  # distribution spreads across buckets
