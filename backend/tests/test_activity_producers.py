"""Behavioural tests for activity producers (epic ACT).

These prove that a row ACTUALLY LANDS for each instrumented action. That is a
different claim from "the producer calls record()", and the difference is the
whole point: an outcome-mode event recorded with no session is dropped and
counted, so a producer can be fully wired up and the feed still stay empty.
The static scan in ``test_activity_events.py`` cannot see call sites whose
event_type is computed, so those are covered here instead.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import Base, Project, ProjectActivityEvent, User, UserRole

_TABLES = [Project.__table__, User.__table__, ProjectActivityEvent.__table__]


class _AwareDateTime(SQLITE_DATETIME):
    """SQLite drops tzinfo; the dedup window compares aware datetimes."""

    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def project(session_factory):
    async with session_factory() as s:
        row = Project(id=uuid.uuid4(), name="Auth Service", slug="auth-service")
        s.add(row)
        await s.commit()
        return row


@pytest.fixture
def actor():
    return User(
        id=uuid.uuid4(),
        email="marcus@example.com",
        username="marcus",
        full_name="Marcus L",
        hashed_password="x",
        role=UserRole.QA_LEAD.value,
    )


async def _events(session_factory) -> list[ProjectActivityEvent]:
    async with session_factory() as s:
        result = await s.execute(select(ProjectActivityEvent))
        return list(result.scalars().all())


# ── Quarantine mirror ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action,expected_event",
    [
        ("create", "quarantine.requested"),
        ("approve", "quarantine.approved"),
        ("reject", "quarantine.rejected"),
        ("release", "quarantine.released"),
    ],
)
async def test_quarantine_mirror_writes_a_row_for_each_human_action(
    session_factory, project, actor, monkeypatch, action, expected_event
):
    """The gap this closes: flaky_quarantine_service writes its compliance row
    to ``settings_audit_log``, which has NO project column, so
    ``query_unified_audit`` drops it for every non-ADMIN caller. The QA lead who
    quarantined a test could see it; the engineer who owns the test could not.

    Parameterised over all four actions because each maps through
    ``_QUARANTINE_ACTIVITY_EVENTS`` — a mapping the static call-site scan cannot
    read.
    """
    import app.db.postgres as pg
    from app.services.flaky_quarantine_service import _record_quarantine_activity

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)

    await _record_quarantine_activity(
        actor,
        action=action,
        request_id=uuid.uuid4(),
        project_id=project.id,
        after={"test_name": "test_login_mfa", "suite_name": "auth"},
    )

    rows = await _events(session_factory)
    assert len(rows) == 1, f"{action} produced no ledger row"
    assert rows[0].event_type == expected_event
    assert rows[0].project_id == project.id
    assert "test_login_mfa" in rows[0].summary
    # The compliance row it mirrors is still the record of authority.
    assert rows[0].source_table == "settings_audit_log"


@pytest.mark.parametrize("action", ["expire", "refresh_proposal"])
async def test_quarantine_bookkeeping_actions_produce_no_feed_row(
    session_factory, project, actor, monkeypatch, action
):
    """`expire` and `refresh_proposal` are the system keeping its own books,
    not things a person did. Recording them at the same rank as an approval is
    how a feed becomes unreadable."""
    import app.db.postgres as pg
    from app.services.flaky_quarantine_service import _record_quarantine_activity

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)

    await _record_quarantine_activity(
        actor,
        action=action,
        request_id=uuid.uuid4(),
        project_id=project.id,
        after={"test_name": "test_login_mfa"},
    )

    assert await _events(session_factory) == []


async def test_quarantine_mirror_never_raises_into_its_caller(
    session_factory, project, actor, monkeypatch
):
    """A quarantine decision must not fail because the ledger did."""
    import app.db.postgres as pg
    from app.services import flaky_quarantine_service as svc

    def _explode():
        raise RuntimeError("db gone")

    monkeypatch.setattr(pg, "AsyncSessionLocal", _explode, raising=False)

    await svc._record_quarantine_activity(
        actor,
        action="approve",
        request_id=uuid.uuid4(),
        project_id=project.id,
        after={"test_name": "t"},
    )


# ── Run lifecycle ────────────────────────────────────────────────────────────


async def test_run_received_is_attempt_mode_so_it_survives_a_failed_ingest(
    session_factory, project, actor, monkeypatch
):
    """"Did my run land?" must be answerable even when ingest then fails.

    This is why run.received is attempt-mode: an outcome row would be erased by
    the very rollback that made the question worth asking.
    """
    import app.db.postgres as pg
    from app.services.activity.service import ActorRef, record

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)

    async with session_factory() as caller_db:
        await record(
            None,
            project_id=project.id,
            event_type="run.received",
            actor=ActorRef.from_user(actor),
            entity_id=str(uuid.uuid4()),
            entity_label="Build 4312",
            context={"source_format": "junit"},
        )
        await caller_db.rollback()

    rows = await _events(session_factory)
    assert len(rows) == 1
    assert rows[0].event_type == "run.received"
    assert rows[0].category == "runs"


async def test_run_completed_carries_the_counts_the_feed_renders(
    session_factory, project
):
    from app.services.activity.service import ActorRef, record

    run_id = uuid.uuid4()
    async with session_factory() as db:
        await record(
            db,
            project_id=project.id,
            event_type="run.completed",
            actor=ActorRef.system("ingestion"),
            entity_id=run_id,
            entity_label="Build 4312",
            context={"total": 812, "passed": 809, "failed": 3, "skipped": 0},
            group_key=f"run:{run_id}:completed",
        )
        await db.commit()

    rows = await _events(session_factory)
    assert rows[0].summary == "Build 4312 completed — 3 failed of 812"
    assert rows[0].actor_type == "system"
    assert rows[0].actor_ref == "ingestion"


# ── Export ───────────────────────────────────────────────────────────────────


async def test_export_writes_no_row_into_the_activity_ledger(session_factory, project):
    """Export is recorded in ``access_audit_logs``, never in the ledger.

    Writing it here made a GET mutate what it reads. Exporting a project with
    no activity left it holding exactly one event - the record of exporting
    nothing - so ``ledger_started_at`` went non-null and the empty state
    claimed a history the project never had. Two identical back-to-back
    exports also returned different row counts, the second having counted the
    first.

    The event type is gone from the registry, so an attempt to emit it now
    raises rather than quietly reintroducing the loop.
    """
    from app.services.activity.events import ACTIVITY_EVENTS
    from app.services.activity.service import ActorRef, UnknownActivityEvent, record

    assert "activity.exported" not in ACTIVITY_EVENTS

    async with session_factory() as db:
        with pytest.raises(UnknownActivityEvent):
            await record(
                db,
                project_id=project.id,
                event_type="activity.exported",
                actor=ActorRef.system("export"),
                entity_id=str(project.id),
                strict=True,
            )


# ── API keys ─────────────────────────────────────────────────────────────────


async def test_api_key_event_stores_no_secret_material(session_factory, project, actor):
    """The raw key is shown to the caller exactly once. It must not be
    reconstructable from the feed, so the event carries field NAMES and the
    non-secret hint, never the key or its hash."""
    from app.services.activity.service import ActorRef, record

    async with session_factory() as db:
        await record(
            db,
            project_id=project.id,
            event_type="api_key.created",
            actor=ActorRef.from_user(actor),
            entity_id=uuid.uuid4(),
            entity_label="ci-token",
            changed_fields=["name", "scopes", "expires_at", "project_id"],
            context={"key_hint": "tl_...9f2c", "scopes": ["ingest"]},
        )
        await db.commit()

    rows = await _events(session_factory)
    blob = f"{rows[0].summary}|{rows[0].context}|{rows[0].diff}"
    assert "key_hash" not in blob
    assert rows[0].diff == {
        "changed_fields": ["name", "scopes", "expires_at", "project_id"]
    }
    assert "before" not in (rows[0].diff or {})


def test_a_user_scoped_api_key_writes_no_row():
    """A key bound to no project has nowhere honest to be filed. Recording it
    against an arbitrary project would be worse than the gap.

    Asserted on the source because the guard is a branch around the call, and
    a behavioural test would need a full request to reach it.
    """
    import inspect

    from app.routers import api_keys

    for fn in (api_keys.create_api_key, api_keys.revoke_api_key):
        src = inspect.getsource(fn)
        assert "if api_key.project_id is not None:" in src, (
            f"{fn.__name__} records activity without checking the key is "
            "project-scoped — a user-scoped key has no project to file under"
        )


# ── Release lifecycle ────────────────────────────────────────────────────────


async def test_release_created_shares_the_create_transaction(session_factory, project, actor):
    """A "release created" row for a create that rolled back would be a lie
    the feed could not walk back, so this one is outcome-mode."""
    from app.services.activity.service import ActorRef, record

    release_id = uuid.uuid4()
    async with session_factory() as db:
        await record(
            db,
            project_id=project.id,
            event_type="release.created",
            actor=ActorRef.from_user(actor),
            entity_id=release_id,
            entity_label="2.14.0",
            context={"version": "2.14.0"},
        )
        await db.rollback()

    assert await _events(session_factory) == []


async def test_release_updated_names_the_fields_that_changed(session_factory, project, actor):
    """A release's name, dates and status are what a gate decision is read
    against later. "Changed" without WHICH fields cannot answer that."""
    from app.services.activity.service import ActorRef, record

    async with session_factory() as db:
        await record(
            db,
            project_id=project.id,
            event_type="release.updated",
            actor=ActorRef.from_user(actor),
            entity_id=uuid.uuid4(),
            entity_label="2.14.0",
            changed_fields=["cutoff_end_at", "status"],
            context={"changed": "cutoff_end_at, status"},
        )
        await db.commit()

    rows = await _events(session_factory)
    assert rows[0].summary == "Release 2.14.0 changed: cutoff_end_at, status"
    assert rows[0].diff == {"changed_fields": ["cutoff_end_at", "status"]}


async def test_release_deleted_survives_the_delete_and_keeps_the_name(
    session_factory, project, actor, monkeypatch
):
    """Attempt-mode, and deliberately carrying NO release_id.

    The FK is ON DELETE SET NULL, so a release_id set here would be nulled
    moments later. The entity_label snapshot is the only thing that survives,
    which is why the producer reads the release BEFORE deleting it — after the
    delete the row could only say that "something" was removed.
    """
    import app.db.postgres as pg
    from app.services.activity.service import ActorRef, record

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)

    async with session_factory() as caller_db:
        await record(
            None,
            project_id=project.id,
            event_type="release.deleted",
            actor=ActorRef.from_user(actor),
            entity_id=str(uuid.uuid4()),
            entity_label="2.13.0",
        )
        await caller_db.rollback()

    rows = await _events(session_factory)
    assert len(rows) == 1
    assert rows[0].entity_label == "2.13.0"
    assert rows[0].release_id is None
    assert "2.13.0" in rows[0].summary


@pytest.mark.parametrize(
    "event_type",
    [
        "release.activated",
        "release.closed",
        "release.linked_run",
        "release.unlinked_run",
        "release.phase_added",
        "release.phase_updated",
        "release.phase_deleted",
        "release.synced_external",
    ],
)
async def test_every_release_event_renders_a_usable_sentence(
    session_factory, project, actor, event_type
):
    """Guards the class of bug where a template references a context key no
    producer supplies, so the feed shows a row of em dashes."""
    from app.services.activity.service import ActorRef, record

    async with session_factory() as db:
        await record(
            db,
            project_id=project.id,
            event_type=event_type,
            actor=ActorRef.from_user(actor),
            entity_id=uuid.uuid4(),
            entity_label="2.14.0",
            context={"phase": "soak", "provider": "GitHub", "changed": "name"},
        )
        await db.commit()

    rows = await _events(session_factory)
    assert len(rows) == 1
    assert rows[0].category == "release"
    # The label must actually appear — a summary that renders only dashes is
    # indistinguishable from a broken producer.
    assert "2.14.0" in rows[0].summary, rows[0].summary
    assert rows[0].summary.count("—") == 0, f"unfilled placeholder: {rows[0].summary}"


# ── A no-op update is not a change ───────────────────────────────────────────


def test_update_guards_record_on_what_DIFFERS_not_on_what_was_sent():
    """`if updates:` is true whenever a field was PRESENT in the request body.

    A PUT that re-sent the current value therefore recorded a `*.updated`
    event whose own diff said `changed_fields: []` while the summary named the
    field — the feed reporting an edit nobody made. Worse, the comment above
    the guard claimed the opposite of what the code did.

    Asserted on source across all three routers that share the idiom, because
    exercising it needs a live row per router and the property is textual: the
    guard must be computed from a before/after comparison.
    """
    import inspect

    from app.routers import projects, release_attribution_rules, releases

    for mod, fn_name in (
        (projects, "update_project"),
        (release_attribution_rules, "update_attribution_rule"),
        (releases, "update_release"),
    ):
        raw = inspect.getsource(getattr(mod, fn_name))
        # Strip comments: the explanation above each guard NAMES the old
        # pattern, and a substring check over raw source matches its own
        # documentation. That is a test failing on prose.
        src = " ".join(
            line for line in raw.splitlines() if not line.strip().startswith("#")
        )
        assert "if changed:" in src, (
            f"{mod.__name__}.{fn_name} guards on the submitted fields, not on "
            "the ones that actually differ"
        )
        assert "if updates:" not in src, (
            f"{mod.__name__}.{fn_name} still guards on `updates` (fields sent)"
        )


def test_release_update_snapshots_before_the_service_mutates():
    """The trap in the fix itself.

    ``release_service.update_release`` applies the change and returns the
    mutated row. Comparing after that call finds every field equal, so the
    guard would record NOTHING — a worse bug than the no-op events it replaced.
    The snapshot must be taken first.
    """
    import inspect

    from app.routers import releases

    src = inspect.getsource(releases.update_release)
    snapshot_at = src.index("get_release_or_404")
    mutate_at = src.index("release_service.update_release(")
    assert snapshot_at < mutate_at, (
        "the before-snapshot must be read BEFORE update_release mutates the row"
    )
