"""Regression (W10 INT-04): the Integration Health routes answer per scope and check every QUERY id first.

The handlers are called directly (as the pinned on-demand tests do) with a
session double that keeps a small world -- health rows, GitHub / GitLab
integrations, paused webhooks -- and answers each statement from it, honouring
the statement's own ``scope_key`` / ``IN`` filters. It records every statement,
so "no read happened" is a fact the test checks, not an assumption.

What these pin:

* ``GET /status`` (default scope) answers the deployment-wide rows only;
* ``scope=all`` for a QA lead lists THEIR projects' integrations only; an
  integration never probed reads ``probed: false`` / ``unknown`` / the "Not
  probed yet" sentence -- never healthy; a paused webhook reads ``kind:
  "webhook"`` / ``paused``; an admin's read is five statements, none per row;
* ``POST /probe?provider=github&project_id=<another tenant's project>`` is a
  403 before ANY statement and no probe runs (``resolve_project_scope`` on the
  QUERY id first: a guard blind to query params once let nine IDORs through);
  the same for ``GET /history/github?project_id=<other>``;
* the 422s: ``provider=gitlab`` without a project, a per-project probe of a
  provider that is not per project, ``scope=bogus``; the 404 for a project with
  no enabled integration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.core import deps
from app.models.postgres import UserRole
from app.models.schemas import IntegrationHealthRow
from app.routers import integration_health as router
from app.services import integration_probe_service as probes
from app.services import webhook_breaker

pytestmark = pytest.mark.regression

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


def _in_values(params: dict, prefix: str):
    for key, value in params.items():
        if key.startswith(prefix) and isinstance(value, (list, tuple, set)):
            return {str(v) for v in value}
    return None


class _World:
    """The rows behind the routes, answered statement by statement."""

    def __init__(self):
        self.statements: list[str] = []
        self.health: list[SimpleNamespace] = []
        self.github: list[SimpleNamespace] = []
        self.gitlab: list[SimpleNamespace] = []
        self.webhooks: list[SimpleNamespace] = []
        self.names: dict[uuid.UUID, str] = {}
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        params = stmt.compile(dialect=postgresql.dialect()).params
        self.statements.append(sql)
        projects = _in_values(params, "project_id")
        if "FROM github_integrations" in sql or "FROM gitlab_integrations" in sql:
            rows = self.github if "FROM github_integrations" in sql else self.gitlab
            base = "api_base_url" if rows is self.github else "base_url"
            return _Result([
                (r.project_id, self.names[r.project_id], getattr(r, base))
                for r in rows
                if r.enabled and (projects is None or str(r.project_id) in projects)
            ])
        if "FROM webhook_subscriptions" in sql:
            return _Result([
                (w, self.names[w.project_id])
                for w in self.webhooks
                if w.disabled_reason == "circuit_open" and not w.enabled
                and (projects is None or str(w.project_id) in projects)
            ])
        if "FROM integration_probe_results" in sql:
            return _Result([])
        assert "FROM integration_health_checks" in sql, sql
        scopes = _in_values(params, "scope_key")
        if scopes is not None:
            return _Result([h for h in self.health if h.scope_key in scopes])
        wanted = params["scope_key_1"]
        return _Result([h for h in self.health if h.scope_key == wanted])

    async def close(self):
        return None


def _hc(provider, scope_key="", status="healthy", **kw):
    base = dict(provider=provider, scope_key=scope_key, status=status, last_checked_at=NOW,
                message=f"{provider} {status}", response_ms=40, consecutive_failures=0, last_success_at=NOW)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def world(monkeypatch):
    world = _World()
    world.mine, world.other = uuid.uuid4(), uuid.uuid4()
    world.names = {world.mine: "Payment Service", world.other: "Checkout"}
    world.health = [_hc("jira"), _hc("github", status="skipped"),
                    _hc("github", str(world.mine), "healthy"),
                    _hc("github", str(world.other), "auth_error")]
    world.github = [
        SimpleNamespace(project_id=world.mine, enabled=True, api_base_url="https://ghe.fake.test/api/v3"),
        SimpleNamespace(project_id=world.other, enabled=True, api_base_url="https://api.github.com"),
    ]
    world.gitlab = [SimpleNamespace(project_id=world.mine, enabled=True, base_url="https://gitlab.fake.test")]
    world.webhooks = [
        SimpleNamespace(id=uuid.uuid4(), project_id=world.mine, name="release hook", enabled=False,
                        target_url="https://user:pw@hooks.fake.test:8443/hooks/fail?x=1",
                        disabled_reason="circuit_open", disabled_at=NOW, last_error="HTTP 500: receiver error",
                        consecutive_failures=3, breaker_threshold=3),
        SimpleNamespace(id=uuid.uuid4(), project_id=world.other, name="theirs", enabled=False,
                        target_url="https://hooks.fake.test/hooks/fail", disabled_reason="circuit_open",
                        disabled_at=NOW, last_error="x", consecutive_failures=10, breaker_threshold=10),
    ]

    def accessible(ids):
        async def _accessible(_db, _user):
            return ids
        monkeypatch.setattr(deps, "get_accessible_project_ids", _accessible)
        monkeypatch.setattr(router, "get_accessible_project_ids", _accessible)

    world.as_lead = lambda: accessible({world.mine})
    world.as_admin = lambda: accessible(None)
    world.user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD)
    return world


@pytest.mark.asyncio
async def test_the_default_status_answers_the_deployment_wide_rows_only(world):
    world.as_lead()
    rows = await router.get_all_status(current_user=world.user, db=world)

    assert [(r["provider"], r["status"]) for r in rows] == [("jira", "healthy"), ("github", "skipped")]
    assert len(world.statements) == 1
    assert all(IntegrationHealthRow.model_validate(r).kind == "global" for r in rows)


@pytest.mark.asyncio
async def test_scope_all_lists_the_leads_projects_only_and_never_reads_unprobed_as_healthy(world):
    world.as_lead()
    rows = [IntegrationHealthRow.model_validate(r) for r in
            await router.get_all_status(scope="all", current_user=world.user, db=world)]

    assert [(r.kind, r.provider, r.label) for r in rows] == [
        ("global", "jira", None),
        ("global", "github", None),
        ("project", "github", "Payment Service"),
        ("project", "gitlab", "Payment Service"),
        ("webhook", "webhook", "release hook"),
    ]
    github, gitlab, hook = rows[2], rows[3], rows[4]
    assert (github.status, github.probed, github.target) == ("healthy", True, "ghe.fake.test/api/v3")
    assert github.project_id == world.mine and github.scope_key == str(world.mine)
    # Never probed: unknown, not healthy.
    assert (gitlab.status, gitlab.probed, gitlab.message) == ("unknown", False, probes.NOT_PROBED_MESSAGE)
    assert gitlab.last_checked_at is None and gitlab.target == "gitlab.fake.test"
    assert (hook.status, hook.breaker_threshold, hook.consecutive_failures) == ("paused", 3, 3)
    assert hook.target == "hooks.fake.test:8443", "the target shows a host, never a user name or a path"
    assert hook.message == "HTTP 500: receiver error"
    assert str(world.other) not in {r.scope_key for r in rows}


@pytest.mark.asyncio
async def test_scope_all_for_an_admin_is_five_reads_whatever_the_row_count(world):
    world.as_admin()
    for _ in range(20):
        pid = uuid.uuid4()
        world.names[pid] = f"Project {pid.hex[:6]}"
        world.github.append(SimpleNamespace(project_id=pid, enabled=True, api_base_url="https://api.github.com"))

    rows = await router.get_all_status(scope="all", current_user=world.user, db=world)

    assert len([r for r in rows if r["kind"] == "project"]) == 23
    assert len([r for r in rows if r["kind"] == "webhook"]) == 2
    assert len(world.statements) == 5, world.statements


@pytest.mark.asyncio
async def test_a_bogus_scope_is_a_422(world):
    with pytest.raises(HTTPException) as exc:
        await router.get_all_status(scope="bogus", current_user=world.user, db=world)
    assert (exc.value.status_code, exc.value.detail) == (422, "scope must be global or all")
    assert world.statements == []


@pytest.mark.asyncio
async def test_probing_another_tenants_project_is_a_403_before_any_read(world, monkeypatch):
    world.as_lead()
    run_project = AsyncMock(return_value=[])
    persist = AsyncMock()
    monkeypatch.setattr(probes, "run_project_probes", run_project)
    monkeypatch.setattr(probes, "persist_probe_results", persist)

    with pytest.raises(HTTPException) as exc:
        await router.trigger_probe(provider="github", project_id=world.other, current_user=world.user, db=world)

    assert exc.value.status_code == 403
    assert world.statements == [], "the other project's integration was read before the scope check"
    run_project.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_probing_my_project_runs_only_that_project_and_provider(world, monkeypatch):
    world.as_lead()
    result = probes.ProbeResult("github", "healthy", 12, "Repository acme/payments reachable",
                                scope_key=str(world.mine), project_id=str(world.mine))
    run_project = AsyncMock(return_value=[result])
    persist = AsyncMock()
    monkeypatch.setattr(probes, "run_project_probes", run_project)
    monkeypatch.setattr(probes, "persist_probe_results", persist)

    answer = await router.trigger_probe(provider="github", project_id=world.mine, current_user=world.user, db=world)

    run_project.assert_awaited_once_with(project_ids={world.mine}, providers={"github"}, db=world)
    persist.assert_awaited_once_with([result])
    assert answer[0]["scope_key"] == str(world.mine) and answer[0]["project_id"] == str(world.mine)


@pytest.mark.asyncio
async def test_a_project_without_an_enabled_integration_is_a_404(world, monkeypatch):
    world.as_lead()
    monkeypatch.setattr(probes, "run_project_probes", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as exc:
        await router.trigger_probe(provider="gitlab", project_id=world.mine, current_user=world.user, db=world)
    assert (exc.value.status_code, exc.value.detail) == (404, "This project has no enabled GitLab integration.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "project", "detail"),
    [
        ("gitlab", False, "GitLab is probed per project: pass project_id."),
        ("jira", True, "A per-project probe needs provider=github or provider=gitlab."),
        (None, True, "A per-project probe needs provider=github or provider=gitlab."),
    ],
)
async def test_the_per_project_422s(world, monkeypatch, provider, project, detail):
    world.as_lead()
    run_all = AsyncMock(return_value=[])
    monkeypatch.setattr(probes, "run_all_probes", run_all)
    with pytest.raises(HTTPException) as exc:
        await router.trigger_probe(
            provider=provider, project_id=world.mine if project else None, current_user=world.user, db=world,
        )
    assert (exc.value.status_code, exc.value.detail) == (422, detail)
    run_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_provider_names_gitlab_among_the_supported(world):
    with pytest.raises(HTTPException) as exc:
        await router.trigger_probe(provider="slcak", current_user=world.user, db=world)
    assert exc.value.status_code == 422
    assert "gitlab" in exc.value.detail.split("Supported: ")[1].split(", ")


@pytest.mark.asyncio
async def test_probe_all_adds_the_readers_projects(world, monkeypatch):
    world.as_lead()
    run_all = AsyncMock(return_value=[probes.ProbeResult("jira", "healthy")])
    run_project = AsyncMock(return_value=[])
    monkeypatch.setattr(probes, "run_all_probes", run_all)
    monkeypatch.setattr(probes, "run_project_probes", run_project)
    monkeypatch.setattr(probes, "persist_probe_results", AsyncMock())

    await router.trigger_probe(current_user=world.user, db=world)

    run_project.assert_awaited_once_with(project_ids={world.mine}, db=world)


@pytest.mark.asyncio
async def test_another_tenants_history_is_a_403_before_any_read(world):
    world.as_lead()
    with pytest.raises(HTTPException) as exc:
        await router.get_provider_history(
            provider="github", days=7, project_id=world.other, current_user=world.user, db=world,
        )
    assert exc.value.status_code == 403
    assert world.statements == []


@pytest.mark.asyncio
async def test_history_reads_one_scope(world):
    world.as_lead()
    await router.get_provider_history(provider="github", days=7, current_user=world.user, db=world)
    await router.get_provider_history(
        provider="github", days=7, project_id=world.mine, current_user=world.user, db=world,
    )
    first, second = world.statements
    assert "integration_probe_results.scope_key = " in first and "integration_probe_results.scope_key = " in second


def test_the_routers_paused_reason_is_the_breakers():
    """The router writes the reason out (importing the recording breaker would
    mark it as recording activity); this keeps the two in step."""
    assert router._CIRCUIT_OPEN == webhook_breaker.WEBHOOK_DISABLED_REASONS[0]
