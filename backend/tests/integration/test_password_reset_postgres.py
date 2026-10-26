"""ADM-02: forgot password + an admin-issued reset link.

There was no way back into an account short of an administrator editing the
database. Now (migration 0233, ``password_reset_tokens``, hash only, 60
minutes, single use):

* ``POST /auth/password/forgot`` answers the same 202 for every address and
  does the lookup, the per-mailbox limit and the mail in a background task;
* ``POST /users/{id}/password-reset-link`` (ADMIN, never the caller's own
  account) returns a link — the admin never sees the password — kills older
  links and writes an audit row without the token;
* ``POST /auth/password/reset`` sets the password, clears lockout and
  ``must_change_password``, revokes every session, and burns the token. MFA is
  not touched.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. The routes commit, so the rows are deleted at the end.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

INVALID = "This link is invalid, expired or already used."


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        return True


@pytest.fixture
async def world(monkeypatch):
    from app.core.security import get_password_hash
    from app.db import postgres as app_pg
    from app.db import redis_client
    from app.models.postgres import User, UserRole
    from app.routers import auth as auth_router
    from app.services.notification import email_service

    sent: list[dict] = []
    smtp = {"enabled": True}
    revoked: list = []

    async def _cfg():
        return dict(smtp)

    async def _send(to_email, subject, html_body, text_body=None, smtp_cfg=None):
        sent.append({"to": to_email, "subject": subject, "text": text_body})

    async def _revoke_all(user_id, db=None):
        revoked.append(user_id)

    redis = _FakeRedis()
    monkeypatch.setattr(email_service, "get_smtp_config", _cfg)
    monkeypatch.setattr(email_service, "send_html_email", _send)
    monkeypatch.setattr(auth_router, "revoke_all_user_tokens", _revoke_all)
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app_pg, "AsyncSessionLocal", factory)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    admin = User(id=uuid.uuid4(), email=f"admin-{marker}@testlookup.dev", username=f"admin-{marker}",
                 full_name=f"Admin {marker}", hashed_password="x", role=UserRole.ADMIN, is_active=True)
    viewer = User(id=uuid.uuid4(), email=f"viewer-{marker}@testlookup.dev", username=f"viewer-{marker}",
                  full_name=f"Viewer {marker}", hashed_password=get_password_hash("Old-pass-123"),
                  role=UserRole.VIEWER, is_active=True, failed_login_attempts=4,
                  locked_until=datetime.now(timezone.utc) + timedelta(minutes=10), must_change_password=True)
    async with factory() as db:
        db.add_all([admin, viewer])
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "admin": admin, "viewer": viewer, "sent": sent, "smtp": smtp,
                   "revoked": revoked, "redis": redis, "marker": marker}
    finally:
        from app.models.postgres import PasswordResetToken, RefreshTokenRecord, SettingsAuditLog

        async with factory() as db:
            ids = [admin.id, viewer.id]
            await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id.in_(ids)))
            await db.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id.in_(ids)))
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.setting_key == f"user:{viewer.id}"))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.commit()
        await engine.dispose()


def _token(link: str) -> str:
    assert "/reset-password/confirm#token=" in link and "?" not in link, link
    return link.split("#token=", 1)[1]


async def _issue(world):
    from app.routers.users import issue_password_reset_link

    return await issue_password_reset_link(world["viewer"].id, db=world["db"], current_user=world["admin"])


async def _reset(world, token, chosen="New-pass-456"):
    from app.models.schemas import RedeemResetLinkRequest
    from app.routers.auth import reset_password

    return await reset_password(RedeemResetLinkRequest(token=token, new_password=chosen), db=world["db"])


async def test_admin_link_resets_the_password_clears_lockout_and_ends_sessions(world, caplog):
    from app.core.security import verify_password
    from app.models.postgres import PasswordResetToken, SettingsAuditLog, User
    from app.services.one_time_tokens import hash_token

    caplog.set_level(logging.DEBUG)
    answer = await _issue(world)
    raw = _token(answer.link)
    assert answer.email_sent is True and world["sent"][0]["to"] == world["viewer"].email
    db = world["db"]
    row = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == world["viewer"].id))).scalar_one()
    assert row.token_hash == hash_token(raw) and row.issued_by_user_id == world["admin"].id
    audit = (await db.execute(select(SettingsAuditLog).where(SettingsAuditLog.setting_key == f"user:{world['viewer'].id}"))).scalar_one()
    assert audit.action == "password_reset_issued" and raw not in str(audit.changed_fields)

    await _reset(world, raw)
    db.expire_all()
    user = (await db.execute(select(User).where(User.id == world["viewer"].id))).scalar_one()
    assert verify_password("New-pass-456", user.hashed_password)
    assert user.failed_login_attempts == 0 and user.locked_until is None and user.must_change_password is False
    assert world["revoked"] == [world["viewer"].id]
    assert raw not in caplog.text

    with pytest.raises(HTTPException) as exc:  # single use
        await _reset(world, raw)
    assert (exc.value.status_code, exc.value.detail) == (400, INVALID)


async def test_a_new_link_kills_the_older_one_and_expiry_is_enforced(world):
    from app.models.postgres import PasswordResetToken

    first = await _issue(world)
    second = await _issue(world)
    with pytest.raises(HTTPException) as exc:
        await _reset(world, _token(first.link))
    assert exc.value.detail == INVALID

    db = world["db"]
    rows = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None),
                                                               PasswordResetToken.user_id == world["viewer"].id))).scalars().all()
    assert len(rows) == 1
    rows[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await _reset(world, _token(second.link))
    assert (exc.value.status_code, exc.value.detail) == (400, INVALID)
    with pytest.raises(HTTPException) as exc:
        await _reset(world, "tampered-" + _token(second.link)[9:])
    assert (exc.value.status_code, exc.value.detail) == (400, INVALID)


async def test_admin_cannot_issue_a_link_for_their_own_account(world):
    from app.routers.users import issue_password_reset_link

    with pytest.raises(HTTPException) as exc:
        await issue_password_reset_link(world["admin"].id, db=world["db"], current_user=world["admin"])
    assert exc.value.status_code == 400


async def test_forgot_answers_the_same_for_known_and_unknown_and_mails_only_the_real_one(world):
    from fastapi import BackgroundTasks
    from starlette.requests import Request

    from app.models.schemas import ResetLinkRequest
    from app.routers.auth import forgot_password

    def _req():
        return Request({"type": "http", "client": ("10.0.0.9", 1234), "headers": [], "method": "POST", "path": "/"})

    answers = []
    for email in (world["viewer"].email, f"nobody-{world['marker']}@testlookup.dev"):
        tasks = BackgroundTasks()
        answers.append(await forgot_password(ResetLinkRequest(email=email), _req(), tasks))
        await tasks()
    assert answers[0] == answers[1] == {"detail": "If an account exists for that address, a reset link is on its way."}
    assert [m["to"] for m in world["sent"]] == [world["viewer"].email]
    link = world["sent"][0]["text"].split("\n\n")[1]
    await _reset(world, _token(link))


async def test_forgot_is_silent_when_smtp_is_off_and_limited_per_mailbox(world):
    from app.routers.auth import process_forgot_request

    world["smtp"]["enabled"] = False
    await process_forgot_request(world["viewer"].email, "10.0.0.9")
    assert world["sent"] == []

    world["smtp"]["enabled"] = True
    for _ in range(5):
        await process_forgot_request(world["viewer"].email.upper(), "10.0.0.9")
    assert len(world["sent"]) == 3  # FORGOT_PER_EMAIL_PER_HOUR


async def test_forgot_skips_a_deactivated_account(world):
    from app.models.postgres import User
    from app.routers.auth import process_forgot_request

    db = world["db"]
    user = (await db.execute(select(User).where(User.id == world["viewer"].id))).scalar_one()
    user.is_active = False
    await db.commit()
    await process_forgot_request(world["viewer"].email, None)
    assert world["sent"] == []


async def test_auth_options_reports_whether_reset_mail_is_possible(world):
    from app.routers.auth import auth_options

    assert (await auth_options())["password_reset_email"] is True
    world["smtp"]["enabled"] = False
    assert (await auth_options())["password_reset_email"] is False


async def test_mfa_is_still_required_after_a_reset(world, monkeypatch):
    """The reset touches only the password: an enrolled user is still challenged."""
    from app.services import mfa_service

    answer = await _issue(world)
    await _reset(world, _token(answer.link))
    called = AsyncMock(return_value=mfa_service.LoginRequirement.CHALLENGE)
    monkeypatch.setattr(mfa_service, "evaluate_login_requirement", called)
    from fastapi.security import OAuth2PasswordRequestForm
    from starlette.requests import Request

    from app.models.schemas import MfaChallengeResponse
    from app.routers.auth import login

    form = OAuth2PasswordRequestForm(username=world["viewer"].username, password="New-pass-456")
    req = Request({"type": "http", "client": ("10.0.0.9", 1), "headers": [], "method": "POST", "path": "/"})
    result = await login(req, form_data=form, db=world["db"])
    assert isinstance(result, MfaChallengeResponse)
