"""Open findings of the API/MCP launch QA sweep (O-01, O-18 to O-27): inputs that were accepted
without a word and answered an empty or wrong result are refused or normalised."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.postgres import TestRun as RunRecord, UserRole
from app.models.schemas import DefectPromotionRequest, NotificationPreferenceCreate
from app.routers import agents, feedback, releases
from app.services import flaky_quarantine_service, runs_service, workflow_run_state
from tests.integration.conftest import fake_execute_result

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")


# ── O-18: pipeline status filter ─────────────────────────────────────────────


@pytest.mark.parametrize("status", ["success", "done", "queued", "FAILD"])
def test_an_unknown_pipeline_status_is_refused_not_read_as_failed(status):
    with pytest.raises(HTTPException) as refused:
        agents._status_filter_values(status)

    assert refused.value.status_code == 422 and "status must be one of" in refused.value.detail


def test_known_pipeline_statuses_still_filter():
    public = sorted(set(workflow_run_state.PUBLIC_STATUS.values()))

    for name in public:
        assert agents._status_filter_values(name.upper())
    for internal in workflow_run_state.PipelineRunStatus:
        assert agents._status_filter_values(internal.value) == [internal.value] or internal.value in public
    assert agents._status_filter_values("partial")  # a legacy word a stale client may still send


# ── O-19: promotion severity ─────────────────────────────────────────────────


@pytest.mark.parametrize(("sent", "stored"), [("CRITICAL", "CRITICAL"), ("critical", "CRITICAL"), (" High ", "HIGH")])
def test_promotion_severity_is_stored_upper_case(sent, stored):
    assert DefectPromotionRequest(title="Checkout fails", severity=sent).severity == stored


@pytest.mark.parametrize("severity", ["P0", "Blocker", "", 3])
def test_promotion_refuses_a_severity_no_reader_counts(severity):
    with pytest.raises(ValidationError) as refused:
        DefectPromotionRequest(title="Checkout fails", severity=severity)

    assert refused.value.errors()[0]["loc"] == ("severity",)


def test_promotion_severity_defaults_to_high():
    assert DefectPromotionRequest(title="Checkout fails").severity == "HIGH"


# ── O-20 / O-21 / O-22: filters that returned an empty page ──────────────────


async def test_defect_list_refuses_a_status_that_does_not_exist(client, auth_as):
    auth_as(role=UserRole.ADMIN)

    response = await client.get("/api/v1/analytics/defects", params={"resolution_status": "WONTFIX"})

    assert response.status_code == 422, response.text
    assert "resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID, ACTIVE, CLOSED" in response.text


async def test_quarantine_list_refuses_an_unknown_status_and_matches_any_case(client, auth_as, monkeypatch):
    from app.routers import flaky_quarantine

    auth_as(role=UserRole.ADMIN)
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(flaky_quarantine.svc, "list_requests", listing)

    refused = await client.get("/api/v1/quarantine", params={"status_filter": "active"})
    accepted = await client.get("/api/v1/quarantine", params={"status_filter": "quarantined"})

    assert refused.status_code == 422 and "status_filter must be one of" in refused.text
    assert accepted.status_code == 200, accepted.text
    assert listing.await_count == 1 and listing.await_args.kwargs["status_filter"] == "QUARANTINED"


@pytest.mark.parametrize("status", ["error", "FAILED&status=nope"])
async def test_run_tests_route_refuses_a_status_no_test_can_have(client, auth_as, fake_db, monkeypatch, status):
    from app.routers import runs

    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=RunRecord(id=RUN_ID, project_id=PROJECT_ID))])
    listing = AsyncMock(return_value=([], 0, 0))
    monkeypatch.setattr(runs, "list_run_test_cases", listing)

    response = await client.get(f"/api/v1/runs/{RUN_ID}/tests?status={status}")

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "status must be one of PASSED, FAILED, SKIPPED, BROKEN, UNKNOWN."
    listing.assert_not_awaited()


@pytest.mark.parametrize("period", ["monthly", "WEEKLY", ""])
async def test_digest_preview_refuses_a_period_it_does_not_have(client, auth_as, period):
    auth_as(role=UserRole.ADMIN)

    response = await client.get("/api/v1/digests/preview", params={"period": period})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "period must be one of daily, weekly."


# ── O-23: model promotion track ──────────────────────────────────────────────


def test_model_promotion_refuses_a_track_nothing_reads():
    assert feedback.PromoteModelRequest(track="classifier", model_name="m").track == "classifier"
    with pytest.raises(ValidationError):
        feedback.PromoteModelRequest(track="classifer", model_name="m")


# ── O-24: notification events ────────────────────────────────────────────────


def test_a_preference_refuses_an_event_that_can_never_fire():
    with pytest.raises(ValidationError) as refused:
        NotificationPreferenceCreate(channel="email", events=["run.failed", "run_failed"])

    assert "unknown notification event(s): run.failed" in str(refused.value)


def test_a_preference_keeps_real_events_and_the_legacy_alias():
    preference = NotificationPreferenceCreate(channel="email", events=["flaky_test_detected", "run_failed"])

    assert preference.events == ["test.newly_flaky", "run_failed"]
    assert NotificationPreferenceCreate(channel="email").events  # the defaults are all real events


# ── O-25: release and phase statuses ─────────────────────────────────────────


def test_release_and_phase_statuses_are_stored_lower_case():
    assert releases.ReleaseUpdate(status=" Released ").status == "released"
    assert releases.PhaseUpdate(status="COMPLETED", phase_type="QA_Testing").model_dump(exclude_unset=True) == {
        "status": "completed", "phase_type": "qa_testing",
    }
    assert releases.PhaseIn(name="QA").status == "pending"
    assert releases.ReleaseUpdate().status is None


@pytest.mark.parametrize("status", ["", "   ", "x" * 31])
def test_a_release_status_must_fit_its_column(status):
    with pytest.raises(ValidationError):
        releases.ReleaseUpdate(status=status)


# ── O-27: a buffered live run filters a suite the way stored rows do ─────────


async def test_buffered_live_tests_match_a_suite_by_contains(monkeypatch):
    from app.db import redis_client

    events = [
        {"event_type": "test_result", "test_name": name, "class_name": "C", "status": "FAILED", "suite_name": suite}
        for name, suite in [("test_pay", "API Regression"), ("test_login", "UI Smoke")]
    ]
    redis = SimpleNamespace(
        xrange=AsyncMock(return_value=[]),
        lrange=AsyncMock(return_value=[json.dumps(event) for event in events]),
    )
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)

    rows, total, _pages = await runs_service._live_buffer_test_cases(RUN_ID, 1, 50, None, "regression")

    assert [row["test_name"] for row in rows] == ["test_pay"] and total == 1


# ── O-01: a manual re-proposal keeps the detector's evidence ─────────────────


@pytest.fixture
def live_request(monkeypatch):
    """An existing QUARANTINED request with measured flip evidence, behind a scripted session."""
    existing = SimpleNamespace(
        id=UUID("77777777-7777-4777-8777-777777777777"), status="QUARANTINED", flip_rate=0.4, flip_window_size=20,
        pass_count=12, fail_count=8, test_name="test_pay", suite_name="checkout", last_failure_at=None,
        rationale={"source": "flip_detector", "flips": 8}, proposed_at=None, updated_at=None,
    )
    db = MagicMock()
    db.commit, db.refresh = AsyncMock(), AsyncMock()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(flaky_quarantine_service, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(flaky_quarantine_service, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(flaky_quarantine_service, "_find_live", AsyncMock(return_value=existing))
    monkeypatch.setattr(flaky_quarantine_service, "_snapshot", lambda row: {})
    monkeypatch.setattr(flaky_quarantine_service, "_audit", AsyncMock())
    from app.services import cache_service

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", AsyncMock())
    return existing


async def _propose(**overrides):
    arguments = dict(
        project_id=PROJECT_ID, test_fingerprint="b94c0127f321daea", test_name=None, suite_name=None,
        detection_method="manual", flip_rate=None, flip_window_size=None,
        rationale={"reason": "flips in CI", "source": "mcp"},
    )
    return await flaky_quarantine_service.propose_quarantine(**{**arguments, **overrides})


async def test_a_manual_proposal_keeps_the_measured_flip_evidence(live_request):
    row = await _propose()

    assert (row.flip_rate, row.flip_window_size, row.status) == (0.4, 20, "QUARANTINED")
    assert row.rationale == {
        "source": "flip_detector", "flips": 8, "manual_proposal": {"reason": "flips in CI", "source": "mcp"},
    }


async def test_a_detector_signal_still_replaces_the_evidence(live_request):
    row = await _propose(
        detection_method="flip_rate", flip_rate=0.6, flip_window_size=30, rationale={"source": "flip_detector", "flips": 18},
    )

    assert (row.flip_rate, row.flip_window_size) == (0.6, 30)
    assert row.rationale == {"source": "flip_detector", "flips": 18}
