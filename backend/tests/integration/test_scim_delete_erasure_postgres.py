"""W38 (W31 F-2): SCIM ``DELETE /Users/{id}`` → an optional erasure, on real PostgreSQL (contract §6 G-B).

The real routers (``scim`` — ``verify_scim_bearer`` overridden, never a real token — and ``app_settings``) on a bare
FastAPI app over this module's own engine, against real rows and the real CHECKs / partial uniques:

* deactivate mode (no setting row): event for event what the pre-W38 route body writes on a twin; no job, no
  access-audit row, sessions untouched;
* erase mode: ONE commit holds the job (``source="scim"`` passes 0256's ``ck_srj_erasure_is_admin``, no requester),
  the lock-out (revocation cutoff, sessions deleted, keys disabled, account off), the pending request accepted with
  ``decided_by_id`` NULL, the actor-less ``subject_erasure_requested`` row with ``via: scim`` and
  ``SCIM_USER_ERASURE_STARTED``; the dispatch comes after the commit; a retry and a DELETE of an erased person are
  no-ops; two concurrent DELETEs serialise on the person's row → one job;
* a running export / the last active administrator → deactivated + ``SCIM_ERASURE_REFUSED`` (the last-admin case
  deactivates the other admins inside a transaction that is rolled back, so a shared CI database never changes);
* the setting: an admin API key's PUT → 403 ``instance_interactive_only`` (nothing written); a signed-in admin's
  PUT → the row + ``settings_audit_log`` + ``SCIM_DELETE_ACTION_UPDATED`` in one commit;
* no mail anywhere: no ``pending`` notification row for our people, ``aiosmtplib.send`` never called.

Fixture rules: ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (≥ 0256); ``dispose_engine_for_loop()`` before and
after; the ``scim_delete_action`` row is restored to what it was (or deleted); every row this module seeded or
caused is deleted by id and the touched tables are back to their pre-test counts.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_LETTERS = "ghijkmnopqrstuvwxyz"   # never a hex digit
_TABLES = ("users", "subject_rights_jobs", "subject_erasure_requests", "access_audit_logs", "identity_events",
           "settings_audit_log", "auth_token_revocations", "refresh_token_records", "api_keys", "notification_logs")
_KEY = "scim_delete_action"
TOKEN_ID = uuid.uuid4()


def _tag(n: int = 8) -> str:
    return "".join(random.choice(_LETTERS) for _ in range(n))


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def sessions() -> Any:
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=6, max_overflow=0)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _counts(db: Any) -> dict[str, int]:
    return {t: int((await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()) for t in _TABLES}


@pytest_asyncio.fixture(loop_scope="module")
async def world(sessions: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """People ``<name>_<tag>``: pat / twin / kim (testers), gone (erased), exp (an export running), ada (admin).
    The setting row is snapshotted and restored; ``aiosmtplib.send``, the cache drop and the enqueue are recorders."""
    import aiosmtplib

    from app.models.postgres import User
    from app.routers import scim as scim_routes

    tag = "s" + _tag(7)
    people = {"pat": ("TESTER", True, False), "twin": ("TESTER", True, False), "kim": ("TESTER", True, False),
              "gone": ("TESTER", False, True), "exp": ("TESTER", True, False), "ada": ("ADMIN", True, False)}
    ids = {name: uuid.uuid4() for name in people}
    mailed: list[Any] = []
    dispatched: list[str] = []

    async def _no_mail(*args: Any, **kwargs: Any) -> None:
        mailed.append((args, kwargs))

    async def _no_cache(user_id: Any) -> None:
        return None

    monkeypatch.setattr(aiosmtplib, "send", _no_mail)
    monkeypatch.setattr("app.services.notification.email_service.aiosmtplib.send", _no_mail)
    monkeypatch.setattr(scim_routes, "_dispatch_erasure", lambda job_id: dispatched.append(str(job_id)))
    monkeypatch.setattr(scim_routes, "invalidate_membership_cache", _no_cache)
    async with sessions() as db:
        before = await _counts(db)
        saved = (await db.execute(text("SELECT value::text AS value, updated_by, updated_at FROM app_settings "
                                       "WHERE key = :k"), {"k": _KEY})).mappings().first()
        await db.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": _KEY})
        for name, (role, active, erased) in people.items():
            db.add(User(
                id=ids[name], email=f"{name}-{tag}@example.test", username=f"{name}_{tag}",
                full_name=f"{name.title()} {tag.title()}", hashed_password="!unusable", role=role, is_active=active,
                erased_at=datetime.now(timezone.utc) if erased else None,
            ))
        await db.commit()
    w = SimpleNamespace(sessions=sessions, tag=tag, ids=ids, mailed=mailed, dispatched=dispatched)
    try:
        yield w
    finally:
        await _cleanup(sessions, list(ids.values()), saved)
        async with sessions() as db:
            assert await _counts(db) == before, "a row this module seeded or caused was left behind"


async def _cleanup(sessions: Any, users: list[uuid.UUID], saved: Any) -> None:
    statements: list[tuple[str, dict[str, Any]]] = [
        ("DELETE FROM identity_events WHERE user_id = ANY(:u) OR actor_id = ANY(:u)", {"u": users}),
        ("DELETE FROM settings_audit_log WHERE actor_id = ANY(:u)", {"u": users}),
        ("DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:u) OR target_user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM subject_erasure_requests WHERE subject_user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM subject_rights_jobs WHERE subject_user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM auth_token_revocations WHERE user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM refresh_token_records WHERE user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM api_keys WHERE user_id = ANY(:u)", {"u": users}),
        ("DELETE FROM app_settings WHERE key = :k", {"k": _KEY}),
        ("DELETE FROM users WHERE id = ANY(:u)", {"u": users}),
    ]
    if saved is not None:
        statements.append((
            "INSERT INTO app_settings (key, value, updated_by, updated_at, is_secret_backed) "
            "VALUES (:k, CAST(:v AS json), :b, :t, false)",
            {"k": _KEY, "v": saved["value"], "b": saved["updated_by"], "t": saved["updated_at"]},
        ))
    for statement, params in statements:
        try:
            async with sessions.begin() as db:
                await db.execute(text(statement), params)
        except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
            print(f"scim-erasure teardown: {type(exc).__name__}: {str(exc)[:160]}")


def _app(w: Any, *routers: Any) -> Any:
    from fastapi import FastAPI

    from app.db.postgres import get_db

    app = FastAPI()
    for router in routers:
        app.include_router(router)

    async def _db() -> Any:
        async with w.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    return app


def _scim(w: Any) -> Any:
    """The SCIM router behind an overridden bearer (an unbound token with an id) — never a real token."""
    from httpx import ASGITransport, AsyncClient

    from app.routers import scim as scim_routes

    app = _app(w, scim_routes.router)
    app.dependency_overrides[scim_routes.verify_scim_bearer] = lambda: SimpleNamespace(sso_config_id=None, id=TOKEN_ID)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _settings_client(w: Any, name: str, *, credential: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import _bind_credential_kind, get_current_active_user
    from app.models.postgres import User
    from app.routers import app_settings

    app = _app(w, app_settings.router)
    async with w.sessions() as db:
        user = await db.get(User, w.ids[name])
    _bind_credential_kind(user, credential)
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _rows(w: Any, sql: str, **params: Any) -> list[Any]:
    async with w.sessions() as db:
        return list((await db.execute(text(sql), params)).mappings().all())


async def _events(w: Any, name: str) -> list[tuple[Any, ...]]:
    rows = await _rows(w, "SELECT event_type, detail, actor_id, actor_name, sso_config_id, success, error_message "
                          "FROM identity_events WHERE user_id = :u ORDER BY created_at, event_type", u=w.ids[name])
    return [tuple(row.values()) for row in rows]


async def _set_mode(w: Any, action: str) -> None:
    async with w.sessions.begin() as db:
        await db.execute(text(
            "INSERT INTO app_settings (key, value, is_secret_backed) VALUES (:k, CAST(:v AS json), false) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"), {"k": _KEY, "v": f'{{"action": "{action}"}}'})


async def _clear_mode(w: Any) -> None:
    async with w.sessions.begin() as db:
        await db.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": _KEY})


async def _assert_no_mail(w: Any) -> None:
    pending = await _rows(w, "SELECT id FROM notification_logs WHERE user_id = ANY(:u) AND status = 'pending'",
                          u=list(w.ids.values()))
    assert pending == [] and w.mailed == []


# ── deactivate (no setting row): today's soft delete ─────────────────────────

async def test_deactivate_mode_is_byte_identical_to_the_old_route(world: Any) -> None:
    from app.services.scim_service import scim_update_user

    w = world
    await _clear_mode(w)
    async with w.sessions.begin() as db:
        await db.execute(text("INSERT INTO refresh_token_records (id, user_id, jti_hash, expires_at) "
                              "VALUES (:i, :u, :h, now() + interval '1 day')"),
                         {"i": uuid.uuid4(), "u": w.ids["pat"], "h": uuid.uuid4().hex * 2})
    async with _scim(w) as client:
        first = await client.delete(f"/api/v1/scim/v2/Users/{w.ids['pat']}")
        again = await client.delete(f"/api/v1/scim/v2/Users/{w.ids['pat']}")
    for _ in range(2):   # the pre-W38 route body, on the twin
        async with w.sessions.begin() as db:
            await scim_update_user(db=db, user_id=w.ids["twin"], active=False, sso_config_id=None,
                                   ip_address="127.0.0.1")
    assert first.status_code == 204 and again.status_code == 204 and first.content == b""
    assert await _events(w, "pat") == await _events(w, "twin") == [
        ("SCIM_USER_DEACTIVATED", {"deactivated": True}, None, None, None, True, None),
        ("SCIM_USER_UPDATED", {"no_changes": True}, None, None, None, True, None),
    ]
    assert await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :u", u=w.ids["pat"]) == []
    assert await _rows(w, "SELECT id FROM access_audit_logs WHERE target_user_id = :u", u=w.ids["pat"]) == []
    assert len(await _rows(w, "SELECT id FROM refresh_token_records WHERE user_id = :u", u=w.ids["pat"])) == 1
    assert w.dispatched == []
    await _assert_no_mail(w)


# ── erase: the job, the lock-out, the request and the event in ONE commit ────

async def test_erase_mode_stages_the_scim_erasure_in_one_commit(world: Any) -> None:
    w, pat = world, world.ids["pat"]
    await _set_mode(w, "erase")
    request_id = uuid.uuid4()
    async with w.sessions.begin() as db:
        await db.execute(text("INSERT INTO refresh_token_records (id, user_id, jti_hash, expires_at) "
                              "VALUES (:i, :u, :h, now() + interval '1 day')"),
                         {"i": uuid.uuid4(), "u": pat, "h": uuid.uuid4().hex * 2})
        await db.execute(text("INSERT INTO api_keys (id, user_id, name, key_hash, key_hint, scopes, is_active) "
                              "VALUES (:i, :u, 'ci', :h, 'tl_zz', '[]', true)"),
                         {"i": uuid.uuid4(), "u": pat, "h": uuid.uuid4().hex})
        await db.execute(text("INSERT INTO subject_erasure_requests (id, subject_user_id, status) "
                              "VALUES (:i, :u, 'pending')"), {"i": request_id, "u": pat})
    async with _scim(w) as client:
        response = await client.delete(f"/api/v1/scim/v2/Users/{pat}")
    assert response.status_code == 204 and response.content == b""
    (job,) = await _rows(w, "SELECT id, kind, source, status, phase, requested_by_id FROM subject_rights_jobs "
                            "WHERE subject_user_id = :u", u=pat)
    assert (job["kind"], job["source"], job["status"], job["phase"], job["requested_by_id"]) == (
        "erasure", "scim", "queued", "lockout", None)
    assert w.dispatched == [str(job["id"])]              # after the commit: the worker can see the job
    (user,) = await _rows(w, "SELECT is_active, erased_at FROM users WHERE id = :u", u=pat)
    assert (user["is_active"], user["erased_at"]) == (False, None)
    assert await _rows(w, "SELECT id FROM refresh_token_records WHERE user_id = :u", u=pat) == []
    assert [r["is_active"] for r in await _rows(w, "SELECT is_active FROM api_keys WHERE user_id = :u", u=pat)] == [
        False]
    assert len(await _rows(w, "SELECT jti FROM auth_token_revocations WHERE user_id = :u", u=pat)) == 1
    (request,) = await _rows(w, "SELECT status, erasure_job_id, decided_by_id, decided_at FROM "
                                "subject_erasure_requests WHERE id = :i", i=request_id)
    assert (request["status"], request["erasure_job_id"], request["decided_by_id"]) == ("accepted", job["id"], None)
    assert request["decided_at"] is not None
    (audit,) = await _rows(w, "SELECT action, actor_user_id, actor_name, before_value, after_value FROM "
                              "access_audit_logs WHERE target_user_id = :u", u=pat)
    assert (audit["action"], audit["actor_user_id"], audit["actor_name"]) == ("subject_erasure_requested", None, None)
    assert audit["before_value"] == {"is_active": True}
    assert audit["after_value"] == {"job_id": str(job["id"]), "via": "scim", "scim_token_id": str(TOKEN_ID),
                                    "request_id": str(request_id)}
    assert await _events(w, "pat") == [
        ("SCIM_USER_ERASURE_STARTED", {"job_id": str(job["id"]), "scim_token_id": str(TOKEN_ID)}, None, None, None,
         True, None),
    ]
    # an IdP retry while the job is queued: one no-op row, nothing else
    async with _scim(w) as client:
        retry = await client.delete(f"/api/v1/scim/v2/Users/{pat}")
    assert retry.status_code == 204 and w.dispatched == [str(job["id"])]
    assert (await _events(w, "pat"))[-1][:2] == ("SCIM_USER_UPDATED",
                                                 {"no_changes": True, "erasure_job_id": str(job["id"])})
    assert len(await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :u", u=pat)) == 1
    assert len(await _rows(w, "SELECT id FROM access_audit_logs WHERE target_user_id = :u", u=pat)) == 1
    await _assert_no_mail(w)


async def test_two_concurrent_deletes_start_one_erasure(world: Any) -> None:
    w, kim = world, world.ids["kim"]
    await _set_mode(w, "erase")

    async def _one() -> int:
        async with _scim(w) as client:
            return (await client.delete(f"/api/v1/scim/v2/Users/{kim}")).status_code

    assert await asyncio.gather(_one(), _one()) == [204, 204]
    (job,) = await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :u", u=kim)
    kinds = sorted((event[0], str(event[1])) for event in await _events(w, "kim"))
    assert [kind for kind, _ in kinds] == ["SCIM_USER_ERASURE_STARTED", "SCIM_USER_UPDATED"]
    assert ("SCIM_USER_UPDATED", str({"no_changes": True, "erasure_job_id": str(job["id"])})) in kinds
    assert w.dispatched == [str(job["id"])]


async def test_an_erased_person_is_a_no_op(world: Any) -> None:
    w = world
    await _set_mode(w, "erase")
    async with _scim(w) as client:
        response = await client.delete(f"/api/v1/scim/v2/Users/{w.ids['gone']}")
    assert response.status_code == 204
    assert await _events(w, "gone") == [("SCIM_USER_UPDATED", {"no_changes": True}, None, None, None, True, None)]
    assert await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :u", u=w.ids["gone"]) == []
    assert w.dispatched == []


# ── D-9: a refused erasure deactivates and is recorded as a failure ──────────

async def test_a_running_export_falls_back_to_deactivation(world: Any) -> None:
    w, exp = world, world.ids["exp"]
    await _set_mode(w, "erase")
    async with w.sessions.begin() as db:
        await db.execute(text(
            "INSERT INTO subject_rights_jobs (id, kind, status, source, subject_user_id, requested_by_id, counts, "
            "attempts, requested_at) VALUES (:i, 'export', 'running', 'self', :u, :u, '{}', 1, now())"),
            {"i": uuid.uuid4(), "u": exp})
    async with _scim(w) as client:
        response = await client.delete(f"/api/v1/scim/v2/Users/{exp}")
    from app.services.subject_rights_vocab import ERROR_SENTENCES

    assert response.status_code == 204
    assert await _events(w, "exp") == [   # one transaction (one created_at): ordered by type
        ("SCIM_ERASURE_REFUSED", {"code": "export_blocks_erasure"}, None, None, None, False,
         ERROR_SENTENCES["export_blocks_erasure"]),
        ("SCIM_USER_DEACTIVATED", {"deactivated": True}, None, None, None, True, None),
    ]
    (user,) = await _rows(w, "SELECT is_active FROM users WHERE id = :u", u=exp)
    assert user["is_active"] is False
    assert await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :u AND kind = 'erasure'",
                       u=exp) == []
    assert await _rows(w, "SELECT id FROM access_audit_logs WHERE target_user_id = :u", u=exp) == []
    assert w.dispatched == []
    await _assert_no_mail(w)


async def test_the_last_active_admin_is_deactivated_not_erased(world: Any) -> None:
    """Every OTHER active admin of the (possibly shared) database is deactivated inside a transaction that is
    rolled back: the real service sees ada as the last active administrator, stages the fallback, and nothing
    of it is ever committed."""
    from app.services import scim_delete_setting
    from app.services.scim_service import scim_delete_user

    w, ada = world, world.ids["ada"]
    await _set_mode(w, "erase")
    async with w.sessions() as db:
        await db.execute(text("UPDATE users SET is_active = false WHERE role = 'ADMIN' AND is_active AND id <> :a"),
                         {"a": ada})
        assert await scim_delete_setting.get_action(db) == "erase"
        job_id = await scim_delete_user(db, user_id=ada, sso_config_id=None, scim_token_id=TOKEN_ID,
                                        ip_address="127.0.0.1")
        staged = (await db.execute(text(
            "SELECT event_type, detail, success FROM identity_events WHERE user_id = :u ORDER BY created_at, id"),
            {"u": ada})).all()
        active = (await db.execute(text("SELECT is_active FROM users WHERE id = :u"), {"u": ada})).scalar_one()
        jobs = (await db.execute(text("SELECT count(*) FROM subject_rights_jobs WHERE subject_user_id = :u"),
                                 {"u": ada})).scalar_one()
        await db.rollback()
    assert job_id is None and active is False and jobs == 0
    assert sorted((row[0], str(row[1]), row[2]) for row in staged) == [
        ("SCIM_ERASURE_REFUSED", str({"code": "last_admin"}), False),
        ("SCIM_USER_DEACTIVATED", str({"deactivated": True}), True),
    ]
    assert await _events(w, "ada") == []        # rolled back: nothing of it reached the database
    (row,) = await _rows(w, "SELECT is_active FROM users WHERE id = :u", u=ada)
    assert row["is_active"] is True


# ── the setting: session-only PUT, audited in both trails ────────────────────

async def test_the_setting_put_is_refused_for_an_api_key_and_audited_for_a_session(world: Any) -> None:
    from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT

    w, ada = world, world.ids["ada"]
    await _clear_mode(w)
    async with await _settings_client(w, "ada", credential=CREDENTIAL_KIND_API_KEY) as client:
        keyed = await client.put("/api/v1/settings/scim-delete-action", json={"action": "erase"})
        read = await client.get("/api/v1/settings/scim-delete-action")
    assert keyed.status_code == 403 and keyed.json()["detail"]["code"] == "instance_interactive_only"
    assert read.status_code == 200 and read.json()["action"] == "deactivate"
    assert await _rows(w, "SELECT key FROM app_settings WHERE key = :k", k=_KEY) == []
    async with await _settings_client(w, "ada", credential=CREDENTIAL_KIND_JWT) as client:
        saved = await client.put("/api/v1/settings/scim-delete-action", json={"action": "erase"})
        again = await client.put("/api/v1/settings/scim-delete-action", json={"action": "erase"})
        bad = await client.put("/api/v1/settings/scim-delete-action", json={"action": "remove"})
    assert saved.status_code == 200 and again.status_code == 200, saved.text
    assert saved.json()["action"] == "erase" and saved.json()["updated_by_display"] == f"Ada {w.tag.title()}"
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "scim_delete_action_invalid"
    (row,) = await _rows(w, "SELECT value, updated_by FROM app_settings WHERE key = :k", k=_KEY)
    assert row["value"] == {"action": "erase"} and row["updated_by"] == ada
    audit = await _rows(w, "SELECT setting_key, action, changed_fields FROM settings_audit_log WHERE actor_id = :a",
                        a=ada)
    assert [tuple(r.values()) for r in audit] == [("scim_delete_action", "updated", ["action"])]
    events = await _rows(w, "SELECT event_type, actor_name, detail FROM identity_events WHERE actor_id = :a", a=ada)
    assert [tuple(r.values()) for r in events] == [
        ("SCIM_DELETE_ACTION_UPDATED", f"ada_{w.tag}", {"before": "deactivate", "after": "erase"})]
    await _clear_mode(w)

