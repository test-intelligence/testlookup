"""W23c AGT-10 §3.2 — the share-link view's refusal is a readable page for a browser.

An anonymous viewer opening ``/api/v1/shared/reports/{token}`` of an unreviewed
AI report while the gate is enforced is a browser, not the SPA: it gets an HTML
409 page "Report awaiting review" with the (escaped) message. Every JSON
caller keeps today's JSON 409 body, and the PDF link keeps JSON.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.postgres import get_db
from app.routers import shared_reports
from app.services import report_distribution_policy as policy
from app.services.review_envelope import ReviewEnvelope

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
SENTINEL = "<script>alert('Zq-share-xyz')</script>"


class _DB:
    def __init__(self) -> None:
        self.commits = 0
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def refused(monkeypatch):
    link = SimpleNamespace(run_id=uuid.uuid4(), project_id=uuid.uuid4(), report_layout="executive",
                           created_by_name="qa", expires_at=None)
    monkeypatch.setattr("app.services.share_link_service.validate_share_link", AsyncMock(return_value=link))
    monkeypatch.setattr("app.services.report_composition_service.compose_report",
                        AsyncMock(return_value=SimpleNamespace(draft_watermark="")))
    envelope = ReviewEnvelope(True, "pending_review", "", review_id="Zq-review-1")
    decision = policy.DistributionDecision(False, policy.REFUSED, envelope, None, True)
    monkeypatch.setattr(policy, "decide_run_distribution", AsyncMock(return_value=decision))
    record = AsyncMock()
    monkeypatch.setattr(policy, "record_distribution", record)
    rendered = AsyncMock(side_effect=AssertionError("a refused report is never rendered"))
    monkeypatch.setattr("app.services.report_html_renderer.render_report_html", rendered)
    monkeypatch.setattr("app.services.report_pdf_renderer.render_report_pdf", rendered)
    db = _DB()
    app = FastAPI()
    app.include_router(shared_reports.router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return SimpleNamespace(client=client, db=db, record=record)


@pytest.mark.asyncio
async def test_a_browser_gets_the_awaiting_review_page(refused):
    async with refused.client as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 409
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    body = resp.text
    assert "<h1>Report awaiting review</h1>" in body
    assert (
        "<p>This AI-generated report has not been accepted by a human reviewer, "
        "so it cannot be distributed.</p>"
    ) in body
    assert "<script" not in body and "<style" not in body
    assert refused.db.commits == 1  # the refusal's audit row is still committed
    assert refused.record.await_args.kwargs["channel"] == "share_link_html"


@pytest.mark.asyncio
async def test_the_message_is_escaped(refused, monkeypatch):
    real = policy.refusal_detail
    monkeypatch.setattr(policy, "refusal_detail", lambda d: {**real(d), "message": SENTINEL})
    async with refused.client as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 409
    assert SENTINEL not in resp.text
    assert "&lt;script&gt;alert(&#x27;Zq-share-xyz&#x27;)&lt;/script&gt;" in resp.text


@pytest.mark.parametrize("accept", [None, "application/json", "application/json, text/plain, */*",
                                    "application/json, text/html"])
@pytest.mark.asyncio
async def test_json_callers_keep_their_json(refused, accept):
    headers = {"Accept": accept} if accept else {}
    async with refused.client as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc", headers=headers)
    assert resp.status_code == 409
    assert resp.headers["content-type"] == "application/json"
    detail = resp.json()["detail"]
    assert detail["code"] == "report_pending_review"
    assert detail["review_id"] == "Zq-review-1"


@pytest.mark.asyncio
async def test_the_pdf_link_keeps_its_json_refusal(refused):
    async with refused.client as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc/pdf", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 409
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["detail"]["code"] == "report_pending_review"
