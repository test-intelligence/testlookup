"""W22b ACT-06 — per-field inheritance, the due-project selector and the D12 enable, against a real
PostgreSQL at 0242 (contract §2.2 W-1 … W-4, W-6, §6.3 G-C).

What a statement-routing fake cannot show:

1. **The resolver on real NULLs.** Three projects (explicit 90 / a row with a NULL ``runs_days`` / no row) under
   an instance ``runs_days = 180`` resolve 90 / 180 / 180 (sources project / instance / instance); with the
   instance value cleared the inheriting two read 365 (``default``); a row with EVERY column explicit keeps every
   value (0242 only relaxed the column definitions — G-A's migration test proves the upgrade itself).
2. **The selector.** At 03:00 UTC with an instance window of 3: windows 2 / 3 / NULL (inherits 3) / 3-but-paused /
   3-but-disabled -> exactly the explicit-3 and the inheriting project are due.
3. **The six readers.** ``list_projects``' ``retention_status`` (no row: instance off -> ``unconfigured``, on ->
   ``enabled``; explicit false -> ``disabled``) and the deleted-project report's ``reachable_by_retention`` follow
   the instance default.
4. **D12.** The preview lists no-row and NULL-``enabled`` projects and never an explicit ``false``; it writes
   nothing; the typed enable flips exactly the previewed projects; a project created after the preview makes the
   hash stale (409).

The instance row is shared by the whole database: every test snapshots it and restores it EXACTLY in a
``finally``; every project is created here and deleted at the end. Nothing binds Redis or Mongo (the ADMIN
project list needs no membership cache; the footprint is stubbed), so the fixture closes neither. It awaits
``dispose_engine_for_loop()`` before building its engine and after disposing it. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` at head (0242). To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml`` (contract §6.4).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_INSTANCE_COLUMNS = (
    "enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs",
    "purge_window_start_utc", "enabled_at", "enabled_by_id", "updated_by_user_id", "updated_at",
)


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(loop_scope="module")
async def sessions():
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


@pytest_asyncio.fixture(loop_scope="module")
async def world(sessions):
    """Snapshot the shared instance row; restore it exactly and delete every project made here."""
    columns = ", ".join(_INSTANCE_COLUMNS)
    async with sessions() as db:
        before = (await db.execute(text(f"SELECT {columns} FROM instance_retention_policy WHERE id = 1"))).first()
    assert before is not None, "0242 inserts the instance row"
    made: list[uuid.UUID] = []
    try:
        yield SimpleNamespace(sessions=sessions, made=made)
    finally:
        async with sessions() as db:
            assignments = ", ".join(f"{c} = :{c}" for c in _INSTANCE_COLUMNS)
            await db.execute(
                text(f"UPDATE instance_retention_policy SET {assignments} WHERE id = 1"),
                dict(zip(_INSTANCE_COLUMNS, before)),
            )
            if made:
                await db.execute(text("DELETE FROM project_retention_policies WHERE project_id = ANY(:i)"), {"i": made})
                await db.execute(text("DELETE FROM projects WHERE id = ANY(:i)"), {"i": made})
            await db.commit()


async def _set_instance(w, **values) -> None:
    """Every inheritable instance column set (NULL unless given), ``enabled`` false unless given."""
    row = {c: None for c in _INSTANCE_COLUMNS[1:7]}
    row["enabled"] = False
    row.update(values)
    assignments = ", ".join(f"{c} = :{c}" for c in row)
    async with w.sessions() as db:
        await db.execute(text(f"UPDATE instance_retention_policy SET {assignments} WHERE id = 1"), row)
        await db.commit()


async def _project(w, label: str, *, active: bool = True, policy: dict | None = None) -> uuid.UUID:
    """A project named so it sorts first (``000-…``); ``policy`` = the row's explicit columns (None = no row)."""
    from app.models.postgres import Project, ProjectRetentionPolicy

    pid = uuid.uuid4()
    async with w.sessions() as db:
        db.add(Project(id=pid, name=f"000-w22b-gc-{label}-{pid.hex[:8]}", slug=f"w22b-gc-{label}-{pid.hex[:8]}",
                       is_active=active))
        await db.flush()
        if policy is not None:
            db.add(ProjectRetentionPolicy(project_id=pid, **policy))
        await db.commit()
    w.made.append(pid)
    return pid


async def _effective(w, pid):
    from app.services import retention_service

    async with w.sessions() as db:
        return await retention_service.get_effective_policy(db, pid)


# ── 1. The resolver on real NULLs ────────────────────────────────────────────


async def test_a_project_override_wins_and_a_null_inherits_field_by_field(world):
    explicit = await _project(world, "explicit", policy={"runs_days": 90})
    null_row = await _project(world, "nullrow", policy={"keep_last_runs": 4})
    no_row = await _project(world, "norow")

    await _set_instance(world, runs_days=180)
    got = {pid: await _effective(world, pid) for pid in (explicit, null_row, no_row)}
    assert [got[p].runs_days for p in (explicit, null_row, no_row)] == [90, 180, 180]
    assert [got[p].sources["runs_days"] for p in (explicit, null_row, no_row)] == ["project", "instance", "instance"]
    assert got[null_row].keep_last_runs == 4 and got[null_row].sources["keep_last_runs"] == "project"
    assert [got[p].source for p in (explicit, null_row, no_row)] == ["custom", "custom", "default"]

    await _set_instance(world)          # the instance value cleared: the code default
    for pid in (null_row, no_row):
        eff = await _effective(world, pid)
        assert (eff.runs_days, eff.sources["runs_days"]) == (365, "default")
    assert (await _effective(world, explicit)).runs_days == 90


async def test_an_existing_explicit_row_keeps_every_value(world):
    explicit = {"enabled": False, "raw_events_days": 120, "runs_days": 400, "artifacts_days": 200,
                "audit_days": 800, "keep_last_runs": 25, "purge_window_start_utc": 6}
    pid = await _project(world, "allexplicit", policy=explicit)
    await _set_instance(world, raw_events_days=30, runs_days=60, artifacts_days=30, audit_days=900,
                        keep_last_runs=1, purge_window_start_utc=9)
    eff = await _effective(world, pid)
    for field, value in explicit.items():
        assert getattr(eff, field) == value, field
        assert eff.sources[field] == "project", field


# ── 2. The selector ──────────────────────────────────────────────────────────


async def test_due_project_ids_at_three_takes_the_window_the_inheritance_and_the_pause(world):
    from app.services import retention_service

    now = datetime(2026, 10, 28, 3, 0, 4, tzinfo=timezone.utc)
    await _set_instance(world, purge_window_start_utc=3)
    at_two = await _project(world, "w2", policy={"enabled": True, "purge_window_start_utc": 2})
    at_three = await _project(world, "w3", policy={"enabled": True, "purge_window_start_utc": 3})
    inheriting = await _project(world, "winherit", policy={"enabled": True})
    paused = await _project(world, "wpaused", policy={"enabled": True, "purge_window_start_utc": 3,
                                                     "purge_paused_until": now + timedelta(days=1)})
    pause_over = await _project(world, "wpauseover", policy={"enabled": True, "purge_window_start_utc": 3,
                                                            "purge_paused_until": now - timedelta(minutes=1)})
    disabled = await _project(world, "woff", policy={"enabled": False, "purge_window_start_utc": 3})
    no_row = await _project(world, "wnorow")   # the instance default is OFF: never due

    async with world.sessions() as db:
        due = set(await retention_service.due_project_ids(db, now=now))
    mine = {at_two, at_three, inheriting, paused, pause_over, disabled, no_row}
    assert due & mine == {at_three, inheriting, pause_over}

    async with world.sessions() as db:
        due_at_two = set(await retention_service.due_project_ids(db, now=now - timedelta(hours=1)))
    assert due_at_two & mine == {at_two}


# ── 3. The readers follow the instance default ───────────────────────────────


async def _statuses(world, ids) -> dict:
    from app.routers.projects import list_projects

    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True)
    async with world.sessions() as db:
        listed = await list_projects(db=db, current_user=admin, include_archived=True)
    return {p.id: p.retention_status for p in listed if p.id in ids}


async def _reachable(world, monkeypatch, ids) -> dict:
    from app.services import storage_accounting_service as accounting

    async def _footprint(db, project_id, **_kw):
        return accounting.ProjectStorageFootprint(project_id=str(project_id), computed_at=datetime.now(timezone.utc))

    monkeypatch.setattr(accounting, "project_storage_footprint", _footprint)
    async with world.sessions() as db:
        report = await accounting.deleted_project_footprints(db, limit=200, mongo=object(), storage=object())
    return {uuid.UUID(p.project_id): p.reachable_by_retention for p in report.projects if uuid.UUID(p.project_id) in ids}


async def test_the_project_list_and_the_deleted_report_follow_the_instance(world, monkeypatch):
    no_row = await _project(world, "lnorow")
    explicit_off = await _project(world, "loff", policy={"enabled": False})
    null_enabled = await _project(world, "lnull", policy={"runs_days": 400})
    gone_no_row = await _project(world, "lgone", active=False)
    gone_off = await _project(world, "lgoneoff", active=False, policy={"enabled": False})
    listed = {no_row, explicit_off, null_enabled}
    deleted = {gone_no_row, gone_off}

    await _set_instance(world, enabled=False)
    assert await _statuses(world, listed) == {
        no_row: "unconfigured", explicit_off: "disabled", null_enabled: "disabled",
    }
    assert await _reachable(world, monkeypatch, deleted) == {gone_no_row: False, gone_off: False}

    await _set_instance(world, enabled=True)
    assert await _statuses(world, listed) == {
        no_row: "enabled", explicit_off: "disabled", null_enabled: "enabled",
    }
    assert await _reachable(world, monkeypatch, deleted) == {gone_no_row: True, gone_off: False}


# ── 4. D12: the preview and the typed enable ─────────────────────────────────


async def _audit_rows(world) -> int:
    async with world.sessions() as db:
        return int((await db.execute(text(
            "SELECT count(*) FROM settings_audit_log WHERE setting_key = 'retention_instance_policy'"
        ))).scalar_one())


async def test_the_enable_flips_only_the_previewed_projects(world):
    from app.services import retention_instance_service as inst_svc

    await _set_instance(world, enabled=False, runs_days=180)
    no_row = await _project(world, "enorow")
    null_enabled = await _project(world, "enull", policy={"keep_last_runs": 3})
    explicit_off = await _project(world, "eoff", policy={"enabled": False})
    explicit_on = await _project(world, "eon", policy={"enabled": True})
    now = datetime.now(timezone.utc)

    audits_before = await _audit_rows(world)
    async with world.sessions() as db:
        preview = await inst_svc.instance_enable_preview(db, now=now)
    assert await _audit_rows(world) == audits_before, "the preview writes nothing"
    listed = {item["project_id"] for item in preview["items"]}
    assert {no_row, null_enabled} <= listed
    assert explicit_off not in listed and explicit_on not in listed
    item = next(i for i in preview["items"] if i["project_id"] == null_enabled)
    assert item["has_policy_row"] is True and item["effective_after"]["runs_days"] == 180
    assert item["effective_after"]["keep_last_runs"] == 3

    actor = SimpleNamespace(id=None, username="w22b-gc-admin", full_name=None)
    async with world.sessions() as db:
        await inst_svc.enable_instance_policy(
            db, actor=actor, confirmation=preview["confirmation_phrase"], preview_hash=preview["preview_hash"], now=now,
        )
        await db.commit()
    assert await _audit_rows(world) == audits_before + 1

    effective = {pid: await _effective(world, pid) for pid in (no_row, null_enabled, explicit_off, explicit_on)}
    assert {pid: e.enabled for pid, e in effective.items()} == {
        no_row: True, null_enabled: True, explicit_off: False, explicit_on: True,
    }
    assert effective[no_row].sources["enabled"] == "instance"
    assert effective[explicit_off].sources["enabled"] == "project", "an explicit false is never overridden"


async def test_a_project_created_after_the_preview_makes_it_stale(world):
    from app.services import retention_instance_service as inst_svc
    from app.services.retention_instance_service import InstancePolicyError

    await _set_instance(world, enabled=False)
    await _project(world, "snorow")
    now = datetime.now(timezone.utc)
    async with world.sessions() as db:
        preview = await inst_svc.instance_enable_preview(db, now=now)
    await _project(world, "slate")
    actor = SimpleNamespace(id=None, username="w22b-gc-admin", full_name=None)
    async with world.sessions() as db:
        with pytest.raises(InstancePolicyError) as err:
            await inst_svc.enable_instance_policy(
                db, actor=actor, confirmation=preview["confirmation_phrase"],
                preview_hash=preview["preview_hash"], now=now,
            )
        await db.rollback()
    assert (err.value.code, err.value.status_code) == ("preview_stale", 409)
    async with world.sessions() as db:
        assert (await db.execute(text("SELECT enabled FROM instance_retention_policy WHERE id = 1"))).scalar_one() is False


async def test_the_runs_past_window_count_uses_each_projects_own_window(world):
    from app.models.postgres import TestRun
    from app.services import retention_instance_service as inst_svc

    await _set_instance(world, enabled=False, runs_days=60)
    short = await _project(world, "rshort")
    long = await _project(world, "rlong", policy={"runs_days": 400})
    now = datetime.now(timezone.utc)
    async with world.sessions() as db:
        for pid in (short, long):
            for age in (10, 100, 500):
                run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"gc-{age}", jenkins_job="gc",
                              status="PASSED", ingestion_source="upload", branch="main")
                run.created_at = now - timedelta(days=age)
                db.add(run)
        await db.commit()
    try:
        async with world.sessions() as db:
            preview = await inst_svc.instance_enable_preview(db, now=now)
        past = {i["project_id"]: i["runs_past_window"] for i in preview["items"] if i["project_id"] in {short, long}}
        assert past == {short: 2, long: 1}
    finally:
        async with world.sessions() as db:
            await db.execute(text("DELETE FROM test_runs WHERE project_id = ANY(:i)"), {"i": [short, long]})
            await db.commit()
