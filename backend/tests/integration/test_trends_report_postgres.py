"""W08 TRND-01 on real PostgreSQL: the Trends report's payload, its recipients and its PDF route.

* the payload is the page's: ``build_trends_report``'s ``trend`` equals
  ``get_trend_data`` for the same scope (release and suite applied), and its
  ``summary`` equals ``trend_summary(scope)`` -- no third read;
* the recipients: a member of another project, an unknown id, an inactive
  member and a service account are all the same 422; two active members get
  ONE message through the stored relay and the ledger holds one
  ``report.emailed`` with the count and no address;
* ``GET /api/v1/reports/trends/pdf``: a VIEWER member gets the PDF (named
  ``trends-<slug>-14d.pdf``, naming the release and the suite), ``days=91`` is
  a 422, another tenant's project a 403, and each export leaves one
  ``report_trends_export_pdf`` audit row.

Uses ``tests/integration/defect_w06_world.py`` (P with release R and suite
``checkout-api``, Q, an outsider of Q, the app over ASGI). The relay is
patched (``_get_smtp_cfg``) and ``aiosmtplib.send`` records. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import SUITE, defect_world, scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOT_A_MEMBER = "Every recipient must be a member of this project."
STORED = {"enabled": True, "host": "relay.lan", "port": 25, "tls": False, "from_address": "qa@example.com"}


@pytest.fixture
async def world(monkeypatch):
    from app.core.config import settings
    from app.models.postgres import LaunchStatus, ProjectMember, TestCase, TestRun, UserRole
    from app.services.notification import email_service

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Nair")
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            w.ana_id = await w.user(db, role=UserRole.QA_ENGINEER.value)
            w.bob_id = await w.user(db, role=UserRole.TESTER.value)
            w.gone_id = await w.user(db, role=UserRole.QA_ENGINEER.value, is_active=False)
            w.svc_id = await w.user(db, role=UserRole.QA_ENGINEER.value, is_service_account=True)
            for uid in (w.lead_id, w.viewer_id, w.ana_id, w.bob_id, w.gone_id, w.svc_id):
                db.add(ProjectMember(project_id=w.P, user_id=uid, role=UserRole.QA_ENGINEER.value))
            now = datetime.now(timezone.utc)
            for days_ago, suite, release in ((1, SUITE, w.R), (2, SUITE, w.R), (2, "other", w.R), (3, SUITE, None)):
                run_id = uuid.uuid4()
                at = now - timedelta(days=days_ago)
                db.add(TestRun(id=run_id, project_id=w.P, build_number=f"trnd01-{uuid.uuid4().hex[:6]}",
                               jenkins_job="trnd01", status=LaunchStatus.FAILED, ingestion_source="unknown",
                               total_tests=4, passed_tests=3, failed_tests=1, skipped_tests=0, broken_tests=0,
                               primary_suite_name=suite, primary_release_id=release, created_at=at))
                await db.flush()
                for i, status in enumerate(("PASSED", "PASSED", "PASSED", "FAILED")):
                    db.add(TestCase(id=uuid.uuid4(), test_run_id=run_id, test_name=f"t{i}", suite_name=suite,
                                    status=status, test_fingerprint=f"{suite}-{i}", created_at=at))
        w.lead, w.viewer = w.jwt(w.lead_id), w.jwt(w.viewer_id)
        w.sent = []

        async def send(message, **kwargs):
            w.sent.append((message, kwargs))

        monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
        monkeypatch.setattr(email_service, "_get_smtp_cfg", AsyncMock(return_value=dict(STORED)))
        monkeypatch.setattr(email_service.aiosmtplib, "send", send)
        yield w


async def test_the_payload_is_the_pages_reads_for_the_same_scope(world):
    from app.services import metrics_service
    from app.services.trend_summary_service import trend_summary
    from app.services.trends_report_service import build_trends_report

    w = world
    s = scope(w.P, 14, releases=(w.R,), suites=(SUITE,))
    async with w.sessions() as db:
        payload = await build_trends_report(db, s)
        trend = await metrics_service.get_trend_data(db, str(w.P), 14, suite_name=SUITE, release_id=str(w.R))
        summary = await trend_summary(db, s)
    # Release R and suite checkout-api: the two seeded days and the world's own run of today
    # (1 failed, no rows); the release-less run and suite "other" are filtered out.
    assert payload["trend"] == trend and len(trend) == 3
    assert payload["summary"] == summary
    assert payload["summary"]["executions"]["total"] == 9
    assert [row["suite_name"] for row in payload["suites"]] == [SUITE]
    assert payload["project_name"].startswith("w06a-p-")
    assert payload["meta"]["scope"]["releases"][0]["id"] == str(w.R)
    assert payload["meta"]["scope"]["suites"] == [SUITE]


async def test_only_active_human_members_of_the_project_may_receive_it(world):
    from app.models.postgres import ProjectActivityEvent

    w = world
    path = "/api/v1/reports/email-trends"
    for stranger in (w.outsider_id, uuid.uuid4(), w.gone_id, w.svc_id):
        refused = await w.client.post(path, json={
            "project_id": str(w.P), "days": 14, "recipient_user_ids": [str(w.ana_id), str(stranger)],
        }, headers=w.lead)
        assert refused.status_code == 422 and refused.json()["detail"] == NOT_A_MEMBER, refused.text
    assert w.sent == []

    sent = await w.client.post(path, json={
        "project_id": str(w.P), "days": 14, "release_id": str(w.R), "suite_name": [SUITE],
        "recipient_user_ids": [str(w.ana_id), str(w.bob_id)],
    }, headers=w.lead)
    assert sent.status_code == 200, sent.text
    assert sent.json()["sent_to"] == 2
    [(message, kwargs)] = w.sent
    assert kwargs["hostname"] == "relay.lan"
    assert sorted(message["To"].split(", ")) == sorted(
        [f"w06a-u{w.ana_id.hex[:8]}-{w.tag}@example.com", f"w06a-u{w.bob_id.hex[:8]}-{w.tag}@example.com"]
    )
    async with w.sessions() as db:
        rows = list((await db.execute(select(ProjectActivityEvent).where(
            ProjectActivityEvent.project_id == w.P, ProjectActivityEvent.event_type == "report.emailed",
        ))).scalars().all())
    [row] = rows
    assert row.summary == "Report Trends, last 14 days was emailed to 2 recipients"
    assert row.context == {"count": 2, "window_days": 14} and row.actor_name == "Priya Nair"
    assert "@" not in repr((row.summary, row.context, row.diff, row.entity_label))


async def test_the_pdf_route_any_member_its_limits_and_its_audit_row(world):
    from app.models.postgres import AccessAuditLog

    pypdf = pytest.importorskip("pypdf")
    w = world
    path = "/api/v1/reports/trends/pdf"
    params = {"project_id": str(w.P), "days": 14, "release_id": str(w.R), "suite_name": SUITE}
    response = await w.client.get(path, params=params, headers=w.viewer)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert re.fullmatch(r'attachment; filename="trends-w06a-p-[a-z0-9]+-14d\.pdf"',
                        response.headers["content-disposition"]), response.headers["content-disposition"]
    text = " ".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(response.content)).pages)
    # The context block names the suite and the release (by its name).
    assert SUITE in text and f"w06a-r-{w.tag}" in text and "Pass rate" in text

    too_long = await w.client.get(path, params={**params, "days": 91}, headers=w.viewer)
    assert too_long.status_code == 422, too_long.text
    foreign = await w.client.get(path, params={"project_id": str(w.P), "days": 14}, headers=w.outsider)
    assert foreign.status_code == 403, foreign.text

    async with w.sessions() as db:
        audits = list((await db.execute(select(AccessAuditLog).where(
            AccessAuditLog.action == "report_trends_export_pdf", AccessAuditLog.project_id == w.P,
        ))).scalars().all())
    [audit] = audits
    assert audit.actor_user_id == w.viewer_id
    assert set(audit.after_value) == {"days", "release_ids", "suite_names", "size_bytes"}
    assert audit.after_value["days"] == 14 and audit.after_value["release_ids"] == [str(w.R)]
    assert audit.after_value["size_bytes"] == len(response.content)
