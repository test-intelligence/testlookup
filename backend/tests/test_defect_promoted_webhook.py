"""DEF-07: ``defect.promoted`` — the payload, the gates, the producers, the catalogue.

* ``build_payload`` carries exactly the §1.6 keys: identity and a link, no
  description, evidence, owner or reviewer.
* ``emit_defect_promoted`` touches nothing when outbound webhooks are off
  (offline: not even a session), emits only for an EFFECTIVE cluster
  promotion, with ``delivery_scope`` making it one delivery per defect.
* ``promote_cluster`` stages the ``defect.promoted`` ledger row only for a
  promotion that is effective at once; the promote route emits only after
  its commit.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.postgres import ProjectActivityEvent, UserRole
from app.services import defect_promoted_webhook as hook
from app.services import webhook_service
from tests.fakes.fake_defect_world import MemDB, defect, make_app, membership, user

PID = uuid.uuid4()
RUN = uuid.uuid4()


def _promoted(**fields):
    base = dict(cluster_id="cl_7", approval_status="approved", severity="CRITICAL", title="Checkout 500",
                description="secret internal details", owner_team="Payments",
                evidence_bundle={"stack_traces": ["at Checkout"]},
                policy_evaluation={"source_run_id": str(RUN), "jira_project_key": "PLAT"},
                created_at=datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc))
    base.update(fields)
    return defect(PID, **base)


# ── 1. the payload ─────────────────────────────────────────────────────────


def test_the_payload_is_identity_and_a_link_only():
    row = _promoted(approved_at=datetime(2026, 10, 15, 11, 30, tzinfo=timezone(timedelta(hours=2))),
                    jira_ticket_id="PLAT-7", jira_ticket_url="https://acme.atlassian.net/browse/PLAT-7",
                    approval_status="executed")

    payload = hook.build_payload(row, run_id=str(RUN), base_url="https://testlookup.example.test/")

    assert tuple(payload) == hook.PAYLOAD_KEYS
    assert set(payload) == {"defect_id", "project_id", "cluster_id", "run_id", "title", "severity", "jira_key",
                            "jira_url", "approval_status", "deep_link", "promoted_at"}
    assert payload["deep_link"] == f"https://testlookup.example.test/defects?defect={row.id}"
    assert payload["promoted_at"] == "2026-10-15T09:30:00+00:00"
    assert payload["approval_status"] == "executed"
    assert (payload["jira_key"], payload["run_id"]) == ("PLAT-7", str(RUN))
    for leaked in ("secret internal details", "Payments", "at Checkout"):
        assert leaked not in str(payload)


def test_a_payload_without_a_ticket_or_approval_time_says_null_and_created_at():
    row = _promoted()

    payload = hook.build_payload(row, run_id=None, base_url="http://localhost:3000")

    assert payload["jira_key"] is None and payload["jira_url"] is None and payload["run_id"] is None
    assert payload["promoted_at"] == "2026-10-15T09:00:00+00:00"


def test_the_delivery_scope_is_one_per_defect():
    did = uuid.uuid4()
    assert hook.delivery_scope(did) == f"defect-promoted:{did}:v1"


# ── 2. the gates ───────────────────────────────────────────────────────────


@pytest.fixture
def emit(monkeypatch):
    calls: list = []

    async def _emit(event_type, *, project_id, payload, delivery_scope=None, **_kw):
        calls.append(SimpleNamespace(event_type=event_type, project_id=project_id, payload=payload,
                                     delivery_scope=delivery_scope))
        return 1

    monkeypatch.setattr(webhook_service, "emit_event", _emit)
    return calls


def _sessions(monkeypatch, rows):
    opened: list = []

    def _factory():
        db = MemDB(rows)
        opened.append(db)
        return db

    monkeypatch.setattr(hook, "AsyncSessionLocal", _factory)
    return opened


@pytest.mark.asyncio
async def test_offline_nothing_is_opened_and_nothing_sent(monkeypatch, emit):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    opened: list = []

    def _explode():
        opened.append(1)  # the emitter swallows errors: record the attempt itself
        raise AssertionError("offline must not open a session")

    monkeypatch.setattr(hook, "AsyncSessionLocal", _explode)

    assert await hook.emit_defect_promoted(uuid.uuid4()) == 0
    assert opened == [] and emit == []


@pytest.mark.asyncio
async def test_the_flag_off_sends_nothing(monkeypatch, emit):
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=False))
    opened = _sessions(monkeypatch, [_promoted()])

    assert await hook.emit_defect_promoted(uuid.uuid4()) == 0
    assert opened == [] and emit == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["pending_review", "rejected", "suggested", "missing", "no_cluster"])
async def test_only_an_effective_cluster_promotion_is_sent(monkeypatch, emit, case):
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    row = _promoted(approval_status=case if case not in ("missing", "no_cluster") else "approved",
                    cluster_id=None if case == "no_cluster" else "cl_7")
    _sessions(monkeypatch, [] if case == "missing" else [row])

    assert await hook.emit_defect_promoted(row.id) == 0
    assert emit == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "executed"])
async def test_an_effective_promotion_is_sent_once_with_its_scope(monkeypatch, emit, status):
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(return_value=True))
    row = _promoted(approval_status=status)
    opened = _sessions(monkeypatch, [row])

    assert await hook.emit_defect_promoted(str(row.id)) == 1

    (call,) = emit
    assert call.event_type == "defect.promoted"
    assert call.project_id == PID
    assert call.delivery_scope == f"defect-promoted:{row.id}:v1"
    assert call.payload["defect_id"] == str(row.id) and call.payload["run_id"] == str(RUN)
    # The emitter's own read session is closed before emit_event opens its own.
    assert opened[0].journal[-1] == "close"


@pytest.mark.asyncio
async def test_a_failure_is_logged_never_raised(monkeypatch):
    monkeypatch.setattr(webhook_service, "_post_allowed", AsyncMock(side_effect=RuntimeError("redis down")))

    assert await hook.emit_defect_promoted(uuid.uuid4()) == 0


# ── 3. the producers ───────────────────────────────────────────────────────


async def _promote(monkeypatch, *, initial_status: str, actor=None):
    """Drive the real promote_cluster on an in-memory session."""
    from app.models.postgres import FailureCluster, TestRun
    from app.services import defect_promotion_service as svc

    cluster = FailureCluster(id=uuid.uuid4(), test_run_id=RUN, cluster_id="cl_7", label="Checkout 500",
                             size=1, member_test_ids=[])
    run = TestRun(id=RUN, project_id=PID, build_number="b1", status="FAILED", primary_release_id=None)
    db = MemDB([cluster, run])
    monkeypatch.setattr(svc, "_resolve_defect_owner_from_memory", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "_find_duplicate_semantic", AsyncMock(return_value=(None, False)))
    monkeypatch.setattr(svc, "_create_jira_ticket", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(svc, "check_defect_promotion_policy", AsyncMock(return_value={
        "initial_status": initial_status, "requires_approval": initial_status != "approved",
        "policy_reasons": [] if initial_status == "approved" else ["Severity HIGH requires human review"],
    }))
    result = await svc.promote_cluster(
        run_id=str(RUN), cluster_id="cl_7", project_id=str(PID),
        request={"title": "Checkout 500", "severity": "MEDIUM", "project_key": "  PLAT "},
        db=db, actor=actor,
    )
    return db, result


@pytest.mark.asyncio
async def test_an_effective_promotion_stages_one_ledger_row(monkeypatch):
    engineer = user(UserRole.QA_ENGINEER, name="Quinn Engineer")

    db, result = await _promote(monkeypatch, initial_status="approved", actor=engineer)

    (row,) = [r for r in db.added if isinstance(r, ProjectActivityEvent)]
    assert row.event_type == "defect.promoted"
    assert row.entity_id == result["defect_id"]
    assert row.summary == "Failure cluster promoted to defect Checkout 500"
    assert row.context == {"cluster_id": "cl_7", "severity": "MEDIUM"}
    assert row.actor_id == engineer.id
    (promoted,) = [r for r in db.added if type(r).__name__ == "Defect"]
    assert promoted.policy_evaluation["jira_project_key"] == "PLAT"
    assert promoted.policy_evaluation["source_run_id"] == str(RUN)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_review", "rejected"])
async def test_a_promotion_that_waits_or_was_refused_records_nothing(monkeypatch, status):
    db, _result = await _promote(monkeypatch, initial_status=status)

    assert [r for r in db.added if isinstance(r, ProjectActivityEvent)] == []
    (promoted,) = [r for r in db.added if type(r).__name__ == "Defect"]
    assert promoted.policy_evaluation["jira_project_key"] == "PLAT"


@pytest.mark.asyncio
async def test_the_promote_route_emits_after_its_commit(monkeypatch, fake_redis):
    from app.routers import deep_investigation
    from app.models.postgres import TestRun

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    engineer = user(UserRole.QA_ENGINEER)
    journal: list[str] = []
    db = MemDB([engineer, membership(engineer, PID),
                TestRun(id=RUN, project_id=PID, build_number="b1", status="FAILED")], journal=journal)
    defect_id = str(uuid.uuid4())

    async def _promote_cluster(**kwargs):
        journal.append(f"promote actor={kwargs['actor'].id == engineer.id}")
        return {"defect_id": defect_id, "cluster_id": "cl_7", "severity": "HIGH", "title": "t"}

    async def _emit(did):
        journal.append(f"emit {did}")
        return 0

    async def _bump(project_id):
        journal.append("bump")

    monkeypatch.setattr(deep_investigation, "promote_cluster", _promote_cluster)
    monkeypatch.setattr("app.services.defect_promoted_webhook.emit_defect_promoted", _emit)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    monkeypatch.setattr("app.services.intelligence_snapshot_service.mark_stale", AsyncMock())

    app = make_app(deep_investigation.router, lambda: db, engineer)
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        response = await asyncio.wait_for(
            c.post(f"/api/v1/deep-investigate/{RUN}/clusters/cl_7/promote", json={"title": "t"}), timeout=5)

    assert response.status_code == 200, response.text
    assert journal.index("promote actor=True") < journal.index("commit") < journal.index("bump") \
        < journal.index(f"emit {defect_id}")


@pytest.mark.asyncio
async def test_a_failing_emitter_does_not_fail_the_promotion(monkeypatch, fake_redis):
    from app.routers import deep_investigation
    from app.models.postgres import TestRun

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    engineer = user(UserRole.QA_ENGINEER)
    db = MemDB([engineer, membership(engineer, PID), TestRun(id=RUN, project_id=PID, build_number="b1",
                                                               status="FAILED")])
    monkeypatch.setattr(deep_investigation, "promote_cluster", AsyncMock(return_value={
        "defect_id": str(uuid.uuid4()), "cluster_id": "cl_7", "severity": "HIGH", "title": "t"}))
    monkeypatch.setattr("app.services.defect_promoted_webhook.emit_defect_promoted",
                        AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr("app.services.intelligence_snapshot_service.mark_stale", AsyncMock())

    app = make_app(deep_investigation.router, lambda: db, engineer)
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        response = await c.post(f"/api/v1/deep-investigate/{RUN}/clusters/cl_7/promote", json={"title": "t"})

    assert response.status_code == 200, response.text


# ── 5. the catalogue: every offered event has a producer ───────────────────


def test_the_catalogue_text_says_when_it_fires():
    text = webhook_service.SUPPORTED_EVENTS["defect.promoted"]
    assert "Inbox › Approvals" in text
    assert "promoted_at" in text and "deep_link" in text


def test_defect_promoted_has_a_literal_producer():
    from tests.test_release_decided_webhook import KNOWN_UNSENT, _emitted_event_literals

    assert "defect.promoted" in _emitted_event_literals()
    assert KNOWN_UNSENT == set()
