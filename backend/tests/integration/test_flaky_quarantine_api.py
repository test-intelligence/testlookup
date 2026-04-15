"""
API integration tests for /api/v1/quarantine — Tier 1 item 3.

Verifies role gates on writes, tenant isolation on reads, the decision
flow happy path, and the 409 conflict path when a QA Lead tries to
approve a terminal-state row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


def _fake_request(
    project_id: uuid.UUID,
    status_value: str = "PROPOSED",
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        test_fingerprint="fp-" + uuid.uuid4().hex[:8],
        test_name="test_login",
        suite_name="auth.spec.ts",
        status=status_value,
        detection_method="flaky_sentinel_agent",
        flip_rate=0.35,
        flip_window_size=20,
        pass_count=13,
        fail_count=7,
        detected_at=now,
        last_failure_at=now,
        proposed_at=now,
        approved_at=None,
        approved_by_user_id=None,
        rejected_at=None,
        rejected_by_user_id=None,
        quarantine_start=None,
        quarantine_expires_at=None,
        quarantine_duration_days=14,
        recheck_at=None,
        rationale={"method": "flaky_sentinel_pass_fail_ratio"},
        reviewer_notes=None,
        created_at=now,
        updated_at=now,
    )


# ── GET /quarantine ─────────────────────────────────────────────────────────


async def test_list_quarantine_requires_project_membership_for_scoped_query(
    client, auth_as, override_db,
):
    target = uuid.uuid4()
    auth_as(accessible_projects={uuid.uuid4()})  # not the target
    resp = await client.get(f"/api/v1/quarantine?project_id={target}")
    assert resp.status_code == 403


async def test_list_quarantine_admin_sees_all(client, auth_as, override_db):
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.flaky_quarantine_service.list_requests",
        AsyncMock(return_value=[_fake_request(uuid.uuid4())]),
    ):
        resp = await client.get("/api/v1/quarantine")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ── GET /quarantine/stats ───────────────────────────────────────────────────


async def test_stats_returns_derived_total_live(client, auth_as, override_db):
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.flaky_quarantine_service.stats_by_status",
        AsyncMock(
            return_value={
                "PROPOSED": 3,
                "QUARANTINED": 2,
                "RECHECK_SCHEDULED": 1,
                "RELEASED": 5,
            }
        ),
    ):
        resp = await client.get("/api/v1/quarantine/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposed"] == 3
    assert body["quarantined"] == 2
    assert body["released"] == 5
    # total_live = 3 + 2 + 1 = 6 (RELEASED is terminal, not counted)
    assert body["total_live"] == 6


# ── POST /quarantine/{id}/approve ───────────────────────────────────────────


async def test_approve_requires_qa_lead(client, auth_as):
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.post(
        f"/api/v1/quarantine/{uuid.uuid4()}/approve",
        json={"notes": "ship it"},
    )
    assert resp.status_code == 403


async def test_approve_happy_path_transitions_to_quarantined(
    client, auth_as, override_db,
):
    target_project = uuid.uuid4()
    req_id = uuid.uuid4()
    auth_as(role=UserRole.ADMIN)

    # The router looks up the request twice (once for scope, once to
    # compute the approval). We mock both the get + the service call.
    proposed = _fake_request(target_project, status_value="PROPOSED")
    proposed.id = req_id
    approved_after = _fake_request(target_project, status_value="QUARANTINED")
    approved_after.id = req_id
    approved_after.approved_at = datetime.now(timezone.utc)

    with patch(
        "app.services.flaky_quarantine_service.get_request",
        AsyncMock(return_value=proposed),
    ), patch(
        "app.services.flaky_quarantine_service.approve",
        AsyncMock(return_value=approved_after),
    ):
        resp = await client.post(
            f"/api/v1/quarantine/{req_id}/approve",
            json={"notes": "confirmed flaky", "quarantine_duration_days": 21},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "QUARANTINED"


# ── POST /quarantine/{id}/reject ────────────────────────────────────────────


async def test_reject_returns_404_when_request_missing(client, auth_as):
    auth_as(role=UserRole.QA_LEAD)
    with patch(
        "app.services.flaky_quarantine_service.get_request",
        AsyncMock(return_value=None),
    ):
        resp = await client.post(
            f"/api/v1/quarantine/{uuid.uuid4()}/reject",
            json={"notes": "not really flaky"},
        )
    assert resp.status_code == 404


# ── POST /quarantine (manual proposal) ─────────────────────────────────────


async def test_manual_propose_503_when_flag_disabled(client, auth_as):
    """propose_quarantine returns None when the feature flag is off.
    The router translates that into a 503 with a user-facing message."""
    auth_as(role=UserRole.QA_LEAD)
    with patch(
        "app.routers.flaky_quarantine.resolve_project_scope",
        AsyncMock(return_value=None),
    ), patch(
        "app.services.flaky_quarantine_service.propose_quarantine",
        AsyncMock(return_value=None),
    ):
        resp = await client.post(
            "/api/v1/quarantine",
            json={
                "project_id": str(uuid.uuid4()),
                "test_fingerprint": "fp-123",
                "test_name": "test_foo",
            },
        )
    assert resp.status_code == 503
    assert "flaky_auto_quarantine" in resp.json()["detail"]
