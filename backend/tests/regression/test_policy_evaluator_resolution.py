"""Regression pins for the policy_evaluator_service review (review/policy-evaluator-service).

Fix: ``resolve_effective_policy`` resolved the active policy with an unordered
``LIMIT 1``. ``is_active`` has no DB-level single-active guarantee (only
``(project_id, version)`` is unique; "one active per scope" is enforced solely
in application code), so a concurrent publish race could leave two active rows —
after which policy resolution flipped nondeterministically between requests. The
queries now ``ORDER BY version DESC`` (latest published wins), matching the
/history endpoints.

These tests also pin the precedence chain: project → system → hardcoded.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression


class _CaptureDB:
    """Fake AsyncSession that records each executed statement's SQL and returns
    a scripted ``scalar_one_or_none`` per call."""

    def __init__(self, scripted_results):
        self._results = list(scripted_results)
        self.sql: list[str] = []
        self._i = 0

    async def execute(self, stmt):
        self.sql.append(str(stmt).lower())
        val = self._results[self._i] if self._i < len(self._results) else None
        self._i += 1
        return SimpleNamespace(scalar_one_or_none=lambda v=val: v)


@pytest.mark.asyncio
async def test_project_policy_query_orders_by_version_desc():
    from app.services.policy_evaluator_service import resolve_effective_policy

    policy = SimpleNamespace(id=uuid.uuid4(), version=7)
    db = _CaptureDB([policy])
    result, level = await resolve_effective_policy(uuid.uuid4(), db)

    assert (result, level) == (policy, "project")
    # the (only) executed query is the project-scope lookup, deterministically ordered
    assert "order by" in db.sql[0]
    assert "version desc" in db.sql[0]


@pytest.mark.asyncio
async def test_system_fallback_also_ordered_when_no_project_policy():
    from app.services.policy_evaluator_service import resolve_effective_policy

    system_policy = SimpleNamespace(id=uuid.uuid4(), version=3)
    # project query → None, system query → the system default
    db = _CaptureDB([None, system_policy])
    result, level = await resolve_effective_policy(uuid.uuid4(), db)

    assert (result, level) == (system_policy, "system")
    assert len(db.sql) == 2
    assert all("order by" in q and "version desc" in q for q in db.sql)
    # system query filters on project_id IS NULL
    assert "is null" in db.sql[1]


@pytest.mark.asyncio
async def test_hardcoded_when_no_active_policy():
    from app.services.policy_evaluator_service import resolve_effective_policy

    db = _CaptureDB([None, None])
    result, level = await resolve_effective_policy(uuid.uuid4(), db)
    assert result is None
    assert level == "hardcoded"


@pytest.mark.asyncio
async def test_no_project_id_skips_project_query():
    from app.services.policy_evaluator_service import resolve_effective_policy

    db = _CaptureDB([None])
    result, level = await resolve_effective_policy(None, db)
    assert (result, level) == (None, "hardcoded")
    # only the system-scope query ran (project branch skipped)
    assert len(db.sql) == 1
    assert "is null" in db.sql[0]


# ── rule escalation semantics (pure; policy_override avoids the DB) ─────────────

@pytest.mark.asyncio
async def test_failing_block_rule_forces_no_go():
    from app.services.policy_evaluator_service import evaluate_policy

    policy = SimpleNamespace(id=uuid.uuid4(), version=1, rules={
        "rules": [{
            "id": "r1", "name": "Flaky cap", "type": "flaky_recurrence",
            "enabled": True, "params": {"max_flaky_tests": 0, "action": "BLOCK"},
        }],
    })
    res = await evaluate_policy(
        project_id=None,
        dim_scores={"user_impact": 0.0},
        pass_rate=100.0,
        context={"flaky_count": 5},
        db=None,  # unused when policy_override is given
        policy_override=policy,
    )
    assert res.policy_level == "simulated"
    assert res.overall_result == "BLOCK"
    assert res.recommendation == "NO_GO"
    assert any((not e.passed) and e.action == "BLOCK" for e in res.rule_evaluations)


@pytest.mark.asyncio
async def test_failing_warn_rule_downgrades_go_to_conditional():
    from app.services.policy_evaluator_service import evaluate_policy

    policy = SimpleNamespace(id=uuid.uuid4(), version=1, rules={
        "rules": [{
            "id": "r1", "name": "Defect cap", "type": "open_defect_limit",
            "enabled": True, "params": {"max_open_defects": 0, "action": "WARN"},
        }],
    })
    res = await evaluate_policy(
        project_id=None,
        dim_scores={"user_impact": 0.0},  # composite 0 → base GO
        pass_rate=100.0,
        context={"open_defects": 3},
        db=None,
        policy_override=policy,
    )
    assert res.overall_result == "WARN"
    assert res.recommendation == "CONDITIONAL_GO"


def test_override_constraints():
    from app.services.policy_evaluator_service import check_override_constraints

    policy = SimpleNamespace(rules={"rules": [{
        "type": "override_rules", "enabled": True,
        "params": {"allow_override_to_go_from_no_go": False, "require_reason_min_length": 10},
    }]})

    # NO_GO → GO blocked outright
    ok, err = check_override_constraints(policy, "NO_GO", "GO", "anything")
    assert ok is False and "NO_GO to GO" in err
    # allowed transition but reason too short
    ok2, err2 = check_override_constraints(policy, "CONDITIONAL_GO", "GO", "short")
    assert ok2 is False and "at least 10" in err2
    # allowed transition + long enough reason
    ok3, err3 = check_override_constraints(policy, "CONDITIONAL_GO", "GO", "a sufficiently long reason")
    assert ok3 is True and err3 == ""
    # no policy → unconstrained
    assert check_override_constraints(None, "NO_GO", "GO", "")[0] is True
