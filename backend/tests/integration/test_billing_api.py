"""
API integration tests for /api/v1/billing and /api/v1/projects/{id}/llm-*
— Tier 1 item 2.

Covers role gating (ADMIN required to write quota), tenant isolation via
resolve_project_scope, the overview response shape, and the 404 path when
a project has no quota configured.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


def _fake_quota(project_id: uuid.UUID) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        enabled=True,
        period_type="MONTHLY",
        included_usd=10.0,
        overage_rate_usd=1.0,
        hard_cap_usd=50.0,
        soft_warn_threshold_pct=80,
        at_cap_action="AUTO_DOWNGRADE_TO_ML",
        created_at=now,
        updated_at=now,
        updated_by_user_id=None,
    )


# ── GET /projects/{id}/llm-quota ────────────────────────────────────────────


async def test_get_quota_requires_project_access(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(accessible_projects={uuid.uuid4()})  # NOT the target
    resp = await client.get(f"/api/v1/projects/{target}/llm-quota")
    assert resp.status_code == 403


async def test_get_quota_returns_404_when_no_row_configured(
    client, auth_as, override_db, fake_db,
):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.llm_cost_budget.get_quota",
        AsyncMock(return_value=None),
    ):
        resp = await client.get(f"/api/v1/projects/{target}/llm-quota")
    assert resp.status_code == 404


async def test_get_quota_happy_path(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.llm_cost_budget.get_quota",
        AsyncMock(return_value=_fake_quota(target)),
    ):
        resp = await client.get(f"/api/v1/projects/{target}/llm-quota")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["hard_cap_usd"] == 50.0
    assert body["at_cap_action"] == "AUTO_DOWNGRADE_TO_ML"


# ── PUT /projects/{id}/llm-quota ────────────────────────────────────────────


async def test_put_quota_requires_admin(client, auth_as):
    target = uuid.uuid4()
    auth_as(role=UserRole.QA_LEAD)  # not ADMIN
    resp = await client.put(
        f"/api/v1/projects/{target}/llm-quota",
        json={
            "enabled": True,
            "included_usd": 10,
            "overage_rate_usd": 1,
            "hard_cap_usd": 100,
            "soft_warn_threshold_pct": 80,
            "at_cap_action": "AUTO_DOWNGRADE_TO_ML",
        },
    )
    assert resp.status_code == 403


async def test_put_quota_rejects_invalid_at_cap_action(client, auth_as):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    resp = await client.put(
        f"/api/v1/projects/{target}/llm-quota",
        json={
            "enabled": True,
            "included_usd": 10,
            "overage_rate_usd": 1,
            "hard_cap_usd": 100,
            "soft_warn_threshold_pct": 80,
            "at_cap_action": "NUKE_PRODUCTION",  # invalid
        },
    )
    assert resp.status_code == 422


async def test_put_quota_happy_path(client, auth_as, override_db, fake_db):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.llm_cost_budget.upsert_quota",
        AsyncMock(return_value=_fake_quota(target)),
    ) as mock_upsert:
        resp = await client.put(
            f"/api/v1/projects/{target}/llm-quota",
            json={
                "enabled": True,
                "included_usd": 10,
                "overage_rate_usd": 1,
                "hard_cap_usd": 100,
                "soft_warn_threshold_pct": 80,
                "at_cap_action": "HARD_BLOCK",
            },
        )
    assert resp.status_code == 200, resp.text
    mock_upsert.assert_awaited_once()


# ── GET /projects/{id}/llm-usage ────────────────────────────────────────────


async def test_get_current_usage_returns_derived_fields(
    client, auth_as, override_db, fake_db,
):
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    now = datetime.now(timezone.utc)
    with patch(
        "app.services.llm_cost_budget.get_current_usage",
        AsyncMock(
            return_value={
                "project_id": target,
                "period_start": now,
                "period_end": now,
                "total_cost_usd": 40.0,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "total_llm_calls": 12,
                "cap_hits": 0,
                "included_usd": 10.0,
                "hard_cap_usd": 50.0,
                "utilization_pct": 80.0,
                "status": "SOFT_WARN",
            }
        ),
    ):
        resp = await client.get(f"/api/v1/projects/{target}/llm-usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SOFT_WARN"
    assert body["utilization_pct"] == 80.0


# ── GET /billing/overview ───────────────────────────────────────────────────


async def test_billing_overview_empty_for_non_admin_with_no_memberships(
    client, auth_as, override_db, fake_db,
):
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects=set())
    resp = await client.get("/api/v1/billing/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == []
    assert body["total_cost_usd"] == 0


# ── AIC-06: overage figures ─────────────────────────────────────────────────


class _Rows:
    """One scripted ``execute`` answer (scalar or scalars().all())."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._value))


def _usage_row(spend: float) -> SimpleNamespace:
    return SimpleNamespace(
        total_cost_usd=spend, total_input_tokens=1000, total_output_tokens=500,
        total_llm_calls=12, cap_hits=1,
    )


def _overage_quota(project_id: uuid.UUID) -> SimpleNamespace:
    quota = _fake_quota(project_id)
    quota.included_usd = 20.0
    quota.overage_rate_usd = 1.5
    quota.hard_cap_usd = 50.0
    quota.soft_warn_threshold_pct = 80
    return quota


def _recording(fake_db):
    statements: list = []
    original = fake_db.execute

    async def _execute(stmt, *args, **kwargs):
        statements.append(stmt)
        return await original(stmt, *args, **kwargs)

    fake_db.execute = _execute
    return statements


async def test_get_current_usage_carries_the_overage_figures(client, auth_as, override_db, fake_db):
    """Through the real service: the route returns covered / overage / charge,
    and utilisation and status are what they were for the same inputs."""
    target = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([_Rows(_usage_row(30.0)), _Rows(_overage_quota(target))])
    resp = await client.get(f"/api/v1/projects/{target}/llm-usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["covered_usd"], body["overage_usd"], body["overage_charge_usd"]) == (20.0, 10.0, 15.0)
    assert body["overage_rate_usd"] == 1.5
    assert body["utilization_pct"] == 60.0 and body["status"] == "OK"
    assert body["projection_basis"] in {"linear_elapsed_days", "insufficient_elapsed_time"}


async def test_billing_overview_rows_carry_the_overage_for_a_member_only(
    client, auth_as, override_db, fake_db, monkeypatch,
):
    """A member sees only their project (the SELECT is scoped to
    ``get_accessible_project_ids``) and its row carries every AIC-06 figure;
    ``BillingOverviewProject`` is hand-built, so a missed key never arrives."""
    from sqlalchemy.dialects import postgresql

    mine, other = uuid.uuid4(), uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER, accessible_projects={mine})

    async def _accessible(_db, _user):
        return {mine}

    monkeypatch.setattr("app.routers.llm_cost_budget.get_accessible_project_ids", _accessible)
    statements = _recording(fake_db)
    project = SimpleNamespace(id=mine, name="Checkout")
    fake_db.set_execute_results([
        _Rows([project]),
        _Rows(_usage_row(30.0)),
        _Rows(_overage_quota(mine)),
    ])
    resp = await client.get("/api/v1/billing/overview")
    assert resp.status_code == 200, resp.text
    [row] = resp.json()["projects"]
    assert row["project_id"] == str(mine)
    assert row["included_usd"] == 20.0
    assert (row["covered_usd"], row["overage_usd"], row["overage_charge_usd"]) == (20.0, 10.0, 15.0)
    assert row["overage_rate_usd"] == 1.5
    for key in ("projected_period_cost_usd", "projected_overage_usd", "projected_overage_charge_usd",
                "projection_basis"):
        assert key in row
    assert row["utilization_pct"] == 60.0 and row["status"] == "OK" and row["cap_hits"] == 1
    # The project query is limited to the caller's projects.
    compiled = statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "WHERE projects.id IN" in sql
    assert str(mine) in sql and str(other) not in sql


async def test_billing_overview_unlimited_row_has_null_figures(
    client, auth_as, override_db, fake_db, monkeypatch,
):
    mine = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)

    async def _everything(_db, _user):
        return None

    monkeypatch.setattr("app.routers.llm_cost_budget.get_accessible_project_ids", _everything)
    fake_db.set_execute_results([
        _Rows([SimpleNamespace(id=mine, name="No quota")]),
        _Rows(_usage_row(30.0)),
        _Rows(None),
    ])
    resp = await client.get("/api/v1/billing/overview")
    assert resp.status_code == 200, resp.text
    [row] = resp.json()["projects"]
    assert row["status"] == "UNLIMITED"
    for key in ("included_usd", "overage_rate_usd", "covered_usd", "overage_usd", "overage_charge_usd",
                "projected_period_cost_usd", "projected_overage_usd", "projected_overage_charge_usd",
                "projection_basis"):
        assert row[key] is None, key
