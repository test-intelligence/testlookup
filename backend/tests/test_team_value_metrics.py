"""W08 TRND-08 -- ``GET /api/v1/value-metrics/by-team``, everything a database
cannot answer: the scope policy, the exact body, the declared filters, and the
service's assembly over canned reads (the sum check among them). Postgres:
``tests/integration/test_team_value_metrics_postgres.py``.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.routers import value_metrics as router
from app.services import team_value_metrics_service as svc
from app.services import value_metrics_service as vms
from app.services.analytics_scope import AnalyticsScope

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
BODY_KEYS = {
    "window_days", "measured", "reason", "rules_configured", "teams",
    "not_attributable_hours", "headline_hours",
}


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _rule(pattern: str, team: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), match_type="suite_name", match_pattern=pattern, service_name=team,
        team_name=team, team_contact=None, priority=0, is_active=True,
    )


@pytest.fixture
def reads(monkeypatch):
    """Every read the service makes, canned; ``state`` holds what they answer."""
    state: dict = {
        "rules": [_rule("pay*", "Payments"), _rule("search*", "Search")],
        "component_map": None,
        "legs": {"clustered_failures": 10, "duplicates_absorbed": 7, "runs_unblocked_proxy": 2},
        "tests": [], "defects": [], "members": [], "since": [],
    }

    async def assumptions(db, project_id):
        return vms.EffectiveAssumptions(
            triage_minutes_per_failure=15, blocked_run_wait_minutes=60, defect_filing_minutes=30,
        )

    async def legs(db, project_id, since):
        state["since"].append(since)
        return state["legs"]

    async def ownership(db, project_id):
        return state["rules"], state["component_map"]

    def canned(name):
        async def read(db, project_id, since):
            state["since"].append(since)
            return state[name]
        return read

    monkeypatch.setattr(vms, "get_effective_assumptions", assumptions)
    monkeypatch.setattr(svc, "_headline_legs", legs)
    monkeypatch.setattr(svc, "_ownership_inputs", ownership)
    monkeypatch.setattr(svc, "_test_rows", canned("tests"))
    monkeypatch.setattr(svc, "_defect_rows", canned("defects"))
    monkeypatch.setattr(svc, "_cluster_member_rows", canned("members"))
    return state


def _test_row(suite, fp, executions=4, passed=3, evaluated=4, flaky=False):
    return {"suite_name": suite, "test_fingerprint": fp, "class_name": None, "package_name": None,
            "full_name": None, "own_label": None, "own_owner": None, "executions": executions,
            "passed": passed, "evaluated": evaluated, "flaky": flaky}


def _member(cluster, size, fp=None, suite=None):
    return {"cluster_id": cluster, "size": size, "test_fingerprint": fp, "suite_name": suite,
            "class_name": None, "package_name": None, "full_name": None, "own_label": None,
            "own_owner": None}


# ── the route ──────────────────────────────────────────────────────────────


def test_the_route_requires_one_project_over_up_to_a_year():
    assert router._TEAM_SCOPE.project_required is True
    assert (router._TEAM_SCOPE.default_days, router._TEAM_SCOPE.max_days) == (30, 365)
    assert router._TEAM_SCOPE.on_denied == "raise"  # a foreign project is a 403
    # Only the scope (project, window, release, suite -- every id checked by
    # ``analytics_scope``) and the session: no raw ``project_id`` of its own.
    params = inspect.signature(router.get_metrics_by_team).parameters
    assert set(params) == {"scope", "db"}


@pytest.mark.asyncio
async def test_the_body_is_exactly_the_contract_s_and_release_and_suite_are_declared(reads, monkeypatch):
    seen: dict = {}

    async def fake_meta(db, scope, **kwargs):
        seen.update(kwargs)
        return {"measured": kwargs["measured"], "reason": kwargs["reason"]}

    monkeypatch.setattr(router, "build_meta", fake_meta)
    scope = _scope(release_ids=(str(uuid.uuid4()),), suite_names=("checkout",))
    body = await router.get_metrics_by_team(scope=scope, db=object())
    assert set(body) == BODY_KEYS | {"meta"}
    assert [item["dimension"] for item in seen["ignored_filters"]] == ["release", "suite"]
    assert seen["measured"] is True and seen["reason"] is None
    assert set(body["meta"]["definitions"]) >= {"team", "hours", "headline_hours"}


@pytest.mark.asyncio
async def test_nothing_is_declared_ignored_when_nothing_was_sent(reads, monkeypatch):
    seen: dict = {}

    async def fake_meta(db, scope, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(router, "build_meta", fake_meta)
    await router.get_metrics_by_team(scope=_scope(), db=object())
    assert seen["ignored_filters"] == []


# ── the service ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_rules_and_no_map_is_not_measured_with_the_headline_still_computed(reads):
    reads["rules"], reads["component_map"] = [], {}
    body = await svc.get_team_value_metrics(object(), _scope())
    assert set(body) == BODY_KEYS
    assert body["measured"] is False and body["rules_configured"] is False
    assert body["reason"] == "No ownership rules yet." and body["teams"] == []
    # 10 x 15 + 7 x 30 + 2 x 60 minutes = 8 h; the quarantine leg is 2 h.
    assert body["headline_hours"] == 8.0 and body["not_attributable_hours"] == 2.0


@pytest.mark.asyncio
async def test_a_component_map_alone_is_configured(reads):
    reads["rules"], reads["component_map"] = [], {"default": {"team": "Platform"}}
    reads["tests"] = [_test_row("anything", "fp1")]
    body = await svc.get_team_value_metrics(object(), _scope())
    assert body["rules_configured"] is True and body["measured"] is True
    assert [t["team"] for t in body["teams"]] == ["Platform"]


@pytest.mark.asyncio
async def test_teams_hours_plus_the_unattributable_leg_are_the_headline(reads):
    """The sum check over canned reads: clusters of 3 (2 Payments + 1 Search),
    4 (no member found) and 3 (all Search) are the legs' 10 / 7."""
    reads["tests"] = [
        _test_row("payments", "p1"), _test_row("payments", "p2"),
        _test_row("search", "s1", passed=0, evaluated=0), _test_row("misc", "m1"),
    ]
    reads["members"] = [
        _member("c1", 3, "p1", "payments"), _member("c1", 3, "p2", "payments"),
        _member("c1", 3, "s1", "search"),
        _member("c2", 4),
        _member("c3", 3, "s1", "search"),
    ]
    reads["defects"] = [
        {"test_fingerprint": "p1", "suite_name": "payments", "class_name": None, "package_name": None,
         "full_name": None, "own_label": None, "own_owner": None,
         "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
         "resolved_at": datetime(2026, 9, 1, 6, tzinfo=timezone.utc)},
    ]
    body = await svc.get_team_value_metrics(object(), _scope())
    by_team = {t["team"]: t for t in body["teams"]}
    assert [t["team"] for t in body["teams"]][-1] == svc.UNASSIGNED
    total = sum(t["hours_saved"] for t in body["teams"]) + body["not_attributable_hours"]
    assert total == pytest.approx(body["headline_hours"], abs=0.01)
    assert by_team["Payments"]["defects"] == 1 and by_team["Payments"]["mttr_hours"] == 6.0
    assert by_team["Search"]["pass_rate"] is None
    assert by_team[svc.UNASSIGNED]["tests"] == 1  # m1 matched no rule
    assert by_team["Payments"]["hours"]["triage"] == pytest.approx(2 * 15 / 60, abs=0.005)


@pytest.mark.asyncio
async def test_every_read_shares_one_since_the_headline_s_rule(reads):
    before = datetime.now(timezone.utc)
    await svc.get_team_value_metrics(object(), _scope(days=14))
    after = datetime.now(timezone.utc)
    assert len(set(reads["since"])) == 1
    (since,) = set(reads["since"])
    assert before - timedelta(days=14) <= since <= after - timedelta(days=14)


@pytest.mark.asyncio
async def test_a_defect_on_a_test_outside_the_window_resolves_from_its_own_row(reads):
    reads["defects"] = [
        {"test_fingerprint": "old", "suite_name": "payments-legacy", "class_name": None,
         "package_name": None, "full_name": None, "own_label": None, "own_owner": None,
         "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "resolved_at": None},
    ]
    body = await svc.get_team_value_metrics(object(), _scope())
    (row,) = body["teams"]
    assert (row["team"], row["defects"], row["tests"], row["mttr_hours"]) == ("Payments", 1, 0, None)
