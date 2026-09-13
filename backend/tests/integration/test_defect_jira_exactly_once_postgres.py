"""Real PostgreSQL proof: one-click Jira filing happens at most once per signature.

``POST /projects/{id}/defects/jira`` is the one path that files Jira issues.
Two failures used to file a duplicate, and both rest on things a mocked
session cannot see:

* **a double-submit** is serialized by ``pg_advisory_xact_lock``: the second
  request must wait for the first to commit and then deduplicate;
* **an outcome lost after Jira accepted** is caught by the ledger claim that
  ``tool_call_idempotency.run_once`` commits before the POST, on its own
  connection, so the router's rollback cannot take it back.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _Jira:
    """Fake Jira: counts issue POSTs, can hold them on a gate or fail them."""

    def __init__(self):
        self.issue_posts = 0
        self.labels: list[list[str]] = []
        self.posted = asyncio.Event()
        self.gate: asyncio.Event | None = None
        self.fail_with: Exception | None = None
        self.search_keys: list[str] = []
        self.searched: list[str] = []

    async def post(self, cfg, path, json):
        if path != "/rest/api/3/issue":
            return _Resp(201, {})  # recurrence comment on a deduplicated request
        self.issue_posts += 1
        self.labels.append(list(json["fields"]["labels"]))
        self.posted.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_with is not None:
            raise self.fail_with
        return _Resp(201, {"key": f"IT-{self.issue_posts}"})

    async def get(self, cfg, path, params=None):
        self.searched.append(params["jql"])
        return _Resp(200, {"issues": [{"key": k} for k in self.search_keys]})


@pytest.fixture
async def wired(monkeypatch):
    from app.core.config import settings
    from app.services import defect_jira_service as svc

    engine = create_async_engine(_dsn(), pool_size=6, max_overflow=0)
    async with engine.begin() as db:
        row = (await db.execute(text("SELECT project_id FROM test_runs ORDER BY created_at LIMIT 1"))).first()
    if row is None:
        await engine.dispose()
        pytest.skip("database has no test run fixture")
    project_id = row[0]
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # The claim/finish sessions must hit the same database as the request.
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    signature = uuid.uuid4().hex  # unique per test: rows never collide across runs
    jira = _Jira()

    async def _cfg(db):
        return {
            "enabled": True, "domain": "example.atlassian.net", "email": "bot@example.com",
            "api_token": "token", "default_project_key": "IT",
        }

    async def _prefill(db, project_id, *, fingerprint=None, cluster_id=None):
        return {
            "signature": signature,
            "summary": "[TestLookup] exactly_once_test",
            "description": "integration fixture",
            "context": {"build_number": "1"},
            "deep_link": "http://localhost:3000/runs/x",
            "ai_analysis": None,
        }

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "build_prefill", _prefill)
    monkeypatch.setattr(svc, "_adf_from_prefill", lambda prefill, comment: {"type": "doc", "version": 1, "content": []})
    monkeypatch.setattr(svc, "_jira_post", jira.post)
    monkeypatch.setattr(svc, "_jira_get", jira.get)

    async def submit(**kwargs):
        async with sessions() as db:
            out = await svc.create_or_link_issue(
                db, str(project_id), SimpleNamespace(id=None, username="it"),
                cluster_id=f"it-{signature}", **kwargs,
            )
            await db.commit()
            return out

    yield SimpleNamespace(
        svc=svc, engine=engine, sessions=sessions, project_id=project_id,
        signature=signature, jira=jira, submit=submit,
    )

    async with engine.begin() as db:
        await db.execute(
            text("DELETE FROM defects WHERE project_id = :p AND signature_fingerprint = :s"),
            {"p": project_id, "s": signature},
        )
        await db.execute(
            text("DELETE FROM agent_action_ledger WHERE project_id = :p AND target_id LIKE :s"),
            {"p": project_id, "s": f"{signature}%"},
        )
    await engine.dispose()


async def _defect_keys(w) -> list[str]:
    async with w.engine.begin() as db:
        rows = await db.execute(
            text("SELECT jira_ticket_id FROM defects WHERE project_id = :p AND signature_fingerprint = :s"),
            {"p": w.project_id, "s": w.signature},
        )
        return [r[0] for r in rows]


async def _claim_statuses(w) -> list[str]:
    async with w.engine.begin() as db:
        rows = await db.execute(
            text("SELECT status FROM agent_action_ledger WHERE project_id = :p AND target_id LIKE :s"),
            {"p": w.project_id, "s": f"{w.signature}%"},
        )
        return [r[0] for r in rows]


async def test_two_concurrent_submits_file_exactly_one_issue(wired):
    w = wired
    w.jira.gate = asyncio.Event()

    first = asyncio.ensure_future(w.submit())
    await asyncio.wait_for(w.jira.posted.wait(), timeout=10)
    second = asyncio.ensure_future(w.submit())
    await asyncio.sleep(0.5)

    # Without the lock the second request finds the first one's claim and
    # fails fast with a 409 instead of waiting.
    assert not second.done(), "the second submit did not wait for the first"

    w.jira.gate.set()
    a = await asyncio.wait_for(first, timeout=10)
    b = await asyncio.wait_for(second, timeout=10)

    assert w.jira.issue_posts == 1
    assert a["deduplicated"] is False
    assert b["deduplicated"] is True
    assert b["jira_key"] == a["jira_key"]
    assert await _defect_keys(w) == [a["jira_key"]]


async def test_a_commit_lost_after_jira_accepted_is_never_filed_again(wired):
    w = wired

    async with w.sessions() as db:
        first = await w.svc.create_or_link_issue(
            db, str(w.project_id), SimpleNamespace(id=None, username="it"),
            cluster_id=f"it-{w.signature}",
        )
        await db.rollback()  # the router's commit failed after Jira accepted

    assert w.jira.issue_posts == 1
    assert await _defect_keys(w) == []
    assert await _claim_statuses(w) == ["executed"], "the claim did not survive the rollback"

    retry = await w.submit()

    assert w.jira.issue_posts == 1, "the retry filed a second Jira issue"
    assert retry["jira_key"] == first["jira_key"]
    assert retry["deduplicated"] is True
    assert await _defect_keys(w) == [first["jira_key"]]


async def test_a_timed_out_post_is_reconciled_by_label_and_never_reposted(wired):
    import httpx

    w = wired
    w.jira.fail_with = httpx.ReadTimeout("read timed out")

    with pytest.raises(HTTPException) as exc:
        await w.submit()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "jira_outcome_unknown"
    assert await _claim_statuses(w) == ["executing"]
    label = exc.value.detail["jira_label"]
    assert label in w.jira.labels[0]

    # Search lag: the first retry finds nothing and still refuses to file.
    w.jira.fail_with = None
    with pytest.raises(HTTPException) as again:
        await w.submit()
    assert again.value.status_code == 409
    assert w.jira.issue_posts == 1

    # Once Jira's index catches up, the retry links the issue it finds.
    w.jira.search_keys = ["IT-77"]
    linked = await w.submit()

    assert w.jira.issue_posts == 1
    assert all(label in jql for jql in w.jira.searched)
    assert linked["jira_key"] == "IT-77"
    assert await _defect_keys(w) == ["IT-77"]
    assert await _claim_statuses(w) == ["executed"]
