"""DEF-06: approving a pending defect files its Jira ticket ON that defect; rejecting closes it.

``POST /api/v1/deep-investigate/defects/{defect_id}/review`` through the real
route, ``approve_action`` / ``reject_action``, ``file_issue_for_defect`` and
the shared filing core, over an in-memory session that evaluates the real
queries and UPDATEs (``tests/fakes/fake_defect_world.MemDB``). Jira is the
process-wide HTTP recorder; the E7.6 claim ledger is in memory (as
``tests/test_defect_jira_endpoint.py``). One journal orders commits, Jira
calls, epoch bumps and the webhook emitter.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.models.postgres import Defect, ProjectActivityEvent, UserRole
from app.routers import deep_investigation
from app.services import defect_jira_service as svc
from tests.fakes.fake_defect_world import (
    ONLINE_CFG,
    HttpRecorder,
    MemDB,
    defect,
    make_app,
    membership,
    user,
)

pytestmark = pytest.mark.asyncio

PID = uuid.uuid4()
RUN = uuid.uuid4()
SIGNATURE = "a" * 64


class _Ledger:
    """The E7.6 claim / finish sessions, in memory (same state machine)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def claim(self, *, key, **_kw):
        row = self.rows.get(key)
        if row is not None:
            if row["status"] == "executed":
                return "executed", dict(row["result"])
            if row["status"] != "failed":
                return "executing", {}
        self.rows[key] = {"status": "executing", "result": {}}
        return "claimed", {}

    async def finish(self, *, key, status, result_payload=None, error_code=None, **_kw):
        row = self.rows.setdefault(key, {"status": status, "result": {}})
        row["status"] = status
        if result_payload is not None:
            row["result"] = dict(result_payload)


def _prefill():
    return {
        "signature": SIGNATURE,
        "summary": "[TestLookup] cluster label",
        "description": "cluster evidence",
        "error_message": "AssertionError",
        "occurrences": {"first_seen": None, "last_seen": None, "failing_runs": 1},
        "context": {"branch": "main", "build_number": "7", "ci_run_url": None},
        "ai_analysis": None,
        "deep_link": "http://localhost:3000/runs/x",
    }


@pytest.fixture
def world(monkeypatch, fake_redis):
    from app.services import tool_call_idempotency as tci

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    http = HttpRecorder().install(monkeypatch)
    ledger = _Ledger()
    monkeypatch.setattr(tci, "_claim", ledger.claim)
    monkeypatch.setattr(tci, "_finish", ledger.finish)
    lead = user(UserRole.QA_LEAD, name="Lee Lead")
    pending = defect(
        PID, cluster_id="cl_1", approval_status="pending_review", severity="CRITICAL",
        title="Checkout total off", description="Reviewed text for the ticket.",
        policy_evaluation={"policy_reasons": ["Severity CRITICAL requires human review"],
                           "jira_project_key": "PLAT", "source_run_id": str(RUN)},
    )
    journal: list[str] = []
    db = MemDB([lead, pending, membership(lead, PID, role="QA_LEAD")], journal=journal)

    cfg_calls: list = []

    async def _cfg(session):
        journal.append("jira-config")
        cfg_calls.append(1)
        return dict(ONLINE_CFG)

    async def _pref(session, project_id, *, fingerprint=None, cluster_id=None):
        return _prefill()

    async def _bump(project_id):
        journal.append(f"bump {project_id}")

    async def _emit(defect_id):
        journal.append(f"emit {defect_id}")
        return 0

    original_send = http.send

    async def _send(request):
        journal.append(f"http {request.method} {request.url.path}")
        return await original_send(request)

    http.send = _send
    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "build_prefill", _pref)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", _bump)
    monkeypatch.setattr("app.services.defect_promoted_webhook.emit_defect_promoted", _emit)

    async def review(body, *, defect_id=None, principal=None):
        app = make_app(deep_investigation.router, lambda: db, principal or lead)
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://t") as client:
            return await asyncio.wait_for(
                client.post(f"/api/v1/deep-investigate/defects/{defect_id or pending.id}/review", json=body),
                timeout=5,
            )

    return SimpleNamespace(db=db, http=http, journal=journal, lead=lead, defect=pending, review=review,
                           cfg_calls=cfg_calls, ledger=ledger)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)


def _events(db) -> list[str]:
    return [r.event_type for r in db.added if isinstance(r, ProjectActivityEvent)]


# ── 1. approve offline: committed first, nothing sent, the reason recorded ──


async def test_offline_the_approval_commits_first_and_jira_is_not_filed(world, offline):
    response = await world.review({"action": "approve"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jira_status"] == "not_filed"
    assert body["jira_reason"] == "offline_mode"
    assert body["jira_error"] is None
    assert body["approval_status"] == "approved"
    assert world.http.requests == []
    # Commit 1 lands before any Jira code runs.
    assert world.journal.index("commit") < world.journal.index("jira-config")
    filing = world.defect.policy_evaluation["jira_filing"]
    assert filing["status"] == "not_filed" and filing["reason"] == "offline_mode"
    assert world.defect.policy_evaluation["jira_project_key"] == "PLAT"   # the rest is kept
    assert _events(world.db) == ["defect.promoted"]


# ── 2. approve online, no twin: one POST, ON this defect ───────────────────


async def test_online_approval_files_one_issue_on_the_approved_defect(world, online):
    world.http.route("POST", "/rest/api/3/issue", (201, {"key": "PLAT-7", "id": "1007"}))
    defects_before = len(world.db.of(Defect))

    response = await world.review({"action": "approve"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jira_status"] == "filed"
    assert body["approval_status"] == "executed"
    assert body["jira_url"] == "https://acme.atlassian.net/browse/PLAT-7"
    # The Inbox toast reads the key from jira_ticket.key (G-D).
    assert body["jira_ticket"] == {"key": "PLAT-7", "url": "https://acme.atlassian.net/browse/PLAT-7"}
    (post,) = world.http.calls("POST", "/rest/api/3/issue")
    fields = world.http.body(post)["fields"]
    assert fields["project"] == {"key": "PLAT"}
    assert fields["summary"] == "[TestLookup] Checkout total off"
    assert "Reviewed text for the ticket." in str(fields["description"])
    # No second Defect row: the key landed on the approved one.
    assert len(world.db.of(Defect)) == defects_before
    assert world.defect.jira_ticket_id == "PLAT-7"
    assert world.defect.signature_fingerprint == SIGNATURE
    assert world.defect.approval_status == "executed"
    assert _events(world.db) == ["defect.promoted", "defect.ticket_created"]
    created = [r for r in world.db.added if isinstance(r, ProjectActivityEvent)][1]
    assert created.context == {"ticket_key": "PLAT-7"}
    # Two commits, the bump after the last, the webhook after the bump.
    commits = [i for i, e in enumerate(world.journal) if e == "commit"]
    assert len(commits) == 2
    http_at = world.journal.index("http POST /rest/api/3/issue")
    assert commits[0] < http_at < commits[1]
    assert commits[1] < world.journal.index(f"bump {PID}") < world.journal.index(f"emit {world.defect.id}")


async def test_the_one_click_path_and_approval_share_one_claim(world, online):
    """Approving after a one-click filing of the same signature whose answer was
    lost replays the recorded issue instead of filing a second one."""
    world.http.route("POST", "/rest/api/3/issue", (201, {"key": "PLAT-8"}))
    from app.services import tool_call_idempotency as tci

    key = tci.tool_call_key(tool=tci.JIRA_TICKET_TOOL, scope_id=f"one-click:{PID}", subject_id=f"{SIGNATURE}:first")
    world.ledger.rows[key] = {"status": "executed",
                              "result": {"jira_key": "PLAT-3", "jira_url": "https://acme.atlassian.net/browse/PLAT-3"}}

    response = await world.review({"action": "approve"})

    assert response.json()["jira_status"] == "linked"
    assert world.http.calls("POST") == []
    assert world.defect.jira_ticket_id == "PLAT-3"


# ── 3. an ACTIVE linked twin of the same signature ─────────────────────────


async def test_a_linked_twin_is_reused_not_refiled(world, online):
    twin = defect(PID, signature_fingerprint=SIGNATURE, jira_ticket_id="PLAT-2",
                  jira_ticket_url="https://acme.atlassian.net/browse/PLAT-2", jira_status="In Progress",
                  resolution_status="IN_PROGRESS", recurrence_count=4)
    world.db.rows.append(twin)
    world.http.route("POST", "/rest/api/3/issue/PLAT-2/comment", (201, {}))

    response = await world.review({"action": "approve"})

    body = response.json()
    assert body["jira_status"] == "linked"
    assert body["jira_ticket"]["key"] == "PLAT-2"
    assert body["jira_url"] == "https://acme.atlassian.net/browse/PLAT-2"
    assert world.http.calls("POST", "/rest/api/3/issue") == []
    assert world.defect.jira_ticket_id == "PLAT-2"
    assert world.defect.jira_status == "In Progress"
    assert world.defect.duplicate_of == twin.id
    assert twin.recurrence_count == 5
    assert "defect.ticket_created" not in _events(world.db)


async def test_a_twin_in_another_project_is_not_reused(world, online):
    world.db.rows.append(defect(uuid.uuid4(), signature_fingerprint=SIGNATURE, jira_ticket_id="OTHER-1"))
    world.http.route("POST", "/rest/api/3/issue", (201, {"key": "PLAT-9"}))

    response = await world.review({"action": "approve"})

    assert response.json()["jira_status"] == "filed"
    assert world.defect.jira_ticket_id == "PLAT-9"


# ── 4. Jira refuses or does not confirm: the approval stands ───────────────


@pytest.mark.parametrize(
    ("answer", "status", "fragment"),
    [((500, {}), "unknown", "testlookup-sig-"), ((400, {"errors": {"x": "y"}}), "failed", "HTTP 400")],
)
async def test_a_jira_failure_keeps_the_committed_approval(world, online, answer, status, fragment):
    world.http.route("POST", "/rest/api/3/issue", answer)

    response = await world.review({"action": "approve"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jira_status"] == status
    assert fragment in body["jira_error"]
    assert body["approval_status"] == "approved"
    assert world.defect.jira_ticket_id is None
    assert world.defect.policy_evaluation["jira_filing"]["status"] == status
    assert world.journal.count("commit") == 2


# ── 5. no project key: no Jira code at all ─────────────────────────────────


async def test_no_project_key_means_no_jira_code(world, online):
    world.defect.policy_evaluation = {"policy_reasons": ["Severity HIGH requires human review"]}

    response = await world.review({"action": "approve"})

    assert response.json()["jira_status"] == "not_requested"
    assert response.json()["jira_ticket"] is None
    assert world.cfg_calls == []
    assert world.http.requests == []
    assert world.journal.count("commit") == 1


async def test_the_integrations_key_name_is_read_too(world, offline):
    world.defect.cluster_id = None
    world.defect.policy_evaluation = {"requested_project_key": "OPS", "requested_run_id": str(RUN)}

    response = await world.review({"action": "approve"})

    assert response.json()["jira_status"] == "not_filed"
    # Not a cluster promotion (D-12): no defect.promoted row.
    assert _events(world.db) == []


# ── 6. reject: needs a reason, closes the defect, the reason in the ledger ──


async def test_reject_without_a_reason_is_422(world, offline):
    for body in ({"action": "reject"}, {"action": "reject", "reason": "   "}):
        response = await world.review(body)
        assert response.status_code == 422
        assert response.json()["detail"] == "Rejection reason is required."
    assert world.defect.approval_status == "pending_review"


async def test_reject_closes_the_defect_as_invalid_with_the_reason(world, offline):
    response = await world.review({"action": "reject", "reason": "  duplicate of PLAT-2  "})

    assert response.status_code == 200, response.text
    assert response.json()["approval_status"] == "rejected"
    assert world.defect.approval_status == "rejected"
    assert world.defect.resolution_status == "INVALID"
    assert world.defect.resolved_at is not None
    (row,) = [r for r in world.db.added if isinstance(r, ProjectActivityEvent)]
    assert row.event_type == "defect.updated"
    assert row.summary.endswith("status → Invalid (rejected in review)")
    assert row.context["reason"] == "duplicate of PLAT-2"
    assert row.diff["before"] == {"status": "Open"} and row.diff["after"] == {"status": "Invalid"}
    assert world.journal.index("commit") < world.journal.index(f"bump {PID}")
    assert not any(e.startswith("emit") for e in world.journal)


# ── authorization stays as it was ──────────────────────────────────────────


async def test_a_lead_of_another_project_cannot_review(world, offline):
    other = user(UserRole.QA_LEAD)
    world.db.rows += [other, membership(other, uuid.uuid4(), role="QA_LEAD")]

    response = await world.review({"action": "approve"}, principal=other)

    assert response.status_code == 403
    assert world.defect.approval_status == "pending_review"


async def test_a_missing_defect_keeps_its_404_text(world, offline):
    response = await world.review({"action": "approve"}, defect_id=uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["detail"] == "Defect not found or not in pending_review status."
