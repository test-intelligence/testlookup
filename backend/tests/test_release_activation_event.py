"""W05 REL-04: activating a release puts a ``release.activated`` row in the ledger.

``POST /releases/{id}/activate`` wrote ``access_audit_logs`` only. The release
page's "Recent activity" rail and ``/activity`` read the activity ledger, where
``release.activated`` was registered and emitted by nobody, so the one act that
moves where unattributed runs land never showed up in either.

Pinned here:

* a real activation stages EXACTLY ONE ledger row, with the before/after
  release NAMES (never ids) and ``changed_fields == ["active_release"]``;
* the first activation of a project names no predecessor (``None``);
* the idempotent no-op and the refused finished release stage nothing;
* the row is ``outcome`` mode: on the request session before its single commit,
  and no second session is opened.

The access-audit row is pinned, unchanged, by
``tests/regression/test_activate_release_endpoint.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.models.postgres import AccessAuditLog, ProjectActivityEvent  # noqa: E402
from app.routers import releases as router_mod  # noqa: E402

PROJECT = uuid.uuid4()


class _Rel:
    def __init__(self, *, name, status="in_progress", is_active=False):
        self.id = uuid.uuid4()
        self.project_id = PROJECT
        self.name = name
        self.status = status
        self.is_active = is_active


class _Session:
    def __init__(self):
        self.added: list = []
        self.commits = 0
        self.ledger_rows_at_commit: list[int] = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        self.ledger_rows_at_commit.append(len(_ledger(self)))


def _ledger(db):
    return [row for row in db.added if isinstance(row, ProjectActivityEvent)]


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="anand", full_name="Anand Goud")


@pytest.fixture
def no_second_session(monkeypatch):
    opened: list[str] = []

    def _opened(*_a, **_kw):
        opened.append("AsyncSessionLocal")
        raise AssertionError("a second database session was opened inside the request")

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _opened, raising=False)
    return opened


def _wire(monkeypatch, release, previous):
    async def _get(db, release_id):
        return release

    async def _activate(db, rel, *, reason="manual"):
        rel.is_active = True
        if previous is not None:
            previous.is_active = False
        return previous

    monkeypatch.setattr(router_mod.release_service, "get_release_or_404", _get)
    monkeypatch.setattr(router_mod.release_lifecycle_service, "activate_release", _activate)


async def _activate_endpoint(db, release, actor):
    return await router_mod.activate_release_endpoint(
        release_id=str(release.id), db=db, current_user=actor, __=None
    )


@pytest.mark.asyncio
async def test_an_activation_stages_one_ledger_row_with_the_names(monkeypatch, no_second_session):
    target, incumbent = _Rel(name="2.4.0"), _Rel(name="2.3.0", is_active=True)
    _wire(monkeypatch, target, incumbent)
    db, actor = _Session(), _actor()

    out = await _activate_endpoint(db, target, actor)

    assert out["changed"] is True
    (event,) = _ledger(db)
    assert event.event_type == "release.activated"
    assert event.summary == "Release 2.4.0 was activated"
    assert event.category == "release"
    assert (event.project_id, event.release_id, event.entity_id) == (PROJECT, target.id, str(target.id))
    assert event.entity_label == "2.4.0"
    assert event.diff == {
        "before": {"active_release": "2.3.0"},
        "after": {"active_release": "2.4.0"},
        "changed_fields": ["active_release"],
    }
    assert str(incumbent.id) not in str(event.diff), "the ledger diff carries names, never ids"
    assert (event.actor_type, event.actor_id, event.actor_name) == ("user", actor.id, "Anand Goud")
    # Outcome mode: on the request session before its ONE commit, nothing else opened.
    assert db.ledger_rows_at_commit == [1]
    assert no_second_session == []
    # The auditor's row is still there beside it.
    assert len([r for r in db.added if isinstance(r, AccessAuditLog)]) == 1


@pytest.mark.asyncio
async def test_the_first_activation_names_no_predecessor(monkeypatch):
    target = _Rel(name="2.4.0", status="planning")
    _wire(monkeypatch, target, None)
    db = _Session()

    await _activate_endpoint(db, target, _actor())

    (event,) = _ledger(db)
    assert event.diff["before"] == {"active_release": None}
    assert event.diff["after"] == {"active_release": "2.4.0"}


@pytest.mark.asyncio
async def test_an_api_key_activation_is_attributed_to_the_key(monkeypatch):
    """CI is the busiest writer: the feed must say a token did it, not a person."""
    from app.core.deps import CREDENTIAL_KIND_API_KEY, _bind_credential_kind

    target = _Rel(name="2.4.0")
    _wire(monkeypatch, target, None)
    db = _Session()

    await _activate_endpoint(db, target, _bind_credential_kind(_actor(), CREDENTIAL_KIND_API_KEY))

    (event,) = _ledger(db)
    assert event.actor_type == "api_key"


@pytest.mark.asyncio
async def test_the_idempotent_no_op_stages_nothing(monkeypatch):
    already = _Rel(name="2.4.0", is_active=True)

    async def _must_not_swap(*_a, **_kw):
        raise AssertionError("the no-op reached the swap")

    monkeypatch.setattr(router_mod.release_service, "get_release_or_404", lambda db, rid: _async(already))
    monkeypatch.setattr(router_mod.release_lifecycle_service, "activate_release", _must_not_swap)
    db = _Session()

    out = await _activate_endpoint(db, already, _actor())

    assert out["changed"] is False
    assert db.added == [], "re-activating the active release must record nothing"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_finished_release_is_refused_and_stages_nothing(monkeypatch):
    shipped = _Rel(name="2.2.0", status="released")
    monkeypatch.setattr(router_mod.release_service, "get_release_or_404", lambda db, rid: _async(shipped))
    db = _Session()

    with pytest.raises(HTTPException) as exc:
        await _activate_endpoint(db, shipped, _actor())

    assert exc.value.status_code == 409
    assert db.added == [] and db.commits == 0


async def _async(value):
    return value


# ── Against a real PostgreSQL: the real swap, the persisted row ──────────────


def _dsn() -> str:
    import os

    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_db():
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                factory = async_sessionmaker(
                    bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
                )
                async with factory() as session:
                    yield session
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_real_activation_persists_one_row_and_a_repeat_adds_none(pg_db, no_second_session):
    from sqlalchemy import select

    from app.models.postgres import Project, Release, User, UserRole

    db = pg_db
    token = uuid.uuid4().hex[:10]
    lead = User(id=uuid.uuid4(), email=f"lead-{token}@example.test", username=f"lead-{token}",
                full_name=f"Lead {token}", hashed_password="x", role=UserRole.QA_LEAD)
    project = Project(id=uuid.uuid4(), name=f"P {token}", slug=f"p-{token}")
    db.add_all([lead, project])
    await db.flush()
    incumbent = Release(id=uuid.uuid4(), project_id=project.id, name="2.3.0",
                        status="in_progress", is_active=True)
    target = Release(id=uuid.uuid4(), project_id=project.id, name="2.4.0", status="planning")
    db.add_all([incumbent, target])
    await db.commit()
    target_id, incumbent_id, lead_id = target.id, incumbent.id, lead.id

    async def _events():
        return list((await db.execute(
            select(ProjectActivityEvent).where(
                ProjectActivityEvent.release_id == target_id,
                ProjectActivityEvent.event_type == "release.activated",
            )
        )).scalars().all())

    first = await router_mod.activate_release_endpoint(
        release_id=str(target_id), db=db, current_user=lead, __=None
    )
    assert first["changed"] is True and first["deactivated"] == str(incumbent_id)
    (row,) = await _events()
    assert row.diff["before"] == {"active_release": "2.3.0"}
    assert row.diff["after"] == {"active_release": "2.4.0"}
    assert row.summary == "Release 2.4.0 was activated"
    assert row.actor_id == lead_id

    again = await router_mod.activate_release_endpoint(
        release_id=str(target_id), db=db, current_user=lead, __=None
    )
    assert again["changed"] is False
    assert len(await _events()) == 1, "the idempotent repeat recorded a second activation"
    assert no_second_session == []
