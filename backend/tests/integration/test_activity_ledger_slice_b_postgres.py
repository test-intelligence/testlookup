"""W21 ACT-01 slice (b): every configuration / integration event has a producer that WRITES a row.

"Declaration is not emission": each test drives the real route handler (or service / worker helper) against a real
database and reads ``project_activity_events`` back:

* gate policies: created, updated (only on a real difference), activated (publish), deactivate -> ``policy.updated``;
  the system-default policy (no project) records nothing;
* ownership: rule created / updated / deleted, bulk import and CODEOWNERS import as ONE row each, team channel set /
  removed (the channel target never enters the ledger);
* webhooks: created (host only), a pause -> ``webhook.disabled`` + ``webhook.updated``, deleted;
* knowledge sources: added, removed, and ``knowledge_source.synced`` from the worker's ``run_sync``;
* retention: a policy change -> ``retention_policy.updated``; the purge helper writes ``retention.purge_executed``
  on its own session;
* feature flags: a project entering / leaving a flag's allow-list -> ``feature_flag.project_toggled`` per project;
* ``project.reset``: an attempt row for a confirmed reset only;
* notification preferences: a project-bound preference records, an all-projects one does not;
* GitHub / GitLab: ``integration.tested`` with the probe verdict, ``integration.disconnected`` on a GitHub delete.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, literal_column, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _noop(*_a, **_k):
    return None


@pytest.fixture
async def world(monkeypatch):
    import app.db.postgres as pg
    import app.services.cache_service as cache
    import app.services.webhook_service as webhook_service
    from app.models.postgres import (
        FeatureFlag,
        GitHubIntegration,
        KnowledgeSource,
        NotificationPreference,
        Project,
        ProjectActivityEvent,
        ProjectRetentionPolicy,
        ReleaseGatePolicy,
        ServiceOwnershipRule,
        TeamNotificationChannel,
        User,
        UserRole,
        WebhookSubscription,
    )

    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Attempt-mode rows, audit writers and the purge helper open their own session.
    monkeypatch.setattr(pg, "AsyncSessionLocal", factory, raising=False)
    monkeypatch.setattr(webhook_service, "AsyncSessionLocal", factory, raising=False)
    monkeypatch.setattr(cache, "bump_analytics_epoch", _noop)
    monkeypatch.setattr(cache, "bump_analytics_epochs", _noop)

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    admin = User(id=uuid.uuid4(), email=f"admin-{marker}@example.test", username=f"admin-{marker}",
                 full_name="Priya Admin", hashed_password="x", role=UserRole.ADMIN, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Ledger {marker}", slug=f"ledger-{marker}", is_active=True)
    async with factory() as db:
        db.add_all([admin, project])
        await db.commit()
    flag_key = f"ledger_{marker}"
    try:
        yield SimpleNamespace(factory=factory, admin=admin, project=project, marker=marker, flag_key=flag_key)
    finally:
        async with factory() as db:
            await db.execute(delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project.id))
            for model in (ReleaseGatePolicy, ServiceOwnershipRule, TeamNotificationChannel, WebhookSubscription,
                          KnowledgeSource, ProjectRetentionPolicy, NotificationPreference, GitHubIntegration):
                await db.execute(delete(model).where(model.project_id == project.id))
            await db.execute(delete(NotificationPreference).where(NotificationPreference.user_id == admin.id))
            await db.execute(delete(FeatureFlag).where(FeatureFlag.key == flag_key))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.commit()
        async with factory() as db:
            try:
                await db.execute(delete(User).where(User.id == admin.id))
                await db.commit()
            except Exception:  # an audit row may still reference the user
                await db.rollback()
        await engine.dispose()


async def _rows(w, prefix: str = ""):
    from app.models.postgres import ProjectActivityEvent

    async with w.factory() as db:
        stmt = select(ProjectActivityEvent).where(ProjectActivityEvent.project_id == w.project.id)
        if prefix:
            stmt = stmt.where(ProjectActivityEvent.event_type.startswith(prefix))
        # ``occurred_at`` is a Python now(): on Windows' clock two rows written back to back tie, and the
        # order they came back in flipped (W22a / W22b local runs). Ties go to the inserting transaction
        # (``xmin``), then to the row's position (``ctid``) — insertion order for this append-only table.
        rows = (await db.execute(stmt.order_by(
            ProjectActivityEvent.occurred_at,
            literal_column("project_activity_events.xmin::text::bigint"),
            literal_column("project_activity_events.ctid"),
        ))).scalars().all()
    for row in rows:
        # A template whose context key no producer supplies renders an em dash.
        assert "—" not in row.summary, f"unfilled placeholder in {row.event_type}: {row.summary}"
    return rows


async def test_policy_create_update_publish_deactivate(world):
    w = world
    from app.models.schemas import ReleaseGatePolicyCreate, ReleaseGatePolicyUpdate
    from app.routers import release_gate_policies as r

    async with w.factory() as db:
        policy = await r.create_policy(ReleaseGatePolicyCreate(name="Strict", project_id=w.project.id),
                                       current_user=w.admin, db=db)
    async with w.factory() as db:
        await r.update_policy(policy.id, ReleaseGatePolicyUpdate(name="Stricter"), current_user=w.admin, db=db)
    async with w.factory() as db:
        # The same value again is not a change.
        await r.update_policy(policy.id, ReleaseGatePolicyUpdate(name="Stricter"), current_user=w.admin, db=db)
    async with w.factory() as db:
        await r.publish_policy(policy.id, current_user=w.admin, db=db)
    async with w.factory() as db:
        await r.deactivate_policy(policy.id, current_user=w.admin, db=db)

    rows = await _rows(w, "policy.")
    assert [x.event_type for x in rows] == ["policy.created", "policy.updated", "policy.activated", "policy.updated"]
    assert all(x.actor_id == w.admin.id and x.entity_id == str(policy.id) for x in rows)
    assert rows[0].summary == "Release gate policy Strict v1 was created"
    assert rows[1].summary == "Release gate policy Stricter v1 changed: name"
    assert rows[2].summary == "Release gate policy Stricter v1 was activated"
    assert rows[3].summary == "Release gate policy Stricter v1 changed: deactivated"


async def test_ownership_rules_imports_and_team_channels(world):
    w = world
    from app.models.schemas import (
        CodeownersImportRequest,
        OwnershipBulkImportRequest,
        OwnershipRuleCreate,
        OwnershipRuleUpdate,
        TeamChannelUpsert,
    )
    from app.routers import ownership as r

    pid = w.project.id
    async with w.factory() as db:
        rule = await r.create_ownership_rule(pid, OwnershipRuleCreate(
            match_type="suite_name", match_pattern="checkout*", service_name="checkout", team_name="Payments",
        ), db=db, current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.update_ownership_rule(pid, rule.id, OwnershipRuleUpdate(team_name="Billing"), db=db,
                                      current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.update_ownership_rule(pid, rule.id, OwnershipRuleUpdate(team_name="Billing"), db=db,
                                      current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.delete_ownership_rule(pid, rule.id, db=db, current_user=w.admin, _=w.admin)
    item = {"match_type": "component", "match_pattern": "cart", "service_name": "cart", "team_name": "Cart"}
    async with w.factory() as db:
        await r.bulk_import_rules(pid, OwnershipBulkImportRequest(rules=[item, {**item, "match_pattern": "bag"}]),
                                  db=db, current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.import_codeowners(pid, CodeownersImportRequest(source="text", text="/src/api/ @acme/api\n"),
                                  db=db, current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.upsert_team_channel(pid, "Payments", TeamChannelUpsert(
            channel_type="slack", target="https://hooks.slack.test/secret-path"), db=db, current_user=w.admin, _=w.admin)
    async with w.factory() as db:
        await r.delete_team_channel(pid, "Payments", db=db, current_user=w.admin, _=w.admin)

    rows = await _rows(w)
    assert [x.event_type for x in rows] == [
        "ownership_rule.created", "ownership_rule.updated", "ownership_rule.deleted",
        "ownership_rules.bulk_imported", "codeowners.imported", "team_channel.set", "team_channel.removed",
    ]
    assert rows[0].summary == "Ownership rule checkout* → Payments was created"
    assert rows[1].summary == "Ownership rule checkout* → Billing changed: team_name"
    assert rows[1].diff["before"] == {"team_name": "Payments"}
    assert rows[3].summary == "Imported 2 ownership rules"
    assert rows[4].summary == "Imported 1 rules from CODEOWNERS"
    assert rows[5].summary == "Notification channel set for team Payments"
    assert "secret-path" not in str(rows[5].context)
    assert rows[6].summary == "Notification channel removed for team Payments"


async def test_webhook_create_pause_delete(world, monkeypatch):
    w = world
    import app.services.webhook_service as svc

    monkeypatch.setattr(svc, "_assert_safe_target_url", _noop)
    async with w.factory() as db:
        row = await svc.create_subscription(
            db, project_id=w.project.id, actor=w.admin, name="CI hook", target_url="https://ci.example.test/hook/tok",
            events=["run.completed"], enabled=True, max_retries=3, secret=None,
        )
        await db.commit()
    async with w.factory() as db:
        await svc.update_subscription(db, subscription_id=row.id, actor=w.admin, name="CI hook 2", target_url=None,
                                      events=None, enabled=False, max_retries=None, secret=None)
        await db.commit()
    async with w.factory() as db:
        await svc.delete_subscription(db, row.id, w.admin)
        await db.commit()

    rows = await _rows(w, "webhook.")
    assert [x.event_type for x in rows] == ["webhook.created", "webhook.disabled", "webhook.updated", "webhook.deleted"]
    assert rows[0].summary == "Webhook to ci.example.test was created"
    assert "tok" not in str(rows[0].context)
    assert rows[1].summary == "Webhook CI hook 2 was disabled"
    assert rows[2].summary == "Webhook CI hook 2 changed: name"


async def test_knowledge_source_added_synced_removed(world, monkeypatch):
    w = world
    import app.services.knowledge_chunking_service as chunking
    import app.services.knowledge_sync_service as sync
    from app.models.schemas import KnowledgeSourceCreate
    import app.services.knowledge_source_service as ks
    from app.routers import knowledge_sources as r

    # The knowledge_rag flag gate is pinned open: this test is about the ledger.
    monkeypatch.setattr(ks, "require_rag_enabled_async", _noop)
    async with w.factory() as db:
        source = await r.create_knowledge_source(KnowledgeSourceCreate(
            source_type="internal_url", title="Runbook", canonical_url=f"https://wiki.internal/{w.marker}",
        ), project_id=w.project.id, db=db, current_user=w.admin)

    class _Connector:
        async def fetch_content(self, *_a):
            return SimpleNamespace(content_hash="h-" + w.marker)

    async def _bind(connector, _db):
        return connector

    async def _chunks(*_a, **_k):
        return 4

    async def _store(*_a, **_k):
        return "raw/path"

    monkeypatch.setattr(sync.settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(sync, "get_connector", lambda _t: _Connector())
    monkeypatch.setattr(sync, "bind_to_deployment", _bind)
    monkeypatch.setattr(sync, "store_raw_content", _store)
    monkeypatch.setattr(chunking, "chunk_and_index", _chunks)
    import app.services.rag_staleness_service as staleness

    async def _zero(*_a, **_k):
        return 0

    monkeypatch.setattr(staleness, "mark_cases_stale_for_source", _zero)
    from app.models.postgres import KnowledgeSource

    async with w.factory() as db:
        live = await db.get(KnowledgeSource, source.id)
        result = await sync.run_sync(db, live, trigger="manual")
    assert result["status"] == "synced", result
    async with w.factory() as db:
        await r.delete_knowledge_source(source.id, db=db, current_user=w.admin)

    rows = await _rows(w, "knowledge_source.")
    assert [x.event_type for x in rows] == [
        "knowledge_source.added", "knowledge_source.synced", "knowledge_source.removed",
    ]
    assert rows[0].summary == "Knowledge source Runbook was added"
    assert rows[1].actor_type == "system" and rows[1].context["chunk_count"] == 4
    assert rows[2].actor_id == w.admin.id


async def test_retention_policy_change_and_purge_row(world):
    w = world
    from app.models.schemas import RetentionPolicyWrite
    from app.routers import retention as r
    from app.worker import tasks

    async with w.factory() as db:
        await r.put_retention_policy(w.project.id, RetentionPolicyWrite(enabled=True, runs_days=120), db=db,
                                     current_user=w.admin, _=w.admin)
        await db.commit()
    async with w.factory() as db:
        # Re-saving the same values is not a change.
        await r.put_retention_policy(w.project.id, RetentionPolicyWrite(enabled=True, runs_days=120), db=db,
                                     current_user=w.admin, _=w.admin)
        await db.commit()
    await tasks._record_retention_purge(w.project.id, {"counts": {"postgres": {"runs": 3, "activity_rows": 9}}})

    rows = await _rows(w)
    assert [x.event_type for x in rows] == ["retention_policy.updated", "retention.purge_executed"]
    assert rows[0].summary.startswith("Retention policy changed: ")
    assert "runs days" in rows[0].summary
    assert rows[1].summary == "Retention purge removed 12 records"
    assert rows[1].actor_type == "system"


async def test_feature_flag_allow_list_toggles(world, monkeypatch):
    w = world
    from app.models.schemas import FeatureFlagCreate, FeatureFlagUpdate
    from app.routers import feature_flags as r

    monkeypatch.setattr(r.ff_service, "invalidate_flag_cache", _noop)
    async with w.factory() as db:
        await r.create_feature_flag(FeatureFlagCreate(key=w.flag_key, enabled_projects=[w.project.id,
                                                                                       uuid.uuid4()]),
                                    db=db, current_user=w.admin)
    async with w.factory() as db:
        await r.update_feature_flag(w.flag_key, FeatureFlagUpdate(description="no allow-list change"), db=db,
                                    current_user=w.admin)
    async with w.factory() as db:
        await r.update_feature_flag(w.flag_key, FeatureFlagUpdate(enabled_projects=[]), db=db, current_user=w.admin)

    rows = await _rows(w, "feature_flag.")
    assert [x.summary for x in rows] == [
        f"Feature flag {w.flag_key} was enabled for this project",
        f"Feature flag {w.flag_key} was disabled for this project",
    ]


async def test_project_reset_records_only_a_confirmed_attempt(world):
    w = world
    from fastapi import HTTPException

    from app.models.schemas import ProjectResetRequest
    from app.routers import projects as r

    async with w.factory() as db:
        with pytest.raises(HTTPException):
            await r.reset_project_data(w.project.id, ProjectResetRequest(mode="runs", confirmation_name="wrong"),
                                       db=db, current_user=w.admin)
    assert await _rows(w, "project.reset") == []
    async with w.factory() as db:
        await r.reset_project_data(w.project.id, ProjectResetRequest(mode="runs", confirmation_name=w.project.name),
                                   db=db, current_user=w.admin)

    rows = await _rows(w, "project.reset")
    assert [x.summary for x in rows] == ["Project data reset (runs) was issued"]


async def test_notification_pref_records_for_a_project_preference_only(world):
    w = world
    from app.models.schemas import NotificationPreferenceCreate
    from app.routers import notifications as r

    async with w.factory() as db:
        await r.upsert_preference(NotificationPreferenceCreate(channel="email", events=["run_failed"]),
                                  current_user=w.admin, db=db)
    assert await _rows(w) == []
    async with w.factory() as db:
        pref = await r.upsert_preference(NotificationPreferenceCreate(
            channel="email", project_id=w.project.id, events=["run_failed"], email_override="qa.lead@acme-shop.com",
        ), current_user=w.admin, db=db)
    async with w.factory() as db:
        await r.delete_preference(pref.id, current_user=w.admin, db=db)

    rows = await _rows(w, "notification_pref.")
    assert [x.summary for x in rows] == [
        "Notification preferences changed: email on, 1 event types",
        "Notification preferences changed: email removed",
    ]
    assert "qa.lead@acme-shop.com" not in str(rows[0].context)


async def test_github_gitlab_tested_and_github_disconnected(world, monkeypatch):
    w = world
    from app.models.postgres import GitHubIntegration
    from app.routers import github_integration as gh
    from app.routers import gitlab_integration as gl

    async def _gh_ok(*_a, **_k):
        return {"success": True, "message": "ok"}

    async def _gl_fail(*_a, **_k):
        return {"ok": False, "detail": "401"}

    monkeypatch.setattr(gh.svc, "test_connection", _gh_ok)
    monkeypatch.setattr(gl.svc, "test_connection", _gl_fail)
    monkeypatch.setattr(gh.svc, "upsert_integration", _noop)
    async with w.factory() as db:
        await gh.test_github_integration(w.project.id, db=db, current_user=w.admin, _=w.admin)
        await gl.test_gitlab_integration(w.project.id, db=db, current_user=w.admin, _=w.admin)
        db.add(GitHubIntegration(project_id=w.project.id, repo_owner="acme", repo_name="shop"))
        await db.commit()
    async with w.factory() as db:
        await gh.delete_github_integration(w.project.id, db=db, current_user=w.admin, _=w.admin)

    rows = await _rows(w, "integration.")
    assert [(x.event_type, x.summary) for x in rows] == [
        ("integration.tested", "GitHub connection was tested"),
        ("integration.tested", "GitLab connection was tested"),
        ("integration.disconnected", "GitHub was disconnected"),
    ]
    assert rows[0].context["ok"] is True and rows[1].context["ok"] is False
