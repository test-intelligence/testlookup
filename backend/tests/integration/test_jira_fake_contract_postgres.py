"""One-click Jira filing against the contract fake, on real PostgreSQL.

Owner request 2026-10-10: test the Jira integration with a mock service.
``test_defect_jira_exactly_once_postgres.py`` proves the at-most-once rules
with the payload builders and the HTTP calls patched out. This drives the
REAL path -- ``build_prefill`` over seeded failures, ``_adf_from_prefill``,
``_jira_post``/``_jira_get`` through the shared client -- into
``tests/fakes/fake_integrations.py``, which refuses what Jira Cloud refuses
(an invalid Atlassian Document, a summary with a newline or over 255
characters, an unknown project, a label with a space).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, adf_problems, create_app

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

DOMAIN = "fake-jira.example.test"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.config import settings
    from app.models.postgres import Project, TestCase, TestRun
    from app.services import defect_jira_service as svc

    engine = create_async_engine(_dsn(), pool_size=6, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    pid, run_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    fingerprints = {
        "plain": f"{tag}-plain",
        "newline": f"{tag}-newline",
        "long": f"{tag}-long",
        "lost": f"{tag}-lost",
        "refused": f"{tag}-refused",
    }
    async with sessions.begin() as db:
        db.add(Project(id=pid, name=f"jirafake-{tag}", slug=f"jirafake-{tag}", is_active=True))
        await db.flush()
        db.add(TestRun(id=run_id, project_id=pid, build_number=f"b-{tag}", branch="main",
                       status="FAILED", total_tests=3, failed_tests=3, created_at=now - timedelta(hours=1),
                       start_time=now - timedelta(hours=1), ci_run_url="https://ci.example.com/run/7"))
        await db.flush()
        names = {
            "plain": "test_checkout_total",
            # A parametrized id can carry a newline; Jira refuses one in a summary.
            "newline": "test_parse[multi\nline input]",
            "long": "test_" + "very_long_parametrized_case_" * 12,
            "lost": "test_lost_answer",
            "refused": "test_refused",
        }
        for key, name in names.items():
            db.add(TestCase(test_run_id=run_id, test_fingerprint=fingerprints[key], test_name=name,
                            suite_name="Checkout", status="FAILED", created_at=now - timedelta(minutes=30),
                            error_message="AssertionError: <total> 41 != 40",
                            stack_trace="Traceback (most recent call last):\n  File \"t.py\", line 3\nAssertionError"))

    state = FakeState(base_url=f"https://{DOMAIN}")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))

    async def resolved(db):
        return {"enabled": True, "domain": DOMAIN, "email": state.jira_email,
                "api_token": state.jira_token, "default_project_key": "QA"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(svc, "get_http_client", lambda: client)

    async def file(fingerprint, **kwargs):
        async with sessions() as db:
            out = await svc.create_or_link_issue(
                db, str(pid), SimpleNamespace(id=None, username="it"), fingerprint=fingerprint, **kwargs,
            )
            await db.commit()
            return out

    try:
        yield SimpleNamespace(svc=svc, sessions=sessions, pid=pid, state=state, fp=fingerprints, file=file)
    finally:
        monkeypatch.undo()
        async with engine.begin() as db:
            await db.execute(text("DELETE FROM agent_action_ledger WHERE project_id = :p"), {"p": pid})
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await client.aclose()
        await engine.dispose()


async def test_a_defect_files_an_issue_jira_accepts_and_links_it(world):
    from app.models.postgres import Defect

    out = await world.file(world.fp["plain"])

    assert out["jira_key"] == "QA-1" and out["deduplicated"] is False
    assert out["jira_url"] == f"https://{DOMAIN}/browse/QA-1"
    issue = world.state.issues["QA-1"]
    assert issue["fields"]["summary"] == "[TestLookup] test_checkout_total"
    assert adf_problems(issue["fields"]["description"]) == []
    assert {"testlookup", "one-click-defect"} <= set(issue["fields"]["labels"])
    assert any(label.startswith("testlookup-sig-") for label in issue["fields"]["labels"])
    async with world.sessions() as db:
        defect = (await db.execute(select(Defect).where(Defect.jira_ticket_id == "QA-1",
                                                        Defect.project_id == world.pid))).scalar_one()
    assert defect.resolution_status == "OPEN" and defect.signature_fingerprint == world.fp["plain"]


async def test_filing_again_links_and_comments_instead_of_a_second_issue(world):
    out = await world.file(world.fp["plain"])

    assert out["deduplicated"] is True and out["jira_key"] == "QA-1"
    assert out["recurrence_comment_posted"] is True
    assert len(world.state.issues) == 1
    [comment] = world.state.issues["QA-1"]["comments"]
    assert adf_problems(comment["body"]) == []


async def test_a_test_name_with_a_newline_still_files(world):
    out = await world.file(world.fp["newline"])
    summary = world.state.issues[out["jira_key"]]["fields"]["summary"]
    assert "\n" not in summary and summary.startswith("[TestLookup] test_parse[multi")


async def test_a_long_test_name_files_within_the_summary_limit(world):
    out = await world.file(world.fp["long"])
    assert len(world.state.issues[out["jira_key"]]["fields"]["summary"]) <= 255


async def test_an_answer_lost_after_jira_created_the_issue_is_reconciled_not_refiled(world):
    """Jira created the issue, then answered 500. The retry must find it by its
    label instead of filing a second one."""
    fp = world.fp["lost"]
    before = len(world.state.issues)
    world.state.faults.append({"method": "POST", "path": "/rest/api/3/issue", "status": 500, "after": True})

    with pytest.raises(HTTPException) as first:
        await world.file(fp)
    assert first.value.status_code == 409
    assert first.value.detail["code"] == "jira_outcome_unknown"
    assert len(world.state.issues) == before + 1  # Jira did create it

    out = await world.file(fp)
    assert out["deduplicated"] is True and "found in Jira by its label" in out["message"]
    assert len(world.state.issues) == before + 1  # and nothing was filed twice


async def test_jira_refusing_the_issue_is_reported_and_records_nothing(world):
    from app.models.postgres import Defect

    fp = world.fp["refused"]
    with pytest.raises(HTTPException) as refused:
        await world.file(fp, jira_project_key="NOPE")
    assert refused.value.status_code == 502 and "HTTP 400" in refused.value.detail
    async with world.sessions() as db:
        rows = (await db.execute(select(Defect).where(Defect.signature_fingerprint == fp))).scalars().all()
    assert rows == []


async def test_status_sync_mirrors_done_and_flags_a_test_that_still_fails(world):
    from app.models.postgres import Defect

    world.state.issues["QA-1"]["fields"]["status"] = {"name": "Done", "statusCategory": {"key": "done"}}
    async with world.sessions() as db:
        # The sync reads every linked open defect in the database; other
        # tests' rows answer 404 from the fake, which is theirs to count.
        result = await world.svc.sync_external_statuses(db, cap=1000)
        await db.commit()
    assert result["checked"] >= 1
    async with world.sessions() as db:
        defect = (await db.execute(select(Defect).where(Defect.jira_ticket_id == "QA-1",
                                                        Defect.project_id == world.pid))).scalar_one()
    assert defect.jira_status == "Done"
    assert defect.external_status_conflict is True  # closed in Jira, still failing here
