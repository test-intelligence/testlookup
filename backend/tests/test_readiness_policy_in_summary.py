"""W11 GATE-04: the dashboard summary names the policy its verdict was decided by.

The pages draw the pass-rate GO line from the policy's ``yellow_min`` and shade
the No-Go zone below ``orange_min``; they can only do that, and say WHICH policy
decided, when the summary carries the policy beside the verdict.

``resolve_readiness_policy`` keeps ``_resolve_active_policy_for_project`` (three
columns, the one precedence implementation) and reads the name in ONE more
statement keyed on the resolved id -- only when a policy resolved, and only when
the window holds evidence (no evidence: no policy statement at all, as today).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import DashboardSummary
from app.services import metrics_service

PROJECT_ID = str(uuid.uuid4())
PROJECT_POLICY_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
SYSTEM_POLICY_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")
BANDS = {"orange_min": 92.0, "yellow_min": 97.0, "green_min": 99.0}


def _stats(pass_rate: float, runs: int) -> dict:
    return {
        "total_runs": runs,
        "total_executions": runs * 10,
        "pass_rate": pass_rate,
        "avg_duration_ms": 1000,
    }


class _Session:
    """Answers the summary's statements by what they select, and records them."""

    def __init__(self, *, project=None, system=None, names=None):
        self.project = project
        self.system = system
        self.names = names or {}
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, *a, **kw):
        compiled = stmt.compile()
        sql = " ".join(str(compiled).split())
        self.statements.append(sql)
        self.params.append(dict(compiled.params))
        res = MagicMock()
        if sql.startswith("SELECT release_gate_policies.rules, release_gate_policies.id"):
            row = self.system if "project_id IS NULL" in sql else self.project
            res.first = MagicMock(return_value=row)
            return res
        if sql.startswith("SELECT release_gate_policies.name"):
            wanted = next(iter(compiled.params.values()))
            res.scalar = MagicMock(return_value=self.names.get(str(wanted)))
            return res
        res.scalar = MagicMock(return_value=0)
        return res

    def policy_statements(self) -> list[str]:
        return [s for s in self.statements if "release_gate_policies" in s]

    def name_statements(self) -> list[str]:
        return [s for s in self.statements if s.startswith("SELECT release_gate_policies.name")]


@pytest.fixture
def wired(monkeypatch):
    """The summary with the stats readers faked and the cache bypassed."""
    from app.services import cache_service

    state = SimpleNamespace(cur=_stats(98.0, 4), prev=_stats(96.0, 4))
    calls: list[int] = []

    async def _period_stats(db, project_id, start, end, suite, release_id):
        calls.append(1)
        return state.cur if len(calls) == 1 else state.prev

    monkeypatch.setattr(metrics_service, "_period_stats", _period_stats)
    monkeypatch.setattr(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0))
    monkeypatch.setattr(metrics_service, "count_open_critical_defects", AsyncMock(return_value=0))
    monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=None))
    monkeypatch.setattr(cache_service, "cache_set", AsyncMock(return_value=None))
    return state


async def test_a_project_policy_is_named_with_its_bands(wired):
    db = _Session(
        project=({"pass_rate_bands": BANDS}, PROJECT_POLICY_ID, 4),
        names={str(PROJECT_POLICY_ID): "Payments gate"},
    )

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    assert out["readiness_policy"] == {
        "id": str(PROJECT_POLICY_ID),
        "name": "Payments gate",
        "version": 4,
        "level": "project",
        "bands": BANDS,
    }
    # The response model reads it (the route returns the dict as is).
    assert DashboardSummary.model_validate(out).readiness_policy.name == "Payments gate"


async def test_the_verdict_is_the_one_the_policy_decided_today(wired):
    """98 % against a 97 / 99 policy is yellow = GO, exactly as before W11."""
    db = _Session(
        project=({"pass_rate_bands": BANDS}, PROJECT_POLICY_ID, 4),
        names={str(PROJECT_POLICY_ID): "Payments gate"},
    )

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    expected = metrics_service.classify_with_policy(
        pass_rate=98.0, active_defects_p0=0, flaky_count=0, new_failures_24h=0,
        bands=BANDS, hard_caps={},
    )
    assert out["release_readiness_band"] == expected["band"] == "yellow"
    assert out["release_readiness"] == "GREEN"
    assert out["release_readiness_downgrades"] == []


async def test_only_a_system_policy_reports_the_system_level(wired):
    db = _Session(
        project=None,
        system=({"pass_rate_bands": {"yellow_min": 96}}, SYSTEM_POLICY_ID, 2),
        names={str(SYSTEM_POLICY_ID): "Deployment default"},
    )

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    policy = out["readiness_policy"]
    assert policy["level"] == "system"
    assert policy["name"] == "Deployment default"
    assert policy["version"] == 2
    # Per key: the missing lines keep the PolicyPassRateBands defaults.
    assert policy["bands"] == {"orange_min": 90.0, "yellow_min": 96.0, "green_min": 99.0}


async def test_no_policy_is_none_and_reads_no_name(wired):
    db = _Session(project=None, system=None)

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    assert out["readiness_policy"] is None
    assert db.name_statements() == []
    # The two precedence reads ran (project, then system), as before.
    assert len(db.policy_statements()) == 2


async def test_no_evidence_reads_no_policy_at_all(wired):
    """An empty window keeps today's statements: no policy statement of any kind."""
    wired.cur = _stats(0.0, 0)
    db = _Session(
        project=({"pass_rate_bands": BANDS}, PROJECT_POLICY_ID, 4),
        names={str(PROJECT_POLICY_ID): "Payments gate"},
    )

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    assert out["readiness_policy"] is None
    assert out["release_readiness"] is None
    assert db.policy_statements() == []


async def test_the_name_is_one_statement_keyed_on_the_resolved_id(wired):
    db = _Session(
        project=({"pass_rate_bands": BANDS}, PROJECT_POLICY_ID, 4),
        names={str(PROJECT_POLICY_ID): "Payments gate"},
    )

    await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    names = db.name_statements()
    assert len(names) == 1
    assert names[0] == (
        "SELECT release_gate_policies.name FROM release_gate_policies "
        "WHERE release_gate_policies.id = :id_1"
    )
    index = db.statements.index(names[0])
    assert db.params[index] == {"id_1": PROJECT_POLICY_ID}
    # Resolved at the project level: one precedence read, then the name.
    assert len(db.policy_statements()) == 2


async def test_a_name_that_is_not_text_names_the_version(wired):
    """A test double (or a row gone between the reads) never yields an empty label."""
    db = _Session(project=({"pass_rate_bands": BANDS}, PROJECT_POLICY_ID, 7), names={})

    out = await metrics_service.get_dashboard_summary(db, PROJECT_ID, days=7)

    assert out["readiness_policy"]["name"] == "Policy v7"


async def test_the_document_resolver_is_unchanged():
    """The council and the provenance pin keep the three-column resolver."""
    import inspect

    src = inspect.getsource(metrics_service._resolve_active_policy_for_project)
    assert "ReleaseGatePolicy.name" not in src
    assert "resolve_readiness_policy" in inspect.getsource(metrics_service.get_dashboard_summary)
    assert metrics_service.READINESS_POLICY_LEVELS == ("project", "system")
