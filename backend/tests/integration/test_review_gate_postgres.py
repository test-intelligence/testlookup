"""W23c AGT-10 against PostgreSQL at head: the enforcement date, the gate it drives, the re-post staging.

What the unit tests cannot show without a database: the ``app_settings``
upsert (``ON CONFLICT (key)``), the would-refuse aggregate (``->>`` on the JSON
``after_value``, ``COUNT(DISTINCT ...)``) over real audit rows, the PDF
export's refusal and its audit rows, the accept handler's savepoint and the
outbox's unique constraint, and the real refresh: Postgres on its own session,
then the Redis mirror, deleted by a PUT.

Fixture rule (contract §6.4): ``dispose_engine_for_loop()`` before building
the engine and after disposing it; ``close_redis()`` in teardown (the PUT and
the refresh bind the app's Redis pool); Mongo is never bound (the pipeline
event emit after an accept is replaced), so ``close_mongo()`` is never called.
The snapshot is reset for the module's tests and the session pin restored
after. Request work runs in one transaction that is rolled back; the one test
of the real refresh commits a row through the app's own sessions and deletes
it again. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` at head; skips otherwise.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PATH = "/api/v1/settings/review-gate"


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def conn(monkeypatch):
    from app.core.config import settings
    from app.db import postgres as app_postgres
    from app.db.redis_client import close_redis
    from app.services import review_gate_setting as rgs

    dsn = _dsn()
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    monkeypatch.setattr("app.services.pipeline_event_log.emit_event", AsyncMock())
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=1, max_overflow=0)
    rgs.reset_snapshot_for_tests()
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                yield connection
            finally:
                await outer.rollback()
    finally:
        rgs.reset_snapshot_for_tests()
        rgs.pin_snapshot_for_tests(None)
        await close_redis()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _session(connection) -> AsyncSession:
    # A commit inside releases a SAVEPOINT; the outer transaction is still rolled back.
    return AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)


def _client(connection, user) -> AsyncClient:
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import app_settings, reports, reviews

    app = FastAPI()
    for module in (app_settings, reports, reviews):
        app.include_router(module.router)

    async def _db():
        session = _session(connection)
        try:
            yield session
        finally:
            await session.close()

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _admin(db: AsyncSession, tag: str):
    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind
    from app.models.postgres import User

    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"gate-{tag}-{user_id.hex[:8]}@example.invalid",
                username=f"gate_{tag}_{user_id.hex[:8]}", full_name="Gate admin",
                hashed_password="!unusable", role="ADMIN"))
    await db.flush()
    user = SimpleNamespace(id=user_id, role="ADMIN", username=f"gate_{tag}", full_name=None,
                           email="gate@example.invalid", is_active=True, is_synthetic=False)
    return _bind_credential_kind(user, CREDENTIAL_KIND_JWT)


async def _reviewed_run(db: AsyncSession, tag: str, *, github: bool, pr: bool):
    """A project, a run (with or without PR context), its completed pipeline
    and a PENDING report review of it; a GitHub integration row when asked."""
    from app.models.postgres import AgentPipelineRun, GitHubIntegration, Project, ReviewRequest, TestRun

    project = Project(id=uuid.uuid4(), name=f"Gate {tag}", slug=f"gate-{tag}-{uuid.uuid4().hex[:6]}",
                      is_active=True)
    db.add(project)
    await db.flush()
    run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"g-{tag}", branch="main",
                  status="FAILED", pr_number=7 if pr else None, ci_repo="acme/payments" if pr else None,
                  ci_provider="github_actions" if pr else None)
    db.add(run)
    await db.flush()
    pipeline = AgentPipelineRun(id=uuid.uuid4(), test_run_id=run.id, workflow_type="offline", status="completed")
    db.add(pipeline)
    await db.flush()
    review = ReviewRequest(id=uuid.uuid4(), project_id=project.id, kind="report", subject_type="pipeline_run",
                           subject_id=str(pipeline.id), pipeline_run_id=pipeline.id, test_run_id=run.id,
                           workflow_type="offline", state="pending_review", ai_disclaimer_version="test")
    db.add(review)
    if github:
        db.add(GitHubIntegration(project_id=project.id, enabled=True, repo_owner="acme",
                                 repo_name="payments", api_base_url="https://api.github.com"))
    await db.flush()
    return SimpleNamespace(project=project, run=run, pipeline=pipeline, review=review)


async def _audit_actions(db: AsyncSession, run_id) -> list[tuple[str, str]]:
    from app.models.postgres import AccessAuditLog

    rows = (await db.execute(
        select(AccessAuditLog.action, AccessAuditLog.after_value).where(
            AccessAuditLog.action.like("ai_report.%"))
    )).all()
    return sorted((action, value["channel"]) for action, value in rows if value.get("run_id") == str(run_id))


def _pdf_without_rendering(monkeypatch) -> None:
    """The gate decision and its audit rows run for real; composing and drawing
    the PDF are not what this module proves."""
    monkeypatch.setattr("app.services.report_composition_service.compose_report",
                        AsyncMock(return_value=SimpleNamespace(draft_watermark="")))
    monkeypatch.setattr("app.services.report_pdf_renderer.render_report_pdf", lambda report: b"%PDF-1.4 gate")


async def test_unset_on_upgrade_then_a_past_date_refuses_the_pdf_and_clearing_allows_it(conn, monkeypatch):
    _pdf_without_rendering(monkeypatch)
    async with _session(conn) as db:
        stored = (await db.execute(text("SELECT count(*) FROM app_settings WHERE key = 'review_gate'"))).scalar_one()
        assert stored == 0  # D17: nothing is written on upgrade
        admin = await _admin(db, "a")
        world = await _reviewed_run(db, "a", github=False, pr=False)
        await db.commit()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    async with _client(conn, admin) as client:
        before = await client.get(_PATH)
        assert (before.json()["enforced"], before.json()["source"]) == (False, "unset"), before.text
        put = await client.put(_PATH, json={"enforced_from": past.isoformat()})
        assert put.status_code == 200, put.text
        got = await client.get(_PATH)
        assert got.status_code == 200, got.text
        assert (got.json()["enforced"], got.json()["source"]) == (True, "date")
        assert datetime.fromisoformat(got.json()["enforced_from"]) == past

        refused = await client.get(f"/api/v1/reports/runs/{world.run.id}/pdf")
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "report_pending_review"

        cleared = await client.put(_PATH, json={"enforced_from": None})
        assert (cleared.json()["enforced"], cleared.json()["source"]) == (False, "unset")
        allowed = await client.get(f"/api/v1/reports/runs/{world.run.id}/pdf")
        assert allowed.status_code == 200, allowed.text
    async with _session(conn) as db:
        assert await _audit_actions(db, world.run.id) == [
            ("ai_report.distribution_refused", "report_pdf"),
            ("ai_report.distribution_would_refuse", "report_pdf"),
        ]
        audits = (await db.execute(text(
            "SELECT action, changed_fields FROM settings_audit_log WHERE setting_key = 'review_gate'"
        ))).all()
        assert [tuple(row) for row in audits] == [("updated", ["enforced_from"])] * 2
        value = (await db.execute(text("SELECT value FROM app_settings WHERE key = 'review_gate'"))).scalar_one()
        assert value == {"enforced_from": None}


async def test_the_would_refuse_count_reads_real_audit_rows(conn):
    from app.models.postgres import AccessAuditLog

    async with _session(conn) as db:
        admin = await _admin(db, "b")
        await db.commit()
    async with _client(conn, admin) as client:
        before = (await client.get(_PATH)).json()["would_refuse"]
    assert before["measured"] is True

    now = datetime.now(timezone.utc)
    runs = [str(uuid.uuid4()) for _ in range(4)]
    seeded = [  # (run, channel, days ago): six rows of three runs inside the window, two older
        (runs[0], "report_pdf", 1), (runs[0], "report_pdf", 2), (runs[0], "share_link_html", 3),
        (runs[1], "report_pdf", 10), (runs[2], "report_pdf", 29), (runs[2], "share_link_html", 20),
        (runs[3], "report_pdf", 35), (runs[3], "share_link_html", 40),
    ]
    async with _session(conn) as db:
        for run_id, channel, days in seeded:
            db.add(AccessAuditLog(
                action="ai_report.distribution_would_refuse", actor_name=None,
                after_value={"channel": channel, "run_id": run_id, "reason": "not_enforced_would_refuse"},
                created_at=now - timedelta(days=days),
            ))
        await db.commit()
        oldest = (await db.execute(text("SELECT min(created_at) FROM access_audit_logs"))).scalar_one()
    async with _client(conn, admin) as client:
        after = (await client.get(_PATH)).json()["would_refuse"]

    assert after["measured"] is True
    assert after["decisions"] - before["decisions"] == 6
    assert after["runs"] - before["runs"] == 3
    channels_before = {c["channel"]: c["decisions"] for c in before["by_channel"]}
    channels_after = {c["channel"]: c["decisions"] for c in after["by_channel"]}
    assert channels_after["report_pdf"] - channels_before.get("report_pdf", 0) == 4
    assert channels_after["share_link_html"] - channels_before.get("share_link_html", 0) == 2
    counts = [c["decisions"] for c in after["by_channel"]]
    assert counts == sorted(counts, reverse=True)
    since = datetime.fromisoformat(after["since"])
    window_start = now - timedelta(days=30)
    expected_since = max(window_start, oldest)
    assert abs((since - expected_since).total_seconds()) < 60


async def test_an_accept_stages_one_repost_row_for_a_pr_run_with_a_github_row_and_none_without(conn):
    from app.models.postgres import ReviewRequest, RunDownstreamOutbox

    async with _session(conn) as db:
        admin = await _admin(db, "c")
        with_pr = await _reviewed_run(db, "c1", github=True, pr=True)
        bare = await _reviewed_run(db, "c2", github=False, pr=False)
        await db.commit()
    async with _client(conn, admin) as client:
        first = await client.post(f"/api/v1/reviews/{with_pr.review.id}/accept", json={})
        assert first.status_code == 200, first.text
        second = await client.post(f"/api/v1/reviews/{with_pr.review.id}/accept", json={})
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "review_not_pending"
        other = await client.post(f"/api/v1/reviews/{bare.review.id}/accept", json={})
        assert other.status_code == 200, other.text
    async with _session(conn) as db:
        rows = (await db.execute(
            select(RunDownstreamOutbox.run_id, RunDownstreamOutbox.operation, RunDownstreamOutbox.payload,
                   RunDownstreamOutbox.status, RunDownstreamOutbox.queue)
            .where(RunDownstreamOutbox.run_id.in_([with_pr.run.id, bare.run.id]))
        )).all()
        assert [(r.run_id, r.operation, r.status, r.queue) for r in rows] == [
            (with_pr.run.id, "pr_comment_repost", "pending", "default")
        ]
        assert rows[0].payload == {"test_run_id": str(with_pr.run.id), "review_id": str(with_pr.review.id)}
        states = dict((await db.execute(
            select(ReviewRequest.id, ReviewRequest.state).where(
                ReviewRequest.id.in_([with_pr.review.id, bare.review.id]))
        )).all())
        assert states == {with_pr.review.id: "accepted", bare.review.id: "accepted"}


async def test_the_real_refresh_reads_postgres_then_the_mirror_and_a_put_drops_it(conn):
    from app.db.postgres import AsyncSessionLocal
    from app.db.redis_client import get_redis
    from app.services import review_gate_setting as rgs
    from app.services.review_gate_vocab import CACHE_KEY

    now = datetime.now(timezone.utc).replace(microsecond=0)
    future, past = now + timedelta(days=5), now - timedelta(hours=1)
    redis = get_redis()
    await redis.delete(CACHE_KEY)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('review_gate', CAST(:v AS json))"),
            {"v": json.dumps({"enforced_from": future.isoformat()})},
        )
        await db.commit()
    try:
        snapshot = await rgs.refresh_snapshot(force=True)  # mirror miss -> Postgres on its own session
        assert snapshot.loaded is True and snapshot.enforced_from == future
        assert json.loads(await redis.get(CACHE_KEY)) == {"enforced_from": future.isoformat()}
        assert rgs.enforced_now() is False

        async with AsyncSessionLocal() as db:  # the row moves without a PUT ...
            await db.execute(text("UPDATE app_settings SET value = CAST(:v AS json) WHERE key = 'review_gate'"),
                             {"v": json.dumps({"enforced_from": past.isoformat()})})
            await db.commit()
        assert (await rgs.refresh_snapshot(force=True)).enforced_from == future  # ... the mirror is read first

        await rgs.after_commit(past)  # what the PUT does after its commit
        assert await redis.get(CACHE_KEY) is None
        assert rgs.enforced_now() is True  # this pod at once
        rgs.reset_snapshot_for_tests()  # another pod, never loaded
        assert (await rgs.refresh_snapshot()).enforced_from == past
        assert rgs.enforced_now() is True
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM app_settings WHERE key = 'review_gate'"))
            await db.commit()
        await redis.delete(CACHE_KEY)
