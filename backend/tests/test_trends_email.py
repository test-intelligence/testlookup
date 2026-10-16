"""W08 TRND-01: ``POST /api/v1/reports/email-trends`` and ``GET .../email-trends/availability``.

Driven over HTTP on the reports router, with the real role guard, the real
recipient resolution, the real relay resolution (``email_service.get_smtp_config``
over a patched stored config), the real egress gates and the real
``send_html_email``; only ``aiosmtplib.send`` records instead of sending.

1. the relay saved in Settings is the one dialled, ONE message whose ``To``
   holds every member address;
2. a disabled (or host-less) stored relay is a 503 that names Settings ›
   Notifications, before anything is sent or recorded;
3. an unknown or non-member id is one 422 text (and the report is not built);
   0 and more than 10 recipients have their own texts; duplicates collapse;
4. VIEWER, TESTER and QA_ENGINEER are refused (POST and availability);
5. a foreign project is refused before the report is built;
6. offline, a public relay is a 503 with the egress text (POST and
   availability), and a recipient outside the allow-list too;
7. a relay timeout is a 504 and records nothing; any other relay error a 502
   that names the error's type and never its text;
8. a send stages exactly one ``report.emailed`` with the count and no address;
10. a hostile suite name reaches the HTML only escaped.

The one-connection rule: ``tests/regression/test_trends_email_holds_one_connection.py``.
Real members and the real report: ``tests/integration/test_trends_report_postgres.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.routers import reports as reports_router
from app.services import trends_report_service
from app.services.analytics_scope import AnalyticsScope
from app.services.notification import egress, email_service

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
HOSTILE = "<img src=x onerror=alert(1)>"
ANA = SimpleNamespace(id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), email="Ana@corp.example")
BOB = SimpleNamespace(id=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), email="bob@corp.example")
STORED = {"enabled": True, "host": "relay.lan", "port": 25, "tls": False, "from_address": "qa@corp.example"}
PATH = "/api/v1/reports/email-trends"


def _payload() -> dict:
    from app.services.trend_summary_service import summarize, utc_today

    trend = [{"date": utc_today().isoformat(), "passed": 9, "failed": 1, "skipped": 0, "broken": 0,
              "total": 10, "pass_rate": 90.0}]
    suites = [{"suite_name": HOSTILE, "passed": 9, "failed": 1, "skipped": 0, "pass_rate": 90.0}]
    meta = {
        "schema_version": 3,
        "scope": {"projects": [{"id": str(PROJECT), "name": "Phoenix"}], "releases": [],
                  "suites": [HOSTILE], "window": {"from": "2026-09-05", "to": "2026-09-18", "days": 14,
                                                  "timezone": "UTC"}},
        "totals": {"matched_runs": 1, "total_runs": 1, "matched_executions": 10, "total_executions": 10},
        "pass_rate_basis": "executions", "ignored_filters": [], "generated_at": "2026-09-18T12:00:00+00:00",
    }
    return {"project_name": "Phoenix", "window_days": 14, "generated_at": "2026-09-18T12:00:00+00:00",
            "trend": trend, "suites": suites, "summary": summarize(trend, suites, days=14, today=utc_today()),
            "pass_rate_delta_pp": None, "meta": meta}


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    """The request session: answers the member lookup from ``members`` (the
    statement's IN list, as the real WHERE would), records close and commit."""

    def __init__(self, events: list):
        self.new, self.dirty, self.deleted = [], [], []
        self.members = [ANA, BOB]
        self.events = events
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        ids = next(v for v in stmt.compile().params.values() if isinstance(v, (list, tuple)))
        return _Rows([m for m in self.members if m.id in ids])

    async def close(self):
        self.events.append("close")

    async def commit(self):
        self.commits += 1
        self.events.append("commit")


def _user(role: str):
    return SimpleNamespace(id=uuid.uuid4(), role=role, username=role.lower(), full_name=f"{role} person",
                           email=f"{role.lower()}@corp.example", is_active=True, is_service_account=False)


@pytest.fixture
def world(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    events: list = []
    db = _DB(events)
    state = SimpleNamespace(user=_user("QA_LEAD"), db=db, events=events, ledger=[], sent=[],
                            stored=dict(STORED))

    async def _db():
        yield db

    async def stored_cfg():
        return dict(state.stored)

    async def send(message, **kwargs):
        events.append("send")
        state.sent.append((message, kwargs))

    async def record(_db, **kwargs):
        state.ledger.append(kwargs)

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(reports_router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(reports_router, "resolve_analytics_scope", AsyncMock(return_value=AnalyticsScope(
        project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=("payments",), days=14,
    )))
    state.build = AsyncMock(side_effect=lambda *a, **k: _payload())
    monkeypatch.setattr(trends_report_service, "build_trends_report", state.build)
    monkeypatch.setattr(email_service, "_get_smtp_cfg", stored_cfg)
    monkeypatch.setattr(email_service.aiosmtplib, "send", send)
    monkeypatch.setattr(reports_router, "record_activity", record)

    app = FastAPI()
    app.include_router(reports_router.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.client = TestClient(app, raise_server_exceptions=False)
    return state


def _post(w, **body):
    payload = {"project_id": str(PROJECT), "days": 14, "recipient_user_ids": [str(ANA.id), str(BOB.id)], **body}
    return w.client.post(PATH, json=payload)


def _html_of(message) -> str:
    [_plain, part] = message.get_payload()
    return part.get_payload(decode=True).decode("utf-8")


def test_the_relay_saved_in_settings_is_dialled_with_one_message_to_every_member(world):
    response = _post(world)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "sent", "sent_to": 2, "recipient": "ana@corp.example", "window_days": 14}
    [(message, kwargs)] = world.sent
    assert kwargs["hostname"] == "relay.lan" and kwargs["port"] == 25
    assert message["To"] == "ana@corp.example, bob@corp.example"
    assert message["Subject"] == "Trends report — Phoenix — last 14 days"
    assert message["From"] == "qa@corp.example"
    # The request's connection went back BEFORE the relay was asked, the ledger after.
    assert world.events.index("close") < world.events.index("send") < world.events.index("commit")


def test_a_success_stages_one_emailed_row_with_the_count_and_no_address(world):
    assert _post(world, recipient_emails=["Ext@Corp.Example"]).status_code == 200
    [row] = world.ledger
    assert row["event_type"] == "report.emailed" and row["entity_type"] == "export"
    assert row["entity_id"] == str(PROJECT) and row["project_id"] == PROJECT
    assert row["entity_label"] == "Trends, last 14 days"
    assert row["context"] == {"count": 3, "window_days": 14}
    assert "@" not in repr(row), "no address anywhere in the ledger row"
    assert world.db.commits == 1


@pytest.mark.parametrize("stored", [
    dict(STORED, enabled=False),
    dict(STORED, host=""),
])
def test_a_relay_that_cannot_send_is_503_before_anything_is_sent(world, monkeypatch, stored):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    world.stored = stored
    response = _post(world)
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "Email is not configured — Settings › Notifications."
    assert world.sent == [] and world.ledger == [] and world.db.commits == 0
    availability = world.client.get(PATH + "/availability").json()
    assert availability == {"available": False, "code": "not_configured",
                            "reason": "Email is not configured — Settings › Notifications."}


def test_availability_answers_true_for_a_usable_relay(world):
    assert world.client.get(PATH + "/availability").json() == {"available": True, "code": None, "reason": None}
    assert "close" in world.events, "the request's connection is released before get_smtp_config"


@pytest.mark.parametrize("ids", [
    [str(uuid.uuid4())],                     # unknown
    [str(ANA.id), str(uuid.uuid4())],        # one member, one not
])
def test_an_id_that_is_not_an_eligible_member_is_one_422(world, ids):
    response = _post(world, recipient_user_ids=ids)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Every recipient must be a member of this project."
    assert world.sent == [] and world.ledger == []
    world.build.assert_not_awaited()


def test_an_inactive_or_foreign_member_is_answered_like_an_unknown_id(world):
    """The lookup returns only active human members of the project; the fake
    leaves BOB out exactly as the real WHERE drops an inactive or foreign user."""
    world.db.members = [ANA]
    response = _post(world)
    assert response.status_code == 422
    assert response.json()["detail"] == "Every recipient must be a member of this project."


def test_no_recipient_too_many_and_duplicates(world):
    none = _post(world, recipient_user_ids=[])
    assert none.status_code == 422 and none.json()["detail"] == "Choose at least one recipient."
    many = _post(world, recipient_emails=[f"x{i}@corp.example" for i in range(5)],
                 recipient_email="legacy@corp.example",
                 recipient_user_ids=[str(ANA.id), str(BOB.id)])
    assert many.status_code == 200, "2 members + 6 typed = 8 distinct"
    world.db.members = [ANA, BOB] + [SimpleNamespace(id=uuid.uuid4(), email=f"m{i}@corp.example") for i in range(4)]
    too_many = _post(world, recipient_emails=[f"y{i}@corp.example" for i in range(5)],
                     recipient_user_ids=[str(m.id) for m in world.db.members])
    assert too_many.status_code == 422 and too_many.json()["detail"] == "Choose at most 10 recipients."
    world.sent.clear()
    dup = _post(world, recipient_user_ids=[str(ANA.id), str(ANA.id)], recipient_emails=["ANA@corp.example"],
                recipient_email="ana@CORP.example")
    assert dup.status_code == 200 and dup.json()["sent_to"] == 1
    assert world.sent[0][0]["To"] == "ana@corp.example"
    assert _post(world, recipient_emails=[f"z{i}@corp.example" for i in range(6)]).status_code == 422


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
def test_below_qa_lead_both_routes_are_refused(world, role):
    world.user = _user(role)
    assert _post(world).status_code == 403
    assert world.client.get(PATH + "/availability").status_code == 403
    world.build.assert_not_awaited()
    assert world.sent == []


def test_a_foreign_project_is_refused_before_the_report_is_built(world, monkeypatch):
    monkeypatch.setattr(reports_router, "resolve_project_scope",
                        AsyncMock(side_effect=HTTPException(status_code=403, detail="Not a member of this project")))
    assert _post(world).status_code == 403
    world.build.assert_not_awaited()
    assert world.sent == []


def test_offline_a_public_relay_is_503_with_the_egress_text(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "")
    monkeypatch.setattr(egress, "host_is_local", lambda host: False)
    world.stored = dict(STORED, host="smtp.gmail.com")
    response = _post(world)
    assert response.status_code == 503 and "smtp.gmail.com" in response.json()["detail"]
    assert "AI_OFFLINE_MODE=true" in response.json()["detail"]
    availability = world.client.get(PATH + "/availability").json()
    assert availability["available"] is False and availability["code"] == "egress_blocked"
    assert "smtp.gmail.com" in availability["reason"]
    assert world.sent == [] and world.ledger == []


def test_offline_a_recipient_outside_the_allow_list_is_503(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "corp.example")
    monkeypatch.setattr(egress, "host_is_local", lambda host: host == "relay.lan")
    assert _post(world).status_code == 200, "a LAN relay and the corp domain pass offline"
    refused = _post(world, recipient_emails=["exec@gmail.com"])
    assert refused.status_code == 503 and "gmail.com" in refused.json()["detail"]
    assert len(world.sent) == 1, "the refused message was never handed to the relay"


def test_a_relay_timeout_is_504_and_records_nothing(world, monkeypatch):
    from app.services import report_service

    async def slow(message, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(report_service, "SEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(email_service.aiosmtplib, "send", slow)
    response = _post(world)
    assert response.status_code == 504, response.text
    assert response.json()["detail"] == "The mail relay did not answer within 20 s; the report may still arrive."
    assert world.ledger == [] and world.db.commits == 0


def test_any_other_relay_error_is_502_naming_the_type_only(world, monkeypatch):
    import aiosmtplib

    async def refuse(message, **kwargs):
        raise aiosmtplib.SMTPRecipientsRefused([])

    async def banner(message, **kwargs):
        raise RuntimeError("550 relay.lan says: secret-banner-text")

    monkeypatch.setattr(email_service.aiosmtplib, "send", refuse)
    first = _post(world)
    assert first.status_code == 502
    assert first.json()["detail"] == "The mail relay refused the report (SMTPRecipientsRefused)."
    monkeypatch.setattr(email_service.aiosmtplib, "send", banner)
    second = _post(world)
    assert second.status_code == 502 and "secret-banner-text" not in second.text
    assert second.json()["detail"] == "The mail relay refused the report (RuntimeError)."
    assert world.ledger == []


def test_a_hostile_suite_name_reaches_the_html_only_escaped(world):
    assert _post(world).status_code == 200
    html = _html_of(world.sent[0][0])
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img" not in html and "<script" not in html
    assert "Report context" in html and "Trend confidence" in html


def test_the_email_html_renders_the_payload_the_pdf_renders():
    from app.services import report_service

    html = report_service.build_trends_email_html(_payload())
    assert "Phoenix" in html and "90.0%" in html and "Daily Breakdown" in html and "Suites" in html
    assert "pass rate per test execution" in html
    assert html.count("&lt;img src=x onerror=alert(1)&gt;") >= 2, "the context block and the suite table"
