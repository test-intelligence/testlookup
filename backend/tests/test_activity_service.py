"""Write-path tests for the activity ledger (epic ACT).

These cover the four invariants the write service exists to hold, and each one
is written so that removing the behaviour makes the test fail (mutation-checked
during development):

1. outcome events share the caller's transaction; attempt events survive it
2. secrets are redacted at WRITE time
3. actor type is resolved correctly for all five ways an action reaches us
4. a ledger failure never propagates into the caller's mutation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME

from app.models.postgres import (
    Base,
    Project,
    ProjectActivityEvent,
    User,
    UserRole,
)
from app.services.activity import service as activity_service
from app.services.activity.service import ActorRef, record

_TABLES = [
    Project.__table__,
    User.__table__,
    ProjectActivityEvent.__table__,
]


class _AwareDateTime(SQLITE_DATETIME):
    """SQLite drops tzinfo; the ledger compares aware datetimes.

    Without this the dedup window comparison raises
    "can't compare offset-naive and offset-aware datetimes" and every dedup
    test would fail for a reason that has nothing to do with dedup.
    """

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
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
async def project(db):
    row = Project(id=uuid.uuid4(), name="Auth Service", slug="auth-service")
    db.add(row)
    await db.commit()
    return row


async def _rows(session_factory) -> list[ProjectActivityEvent]:
    async with session_factory() as s:
        result = await s.execute(select(ProjectActivityEvent))
        return list(result.scalars().all())


# ── Durability contract ──────────────────────────────────────────────────────


async def test_outcome_event_dies_with_the_callers_rollback(db, project, session_factory):
    """"policy.updated" must not be recorded for an update that rolled back."""
    await record(
        db,
        project_id=project.id,
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="Marcus"),
        entity_id=uuid.uuid4(),
        entity_label="Strict gate",
        changed_fields=["threshold"],
    )
    await db.rollback()

    assert await _rows(session_factory) == []


async def test_outcome_event_lands_when_the_caller_commits(db, project, session_factory):
    await record(
        db,
        project_id=project.id,
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="Marcus"),
        entity_id=uuid.uuid4(),
        entity_label="Strict gate",
        changed_fields=["threshold"],
    )
    await db.commit()

    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].event_type == "policy.updated"
    assert rows[0].category == "configuration"
    assert rows[0].summary == "Release gate policy Strict gate changed: —"


async def test_attempt_event_survives_the_callers_rollback(
    db, project, session_factory, monkeypatch
):
    """A project reset that was ISSUED and then failed is exactly the half an
    operator needs. An outcome-mode write would erase it."""
    import app.db.postgres as pg

    monkeypatch.setattr(pg, "AsyncSessionLocal", session_factory, raising=False)

    await record(
        db,
        project_id=project.id,
        event_type="project.reset",
        actor=ActorRef("user", actor_name="Sam"),
        entity_id=project.id,
        entity_label="Auth Service",
        context={"mode": "full"},
    )
    await db.rollback()

    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].event_type == "project.reset"
    assert "full" in rows[0].summary


# ── Redaction at write time ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secret_key", ["token", "secret", "password", "client_secret", "api_key"]
)
async def test_secret_values_never_reach_the_table(
    db, project, session_factory, secret_key
):
    """The value must not be stored, in diff, context or summary.

    Redaction happens on the way IN precisely so that no future reader — an
    export, the CLI, an MCP tool, an endpoint nobody has written yet — can
    surface it.
    """
    poison = "hunter2-SUPER-SECRET"
    await record(
        db,
        project_id=project.id,
        event_type="integration.connected",
        actor=ActorRef("user", actor_name="Sam"),
        entity_id="github",
        entity_label="GitHub",
        context={"provider": "GitHub", secret_key: poison},
        before={},
        after={secret_key: poison},
    )
    await db.commit()

    rows = await _rows(session_factory)
    assert len(rows) == 1
    blob = f"{rows[0].summary}|{rows[0].context}|{rows[0].diff}"
    assert poison not in blob, f"{secret_key} value leaked into the ledger: {blob}"


async def test_changed_fields_only_contract_stores_no_values(
    db, project, session_factory
):
    """The contract for secret-bearing objects: names, never values."""
    await record(
        db,
        project_id=project.id,
        event_type="api_key.created",
        actor=ActorRef("user", actor_name="Sam"),
        entity_id=uuid.uuid4(),
        entity_label="ci-token",
        changed_fields=["key_hash", "scopes"],
    )
    await db.commit()

    rows = await _rows(session_factory)
    assert rows[0].diff == {"changed_fields": ["key_hash", "scopes"]}
    assert "before" not in (rows[0].diff or {})


# ── Actor resolution ─────────────────────────────────────────────────────────


def test_actor_from_user_detects_a_plain_human():
    user = User(
        id=uuid.uuid4(),
        email="p@example.com",
        username="priya",
        full_name="Priya R",
        hashed_password="x",
        role=UserRole.QA_ENGINEER.value,
    )
    actor = ActorRef.from_user(user)
    assert actor.actor_type == "user"
    assert actor.actor_name == "Priya R"


def test_actor_from_user_detects_a_service_account():
    user = User(
        id=uuid.uuid4(),
        email="mcp@example.com",
        username="mcp_service",
        hashed_password="x",
        role=UserRole.QA_ENGINEER.value,
        is_service_account=True,
    )
    assert ActorRef.from_user(user).actor_type == "service_account"


def test_actor_from_user_detects_an_api_key_principal():
    """An API key authenticates AS a user row, and CI is the busiest writer in
    the system. Without this the feed cannot tell "Priya clicked a button" from
    "Priya's CI token did".

    Marked through the REAL mechanism — ``core.deps._bind_credential_kind``,
    which the auth dependencies actually call — rather than by setting an
    attribute this test invents. An earlier version did the latter: it passed
    while nothing in the codebase populated the attribute, so every CI-driven
    event would have been attributed to a person.
    """
    from app.core.deps import CREDENTIAL_KIND_API_KEY, _bind_credential_kind

    user = User(
        id=uuid.uuid4(),
        email="p@example.com",
        username="priya",
        hashed_password="x",
        role=UserRole.QA_ENGINEER.value,
    )
    _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)

    actor = ActorRef.from_user(user)
    assert actor.actor_type == "api_key"


def test_a_jwt_authenticated_user_is_not_mistaken_for_a_key():
    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind

    user = User(
        id=uuid.uuid4(),
        email="p@example.com",
        username="priya",
        hashed_password="x",
        role=UserRole.QA_ENGINEER.value,
    )
    _bind_credential_kind(user, CREDENTIAL_KIND_JWT)
    assert ActorRef.from_user(user).actor_type == "user"


def test_the_ingest_path_really_stamps_the_credential_kind():
    """Guards the connection the previous test cannot see.

    ``from_user`` reading the marker is only useful if the auth dependency
    writes it. This asserts the API-key validator does — the missing half that
    made the original implementation dead code.
    """
    import inspect

    from app.core import deps

    src = inspect.getsource(deps._validate_api_key)
    assert "_bind_credential_kind" in src
    assert "CREDENTIAL_KIND_API_KEY" in src


def test_actor_system_and_agent_constructors():
    assert ActorRef.system("retention-scheduler").actor_type == "system"
    assert ActorRef.agent("Fixer").actor_type == "agent"
    assert ActorRef.agent("Fixer").actor_ref == "Fixer"


def test_actor_from_none_is_system_not_a_crash():
    assert ActorRef.from_user(None).actor_type == "system"


# ── Registry enforcement ─────────────────────────────────────────────────────


async def test_unregistered_event_raises_under_pytest(db, project):
    """Loud in CI. In production the same path drops and counts instead."""
    with pytest.raises(activity_service.UnknownActivityEvent):
        await record(
            db,
            project_id=project.id,
            event_type="totally.invented",
            actor=ActorRef("user", actor_name="x"),
            entity_id="1",
        )


async def test_unregistered_event_is_swallowed_when_not_strict(
    db, project, session_factory
):
    await record(
        db,
        project_id=project.id,
        event_type="totally.invented",
        actor=ActorRef("user", actor_name="x"),
        entity_id="1",
        strict=False,
    )
    await db.commit()
    assert await _rows(session_factory) == []


async def test_actor_type_not_allowed_for_event_is_rejected(db, project):
    """agent.fix_proposed is emittable only by an agent. A human 'proposing a
    fix' through that event would misattribute the governance record."""
    with pytest.raises(ValueError):
        await record(
            db,
            project_id=project.id,
            event_type="agent.fix_proposed",
            actor=ActorRef("user", actor_name="Priya"),
            entity_id="1",
        )


# ── Duplicate suppression ────────────────────────────────────────────────────


async def test_group_key_suppresses_a_repeat_inside_the_window(
    db, project, session_factory
):
    """Celery redelivers. A retried finalize_run must not write a second
    "run completed" for the same run."""
    run_id = uuid.uuid4()
    for _ in range(2):
        await record(
            db,
            project_id=project.id,
            event_type="run.completed",
            actor=ActorRef.system("ingestion"),
            entity_id=run_id,
            entity_label="Build #4312",
            context={"failed": 3, "total": 812},
            group_key=f"run:{run_id}:completed",
        )
        await db.commit()

    rows = await _rows(session_factory)
    assert len(rows) == 1


async def test_group_key_allows_a_repeat_outside_the_window(
    db, project, session_factory
):
    """The suppression is a window, not a unique constraint — a genuinely new
    occurrence an hour later is still an event."""
    run_id = uuid.uuid4()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    await record(
        db,
        project_id=project.id,
        event_type="run.completed",
        actor=ActorRef.system("ingestion"),
        entity_id=run_id,
        entity_label="Build #4312",
        group_key=f"run:{run_id}:completed",
        occurred_at=old,
    )
    await db.commit()
    await record(
        db,
        project_id=project.id,
        event_type="run.completed",
        actor=ActorRef.system("ingestion"),
        entity_id=run_id,
        entity_label="Build #4312",
        group_key=f"run:{run_id}:completed",
    )
    await db.commit()

    assert len(await _rows(session_factory)) == 2


# ── Failure isolation ────────────────────────────────────────────────────────


async def test_a_ledger_failure_never_propagates(db, project, monkeypatch):
    """The mutation must survive a broken ledger.

    Recording that a policy was updated must never be the reason a policy
    update 500s.
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("table is on fire")

    monkeypatch.setattr(activity_service, "_build_row", _boom)

    # strict=False is the production default; assert no exception escapes.
    await record(
        db,
        project_id=project.id,
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="Marcus"),
        entity_id=uuid.uuid4(),
        strict=False,
    )


async def test_a_malformed_project_id_is_dropped_not_raised(db):
    await record(
        db,
        project_id="not-a-uuid",
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="x"),
        entity_id="1",
        strict=False,
    )


async def test_live_session_slug_entity_id_is_accepted(db, project, session_factory):
    """entity_id is TEXT because live sessions address runs by slug. A UUID
    cast here would drop those events or 500 the ingest path."""
    await record(
        db,
        project_id=project.id,
        event_type="run.live_started",
        actor=ActorRef.system("stream"),
        entity_id="live-auth-service-4312",
        entity_label="Live build 4312",
    )
    await db.commit()

    rows = await _rows(session_factory)
    assert rows[0].entity_id == "live-auth-service-4312"


# ── Misuse vs infrastructure ─────────────────────────────────────────────────


async def test_an_unreachable_database_never_raises_even_under_pytest(
    db, project, monkeypatch
):
    """The contract, stated in this module's docstring, under the condition
    that actually breaks it.

    ``strict`` defaults to True under pytest so a typo'd event name fails CI.
    An earlier version let that cover WRITE failures too, which turned an
    unreachable database into a 500 from POST /ingest — and this repo's
    integration tests are deliberately hermetic: they override ``get_db`` with
    a fake but not ``AsyncSessionLocal``, so every attempt-mode write reaches a
    real socket that is not there. Ten of them failed in CI while the whole
    unit suite was green, because the unit suite skips tests/integration.

    Infrastructure failures are swallowed at every value of ``strict``.
    """
    import app.db.postgres as pg

    def _unreachable():
        raise OSError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(pg, "AsyncSessionLocal", _unreachable, raising=False)

    # attempt mode — its own session, the path that broke.
    await record(
        db,
        project_id=project.id,
        event_type="run.received",
        actor=ActorRef.system("ingestion"),
        entity_id=str(uuid.uuid4()),
        entity_label="Build 1",
        strict=True,
    )


async def test_an_outcome_write_failure_never_raises_either(db, project, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(activity_service, "_build_row", _boom)

    await record(
        db,
        project_id=project.id,
        event_type="policy.updated",
        actor=ActorRef("user", actor_name="Marcus"),
        entity_id=uuid.uuid4(),
        strict=True,
    )


async def test_misuse_still_raises_under_strict(db, project):
    """The other half. Infrastructure is forgiven; a typo is not — otherwise
    an unregistered event name would sail through CI unnoticed."""
    with pytest.raises(activity_service.UnknownActivityEvent):
        await record(
            db,
            project_id=project.id,
            event_type="not.registered",
            actor=ActorRef("user", actor_name="x"),
            entity_id="1",
            strict=True,
        )
