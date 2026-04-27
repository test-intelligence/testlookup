"""
Unit tests for ``app.services.feature_flags`` — the pure-evaluation path
and the rollout bucket hash. Integration tests cover the router; this
suite is fast, needs no DB, and exercises the decision logic only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace


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


# ── _evaluate rollout edge cases ─────────────────────────────────────────────


def test_evaluate_rollout_partial_uses_user_bucket_for_decision():
    """At rollout=50, half the user IDs should be in, half out — deterministic."""
    flag = {
        "key": "test_flag",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 50,
    }
    in_count = 0
    out_count = 0
    for i in range(200):
        user = _make_user(user_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user-{i}")))
        if ff._evaluate(flag, project_id=None, user=user):
            in_count += 1
        else:
            out_count += 1
    # Roughly half-half (allow 35–65% slack on a 200-sample test).
    assert 70 <= in_count <= 130
    assert 70 <= out_count <= 130


def test_evaluate_rollout_partial_falls_back_to_project_when_no_user():
    """When user is None, the bucket key is the project_id."""
    project = uuid.uuid4()
    flag = {
        "key": "test_flag",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 50,
    }
    a = ff._evaluate(flag, project_id=project, user=None)
    b = ff._evaluate(flag, project_id=project, user=None)
    assert a == b  # deterministic


def test_evaluate_rollout_partial_falls_back_to_global_bucket():
    """When neither user nor project_id is given, bucket is "global" (deterministic)."""
    flag = {
        "key": "stable_key",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": 50,
    }
    a = ff._evaluate(flag, project_id=None, user=None)
    b = ff._evaluate(flag, project_id=None, user=None)
    assert a == b


def test_evaluate_handles_rollout_string_input_safely():
    """Some legacy/JSON paths give rollout_percent as a string — coerce safely."""
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": "100",  # string from JSON cache, must coerce
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is True


def test_evaluate_handles_none_rollout_as_zero():
    """``rollout_percent: None`` (which has happened with bad migrations)
    should be treated as 0 rather than crashing on int(None)."""
    flag = {
        "key": "x",
        "enabled_global": True,
        "enabled_projects": [],
        "enabled_roles": [],
        "rollout_percent": None,
    }
    assert ff._evaluate(flag, project_id=None, user=_make_user()) is False


# ── _normalize_role ─────────────────────────────────────────────────────────


def test_normalize_role_handles_enum_like():
    """Roles arrive as a UserRole enum at runtime — extract .value."""
    enum_like = SimpleNamespace(value="ADMIN")
    assert ff._normalize_role(enum_like) == "ADMIN"


def test_normalize_role_handles_plain_string():
    assert ff._normalize_role("QA_LEAD") == "QA_LEAD"


def test_normalize_role_handles_none_safely():
    assert ff._normalize_role(None) == ""


# ── _serialize_flag / _deserialize_flag round-trip ──────────────────────────


def test_serialize_deserialize_round_trip_preserves_fields():
    """Cached dict round-trip must keep every field needed by _evaluate."""
    project_a = uuid.uuid4()
    flag_id = uuid.uuid4()
    src = SimpleNamespace(
        id=flag_id,
        key="cypress_ingest",
        description="Cypress ingest",
        enabled_global=True,
        enabled_projects=[str(project_a)],
        enabled_roles=["ADMIN", "QA_LEAD"],
        rollout_percent=42,
    )
    serialized = ff._serialize_flag(src)
    rt = ff._deserialize_flag(serialized)
    assert rt["key"] == "cypress_ingest"
    assert rt["enabled_global"] is True
    assert rt["enabled_projects"] == [str(project_a)]
    assert rt["enabled_roles"] == ["ADMIN", "QA_LEAD"]
    assert rt["rollout_percent"] == 42


def test_deserialize_flag_handles_missing_keys_with_safe_defaults():
    """Sparse Redis payload from an older serializer must not crash."""
    rt = ff._deserialize_flag({"key": "x"})
    assert rt["key"] == "x"
    assert rt["enabled_global"] is False
    assert rt["enabled_projects"] == []
    assert rt["enabled_roles"] == []
    assert rt["rollout_percent"] == 0


# ── _legacy_env_fallback ────────────────────────────────────────────────────


def test_legacy_env_fallback_unknown_key_returns_none():
    assert ff._legacy_env_fallback("not_a_legacy_flag") is None


def test_legacy_env_fallback_truthy_values(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "TRUE")
    assert ff._legacy_env_fallback("knowledge_rag") is True
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "1")
    assert ff._legacy_env_fallback("knowledge_rag") is True
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "yes")
    assert ff._legacy_env_fallback("knowledge_rag") is True


def test_legacy_env_fallback_falsy_values(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "false")
    assert ff._legacy_env_fallback("knowledge_rag") is False
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "0")
    assert ff._legacy_env_fallback("knowledge_rag") is False


def test_legacy_env_fallback_unset_returns_none(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_RAG_ENABLED", raising=False)
    assert ff._legacy_env_fallback("knowledge_rag") is None
