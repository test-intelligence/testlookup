"""W38 (W31 F-2): SCIM ``DELETE /Users/{id}`` in both modes (``routers/scim.py`` ``scim_delete`` →
``services/scim_service.scim_delete_user``; contract D-8..D-11, §3).

The real SCIM router on a bare FastAPI app over the SQLite world of ``test_subject_erasure_service.py``
(``get_db`` overridden with a commit counter; ``verify_scim_bearer`` overridden — never a real token — with a
namespace carrying ``sso_config_id`` and the token ``id``). Deactivate (the default) is today's soft delete, event for
event; erase starts the W31 erasure in ONE commit (job ``source="scim"``, no requester, the lock-out, the pending
request accepted, ``SCIM_USER_ERASURE_STARTED``) and dispatches AFTER the commit; a retry / an erased person is a
no-op; a refused erasure falls back to the deactivation + ``SCIM_ERASURE_REFUSED``; scope and 404 as today. Real
Postgres: ``tests/integration/test_scim_delete_erasure_postgres.py``.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.postgres import get_db
from app.models.postgres import (
    AccessAuditLog,
    AppSetting,
    Base,
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    SubjectErasureRequest,
    SubjectRightsJob,
    User,
    UserRole,
)
from app.routers import scim as scim_routes
from app.services import scim_delete_setting
from app.services import scim_service
from app.services import subject_erasure_service as erasure
from app.services.subject_rights_vocab import ERROR_SENTENCES
from tests.test_subject_erasure_service import _job, _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

USERS = "/api/v1/scim/v2/Users"
TOKEN_ID = uuid.UUID("5c1a0000-0000-4000-8000-00000000abcd")
CLIENT_IP = "127.0.0.1"   # httpx's ASGITransport client address


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The order of the side effects: the token revocation, each handler commit, the cache drop, the enqueue."""
    seen: list[Any] = []

    async def _revoke(user_id: uuid.UUID, db: Any = None) -> None:
        seen.append(("revoke", user_id, db is not None))

    async def _invalidate(user_id: uuid.UUID) -> None:
        seen.append(("membership", user_id))

    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _revoke)
    monkeypatch.setattr(scim_routes, "invalidate_membership_cache", _invalidate)
    monkeypatch.setattr(scim_routes, "_dispatch_erasure", lambda job_id: seen.append(("dispatch", str(job_id))))
    return seen


def _client(maker: Any, events: list[Any], token: Any = None) -> AsyncClient:
    app = FastAPI()
    app.include_router(scim_routes.router)

    async def _db() -> Any:
        async with maker.session() as session:
            original = session.commit

            async def _counted() -> None:
                events.append(("commit",))
                await original()

            session.commit = _counted  # type: ignore[method-assign]
            try:
                yield session
                await original()   # get_db's own end-of-request commit (not counted)
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[scim_routes.verify_scim_bearer] = (
        lambda: token if token is not None else SimpleNamespace(sso_config_id=None, id=TOKEN_ID)
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _delete(maker: Any, events: list[Any], user_id: uuid.UUID, token: Any = None) -> Any:
    async with _client(maker, events, token) as client:
        return await client.delete(f"{USERS}/{user_id}")


async def _set_mode(maker: Any, action: str) -> None:
    async with maker.session() as db:
        db.add(AppSetting(key=scim_delete_setting.APP_SETTINGS_KEY, value={"action": action}))
        await db.commit()


async def _rows(maker: Any, model: Any, *where: Any) -> list[Any]:
    async with maker.session() as db:
        return list((await db.execute(select(model).where(*where))).scalars().all())


async def _count(maker: Any, model: Any, *where: Any) -> int:
    async with maker.session() as db:
        return int((await db.execute(select(func.count()).select_from(model).where(*where))).scalar() or 0)


def _event_shape(event: IdentityEvent) -> tuple[Any, ...]:
    return (event.event_type, event.detail, event.actor_id, event.actor_name, event.sso_config_id,
            event.ip_address, event.success, event.error_message)


async def _events_of(maker: Any, user_id: uuid.UUID) -> list[IdentityEvent]:
    rows = await _rows(maker, IdentityEvent, IdentityEvent.user_id == user_id)
    return sorted(rows, key=lambda row: (row.created_at, str(row.event_type)))


async def _seed_sessions_and_keys(maker: Any, subject: User) -> None:
    async with maker.session() as db:
        await db.execute(Base.metadata.tables["refresh_token_records"].insert().values(
            id=uuid.uuid4(), user_id=subject.id, jti_hash="j" * 64,
            issued_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc)))
        await db.execute(Base.metadata.tables["api_keys"].insert().values(
            id=uuid.uuid4(), user_id=subject.id, name="ci", key_hash="h" * 20, key_hint="tl_zz", scopes=[],
            is_active=True))
        await db.commit()


async def _seed_request(maker: Any, subject: User) -> uuid.UUID:
    async with maker.session() as db:
        request = SubjectErasureRequest(id=uuid.uuid4(), subject_user_id=subject.id, status="pending",
                                        requested_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
        db.add(request)
        await db.commit()
        return request.id


def test_the_sqlite_world_holds_every_table_this_needs(maker: Any) -> None:
    assert {"app_settings", "identity_events", "access_audit_logs", "subject_erasure_requests",
            "federated_identities", "refresh_token_records", "api_keys"} <= maker.created


# ── deactivate (the default): today's soft delete, event for event ───────────

async def _legacy_delete(maker: Any, user_id: uuid.UUID) -> None:
    """The pre-W38 route body: ``scim_update_user(active=False)`` + the commit."""
    async with maker.session() as db:
        await scim_service.scim_update_user(db=db, user_id=user_id, active=False, sso_config_id=None,
                                            ip_address=CLIENT_IP)
        await db.commit()


@pytest.mark.parametrize("stored", [None, "deactivate", "obliterate"])   # no row / explicit / unreadable
async def test_deactivate_mode_is_todays_soft_delete(maker: Any, world: SimpleNamespace, events: list[Any],
                                                     stored: Optional[str]) -> None:
    subject, twin = world.subject, _user(UserRole.TESTER, "zqxwtwin")
    async with maker.session() as db:
        db.add(twin)
        await db.commit()
    if stored is not None:
        await _set_mode(maker, stored)
    await _seed_sessions_and_keys(maker, subject)
    await _legacy_delete(maker, twin.id)
    first = await _delete(maker, events, subject.id)
    assert first.status_code == 204 and first.content == b""
    assert events == [("commit",)]                       # no revocation, no cache drop, no dispatch
    await _legacy_delete(maker, twin.id)
    again = await _delete(maker, events, subject.id)     # a retry: SCIM_USER_UPDATED {"no_changes": true}
    assert again.status_code == 204 and events == [("commit",), ("commit",)]
    mine = [_event_shape(e) for e in await _events_of(maker, subject.id)]
    legacy = [_event_shape(e) for e in await _events_of(maker, twin.id)]
    assert mine == legacy == [
        (IdentityEventType.SCIM_USER_DEACTIVATED.value, {"deactivated": True}, None, None, None, CLIENT_IP, True, None),
        (IdentityEventType.SCIM_USER_UPDATED.value, {"no_changes": True}, None, None, None, CLIENT_IP, True, None),
    ]
    async with maker.session() as db:
        assert (await db.get(User, subject.id)).is_active is False
        keys = Base.metadata.tables["api_keys"]
        sessions = Base.metadata.tables["refresh_token_records"]
        # today's DELETE never touched sessions or keys (access stops on the per-request is_active check)
        assert (await db.execute(select(func.count()).select_from(sessions))).scalar() == 1
        assert (await db.execute(select(keys.c.is_active))).scalars().all() == [True]
    assert await _count(maker, SubjectRightsJob) == 0 and await _count(maker, AccessAuditLog) == 0


async def test_deactivate_mode_leaves_a_pending_request_and_an_erased_row_alone(
    maker: Any, world: SimpleNamespace, events: list[Any],
) -> None:
    request_id = await _seed_request(maker, world.subject)
    async with maker.session() as db:
        erased = _user(UserRole.TESTER, "zqxwgone", is_active=False, erased_at=datetime.now(timezone.utc))
        db.add(erased)
        await db.commit()
    assert (await _delete(maker, events, world.subject.id)).status_code == 204
    assert (await _delete(maker, events, erased.id)).status_code == 204
    assert [e.detail for e in await _events_of(maker, erased.id)] == [{"no_changes": True}]
    async with maker.session() as db:
        assert (await db.get(SubjectErasureRequest, request_id)).status == "pending"
    assert await _count(maker, SubjectRightsJob) == 0


@pytest.mark.parametrize("mode", ["deactivate", "erase"])
async def test_unknown_and_out_of_scope_ids_are_404_in_both_modes(maker: Any, world: SimpleNamespace,
                                                                  events: list[Any], mode: str) -> None:
    await _set_mode(maker, mode)
    unknown = await _delete(maker, events, uuid.uuid4())
    bound = SimpleNamespace(sso_config_id=uuid.uuid4(), id=TOKEN_ID)   # the subject has no identity of it
    out_of_scope = await _delete(maker, events, world.subject.id, token=bound)
    assert unknown.status_code == 404 and out_of_scope.status_code == 404
    assert out_of_scope.json()["detail"] == f"User {world.subject.id} not found"
    assert events == []
    assert await _count(maker, IdentityEvent) == 0 and await _count(maker, SubjectRightsJob) == 0
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is True


# ── erase: the W31 erasure in ONE commit, the IdP as the requester ───────────

async def test_erase_mode_starts_the_erasure_in_one_commit(maker: Any, world: SimpleNamespace,
                                                           events: list[Any]) -> None:
    subject = world.subject
    await _set_mode(maker, "erase")
    await _seed_sessions_and_keys(maker, subject)
    request_id = await _seed_request(maker, subject)
    response = await _delete(maker, events, subject.id)
    assert response.status_code == 204 and response.content == b""
    (job,) = await _rows(maker, SubjectRightsJob)
    # the revocation on the request's session, ONE commit, then the cache drop and the enqueue
    assert events == [("revoke", subject.id, True), ("commit",), ("membership", subject.id), ("dispatch", str(job.id))]
    assert (job.kind, job.source, job.requested_by_id, job.status, job.phase) == (
        "erasure", "scim", None, "queued", "lockout")
    assert job.counts == {"refresh_token_records.user_id": 1}
    async with maker.session() as db:
        row = await db.get(User, subject.id)
        assert row.is_active is False and row.erased_at is None    # the worker pseudonymises later
        assert (await db.execute(select(func.count()).select_from(
            Base.metadata.tables["refresh_token_records"]))).scalar() == 0
        keys = Base.metadata.tables["api_keys"]
        assert (await db.execute(select(keys.c.is_active))).scalars().all() == [False]
        request = await db.get(SubjectErasureRequest, request_id)
    assert (request.status, request.erasure_job_id, request.decided_by_id) == ("accepted", job.id, None)
    assert request.decided_at is not None
    (audit,) = await _rows(maker, AccessAuditLog)
    assert (audit.action, audit.actor_user_id, audit.actor_name, audit.target_user_id) == (
        "subject_erasure_requested", None, None, subject.id)
    assert audit.before_value == {"is_active": True}
    assert audit.after_value == {"job_id": str(job.id), "via": "scim", "scim_token_id": str(TOKEN_ID),
                                 "request_id": str(request_id)}
    assert [_event_shape(e) for e in await _events_of(maker, subject.id)] == [
        (IdentityEventType.SCIM_USER_ERASURE_STARTED.value, {"job_id": str(job.id), "scim_token_id": str(TOKEN_ID)},
         None, None, None, CLIENT_IP, True, None),
    ]


async def test_erase_mode_without_a_request_or_a_token_id(maker: Any, world: SimpleNamespace,
                                                          events: list[Any]) -> None:
    """No pending request → no ``request_id``; a bearer override without ``id`` (the tests' namespace) → null."""
    await _set_mode(maker, "erase")
    response = await _delete(maker, events, world.subject.id, token=SimpleNamespace(sso_config_id=None))
    assert response.status_code == 204
    (job,) = await _rows(maker, SubjectRightsJob)
    (audit,) = await _rows(maker, AccessAuditLog)
    assert audit.after_value == {"job_id": str(job.id), "via": "scim", "scim_token_id": None}
    (event,) = await _events_of(maker, world.subject.id)
    assert event.detail == {"job_id": str(job.id), "scim_token_id": None}


async def test_a_bound_token_erases_a_person_in_its_scope(maker: Any, world: SimpleNamespace,
                                                          events: list[Any]) -> None:
    config_id = uuid.uuid4()
    async with maker.session() as db:
        db.add(FederatedIdentity(id=uuid.uuid4(), user_id=world.subject.id, sso_config_id=config_id,
                                 external_id="ext-zqxw"))
        await db.commit()
    await _set_mode(maker, "erase")
    response = await _delete(maker, events, world.subject.id,
                             token=SimpleNamespace(sso_config_id=config_id, id=TOKEN_ID))
    assert response.status_code == 204
    (event,) = await _events_of(maker, world.subject.id)
    assert event.event_type == IdentityEventType.SCIM_USER_ERASURE_STARTED.value and event.sso_config_id == config_id
    assert await _count(maker, SubjectRightsJob, SubjectRightsJob.source == "scim") == 1


# ── D-10: idempotent ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, error_code", [("queued", None), ("running", None),
                                                ("running", "erasure_incomplete")])
async def test_a_retry_while_the_erasure_is_live_is_a_no_op(maker: Any, world: SimpleNamespace, events: list[Any],
                                                            status: str, error_code: Optional[str]) -> None:
    await _set_mode(maker, "erase")
    live = _job(world.subject, world.admin, kind="erasure", status=status, error_code=error_code)
    async with maker.session() as db:
        db.add(live)
        await db.commit()
    response = await _delete(maker, events, world.subject.id)
    assert response.status_code == 204 and events == [("commit",)]
    assert [_event_shape(e) for e in await _events_of(maker, world.subject.id)] == [
        (IdentityEventType.SCIM_USER_UPDATED.value, {"no_changes": True, "erasure_job_id": str(live.id)},
         None, None, None, CLIENT_IP, True, None),
    ]
    assert await _count(maker, SubjectRightsJob) == 1 and await _count(maker, AccessAuditLog) == 0
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is True   # nothing else: no deactivation either


async def test_a_second_delete_after_the_erasure_started_is_a_no_op(maker: Any, world: SimpleNamespace,
                                                                    events: list[Any]) -> None:
    await _set_mode(maker, "erase")
    assert (await _delete(maker, events, world.subject.id)).status_code == 204
    (job,) = await _rows(maker, SubjectRightsJob)
    events.clear()
    assert (await _delete(maker, events, world.subject.id)).status_code == 204
    assert events == [("commit",)]
    details = sorted((e.event_type, str(e.detail)) for e in await _events_of(maker, world.subject.id))
    assert [kind for kind, _ in details] == [IdentityEventType.SCIM_USER_ERASURE_STARTED.value,
                                             IdentityEventType.SCIM_USER_UPDATED.value]
    assert await _count(maker, SubjectRightsJob) == 1 and await _count(maker, AccessAuditLog) == 1
    assert {"no_changes": True, "erasure_job_id": str(job.id)} in [
        e.detail for e in await _events_of(maker, world.subject.id)]


async def test_an_erased_person_is_todays_no_op(maker: Any, world: SimpleNamespace, events: list[Any]) -> None:
    await _set_mode(maker, "erase")
    async with maker.session() as db:
        row = await db.get(User, world.subject.id)
        row.is_active, row.erased_at = False, datetime.now(timezone.utc)
        await db.commit()
    response = await _delete(maker, events, world.subject.id)
    assert response.status_code == 204 and events == [("commit",)]
    assert [(e.event_type, e.detail) for e in await _events_of(maker, world.subject.id)] == [
        (IdentityEventType.SCIM_USER_UPDATED.value, {"no_changes": True})]
    assert await _count(maker, SubjectRightsJob) == 0 and await _count(maker, AccessAuditLog) == 0


# ── D-9: a refused erasure falls back to today's deactivation ────────────────

async def _refusing(maker: Any, world: SimpleNamespace, code: str) -> User:
    """A person whose erasure is refused with ``code``."""
    if code == "export_blocks_erasure":
        async with maker.session() as db:
            db.add(_job(world.subject, world.admin, kind="export", status="running"))
            await db.commit()
        return world.subject
    if code == "last_admin":
        return world.admin            # the only active administrator of the world
    return world.bot                  # not_a_person (a service account)


@pytest.mark.parametrize("code", ["not_a_person", "export_blocks_erasure", "last_admin"])
async def test_a_refused_erasure_deactivates_and_records_the_refusal(maker: Any, world: SimpleNamespace,
                                                                    events: list[Any], code: str) -> None:
    await _set_mode(maker, "erase")
    person = await _refusing(maker, world, code)
    request_id = await _seed_request(maker, person)
    response = await _delete(maker, events, person.id)
    assert response.status_code == 204 and response.content == b""
    assert events == [("commit",)]                    # no revocation, no dispatch
    shapes = sorted((_event_shape(e) for e in await _events_of(maker, person.id)), key=lambda s: str(s[0]))
    assert shapes == sorted([
        (IdentityEventType.SCIM_USER_DEACTIVATED.value, {"deactivated": True}, None, None, None, CLIENT_IP, True, None),
        (IdentityEventType.SCIM_ERASURE_REFUSED.value, {"code": code}, None, None, None, CLIENT_IP, False,
         ERROR_SENTENCES[code]),
    ], key=lambda s: str(s[0]))
    async with maker.session() as db:
        assert (await db.get(User, person.id)).is_active is False       # access stops either way
        assert (await db.get(SubjectErasureRequest, request_id)).status == "pending"
    assert await _count(maker, SubjectRightsJob, SubjectRightsJob.kind == "erasure") == 0
    assert await _count(maker, AccessAuditLog) == 0


async def test_a_synthetic_account_falls_back_too(maker: Any, world: SimpleNamespace, events: list[Any]) -> None:
    await _set_mode(maker, "erase")
    assert (await _delete(maker, events, world.synthetic.id)).status_code == 204
    refused = [e for e in await _events_of(maker, world.synthetic.id)
               if e.event_type == IdentityEventType.SCIM_ERASURE_REFUSED.value]
    assert [(e.detail, e.success) for e in refused] == [({"code": "not_a_person"}, False)]


# ── after the commit: a lost enqueue only logs ───────────────────────────────

async def test_a_lost_enqueue_still_answers_204_with_the_job_committed(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []

    def _refuse(*args: Any, **kwargs: Any) -> None:
        sent.append(kwargs.get("args"))
        raise ConnectionError("broker down")

    async def _revoke(user_id: uuid.UUID, db: Any = None) -> None:
        return None

    async def _invalidate(user_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _revoke)
    monkeypatch.setattr(scim_routes, "invalidate_membership_cache", _invalidate)
    monkeypatch.setitem(sys.modules, "app.worker.tasks",
                        SimpleNamespace(erase_subject_data=SimpleNamespace(apply_async=_refuse)))
    await _set_mode(maker, "erase")
    response = await _delete(maker, [], world.subject.id)
    assert response.status_code == 204
    (job,) = await _rows(maker, SubjectRightsJob)
    assert sent == [[str(job.id)]] and job.status == "queued"   # the janitor re-sends a job left queued


# ── the worker's closing audit row for a SCIM job (D-11) ─────────────────────

async def test_the_erased_audit_row_of_a_scim_job_names_the_token(maker: Any, world: SimpleNamespace,
                                                                  events: list[Any]) -> None:
    await _set_mode(maker, "erase")
    assert (await _delete(maker, events, world.subject.id)).status_code == 204
    async with maker.session() as db:
        (job,) = (await db.execute(select(SubjectRightsJob))).scalars().all()
        await erasure.stage_erased_audit(db, job=job)
        await db.commit()
    erased = await _rows(maker, AccessAuditLog, AccessAuditLog.action == "subject_erased")
    assert len(erased) == 1 and (erased[0].actor_user_id, erased[0].actor_name) == (None, None)
    assert erased[0].after_value == {"job_id": str(job.id), "counts": job.counts, "via": "scim",
                                     "scim_token_id": str(TOKEN_ID)}


# ── B4: the admin panel reads a SCIM job (no requester) ──────────────────────

async def test_the_admin_panel_reads_a_scim_job_with_no_requester(maker: Any, world: SimpleNamespace,
                                                                  events: list[Any]) -> None:
    from app.core.deps import get_current_active_user
    from app.routers import subject_erasure, subject_rights

    await _set_mode(maker, "erase")
    assert (await _delete(maker, events, world.subject.id)).status_code == 204
    (job,) = await _rows(maker, SubjectRightsJob)
    app = FastAPI()
    app.include_router(subject_rights.router)
    app.include_router(subject_erasure.router)

    async def _db() -> Any:
        async with maker.session() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: world.admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get(f"/api/v1/users/{world.subject.id}/data-jobs")
        one = await client.get(f"/api/v1/subject-data-jobs/{job.id}")
        preview = await client.get(f"/api/v1/users/{world.subject.id}/erasure-preview")
    assert listed.status_code == 200 and one.status_code == 200 and preview.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    for read in (item, one.json(), preview.json()["erasure_job"]):
        assert (read["id"], read["source"], read["requested_by_display"]) == (str(job.id), "scim", None)
        assert read["subject_display"] == "Zqxwsubj" and read["phase"] == "lockout"
    assert [b["code"] for b in preview.json()["blockers"]] == ["erasure_running"]
