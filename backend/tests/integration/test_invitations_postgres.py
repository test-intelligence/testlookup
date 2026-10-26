"""ADM-01: an invitation can be redeemed, once, by the person it was sent to.

``POST /users/invite`` stored a plaintext token, put the email in the link's
query string and nothing ever read the token. Now: only ``sha256(token)`` is
stored (migration 0233), the link is ``/accept-invite#token=…``, the mail goes
out when SMTP is configured (``email_sent``), and the public
``/auth/invitations/preview`` + ``/auth/invitations/accept`` create the account
with the invited role and projects and sign it in — under ``SELECT … FOR
UPDATE``, single use, with one generic 400 for every bad token (no oracle).

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


@pytest.fixture
async def world(monkeypatch):
    from app.core import deps
    from app.models.postgres import Project, User, UserRole
    from app.services.notification import email_service

    sent: list[dict] = []
    smtp = {"enabled": False}

    async def _cfg():
        return dict(smtp)

    async def _send(to_email, subject, html_body, text_body=None, smtp_cfg=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body, "text": text_body})

    monkeypatch.setattr(email_service, "get_smtp_config", _cfg)
    monkeypatch.setattr(email_service, "send_html_email", _send)
    monkeypatch.setattr(deps, "invalidate_membership_cache", AsyncMock())

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    admin = User(id=uuid.uuid4(), email=f"admin-{marker}@testlookup.dev", username=f"admin-{marker}",
                 full_name=f"Admin {marker}", hashed_password="x", role=UserRole.ADMIN, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Payments {marker}", slug=f"payments-{marker}", is_active=True)
    async with factory() as db:
        db.add_all([admin, project])
        await db.commit()
    created_emails: list[str] = []
    try:
        async with factory() as db:
            yield {"db": db, "admin": admin, "project": project, "marker": marker, "sent": sent,
                   "smtp": smtp, "emails": created_emails}
    finally:
        from app.models.postgres import ProjectMember, RefreshTokenRecord, UserInvitation

        async with factory() as db:
            emails = [f"new-{marker}@testlookup.dev", *created_emails]
            ids = (await db.execute(select(User.id).where(User.email.in_(emails)))).scalars().all()
            await db.execute(delete(UserInvitation).where(UserInvitation.invited_by_id == admin.id))
            if ids:
                await db.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id.in_(ids)))
                await db.execute(delete(ProjectMember).where(ProjectMember.user_id.in_(ids)))
                await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()
        await engine.dispose()


def _token(link: str) -> str:
    assert "#token=" in link and "?" not in link, link
    return link.split("#token=", 1)[1]


async def _invite(world, **extra):
    from app.models.postgres import UserRole
    from app.models.schemas import InviteProjectMembership, InviteUserRequest
    from app.routers.users import invite_user

    payload = InviteUserRequest(
        email=f"new-{world['marker']}@testlookup.dev",
        role=UserRole.QA_ENGINEER,
        project_memberships=[InviteProjectMembership(project_id=world["project"].id, role=UserRole.QA_ENGINEER)],
        **extra,
    )
    return await invite_user(payload, db=world["db"], current_user=world["admin"])


async def _accept(world, token, username=None, password="Sup3r-secret!"):
    from app.models.schemas import AcceptInvitationRequest
    from app.routers.auth import accept_invitation

    payload = AcceptInvitationRequest(
        token=token, username=username or f"new-{world['marker']}", full_name="New Tester", password=password,
    )
    return await accept_invitation(payload, db=world["db"])


async def test_invite_stores_only_the_hash_and_links_by_fragment(world, caplog):
    from app.models.postgres import UserInvitation
    from app.services.one_time_tokens import hash_token

    caplog.set_level(logging.DEBUG)
    answer = await _invite(world)
    raw = _token(answer.invitation_link)
    assert answer.invitation_link.startswith("http") and "/accept-invite#token=" in answer.invitation_link
    assert world["marker"] not in answer.invitation_link  # the email is not in the link
    assert answer.email_sent is False  # SMTP off: copy-link path
    row = (await world["db"].execute(select(UserInvitation).where(UserInvitation.id == answer.id))).scalar_one()
    assert row.token is None and row.token_hash == hash_token(raw) and raw not in (row.token_hash or "")
    assert row.project_memberships == [{"project_id": str(world["project"].id), "role": "QA_ENGINEER"}]
    assert raw not in caplog.text


async def test_accept_creates_the_account_with_role_and_project_and_signs_in(world):
    from app.models.postgres import ProjectMember, User, UserInvitation
    from app.models.schemas import InvitationTokenRequest, TokenResponse
    from app.routers.auth import preview_invitation

    answer = await _invite(world)
    raw = _token(answer.invitation_link)
    preview = await preview_invitation(InvitationTokenRequest(token=raw), db=world["db"])
    assert preview.email == f"new-{world['marker']}@testlookup.dev"
    assert preview.invited_by_name == world["admin"].full_name
    assert preview.project_names == [world["project"].name] and preview.sso_required is False

    tokens = await _accept(world, raw)
    assert isinstance(tokens, TokenResponse) and tokens.access_token and tokens.refresh_token
    assert tokens.must_change_password is False
    db = world["db"]
    db.expire_all()
    user = (await db.execute(select(User).where(User.email == preview.email))).scalar_one()
    assert str(user.role) in ("QA_ENGINEER", "UserRole.QA_ENGINEER")
    members = (await db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id))).scalars().all()
    assert [(m.project_id, m.role) for m in members] == [(world["project"].id, "QA_ENGINEER")]
    inv = (await db.execute(select(UserInvitation).where(UserInvitation.id == answer.id))).scalar_one()
    assert inv.is_used and inv.used_at is not None and inv.accepted_user_id == user.id


async def test_every_bad_token_gets_the_same_answer(world, monkeypatch):
    """Reused, expired, revoked and tampered tokens: one status, one message."""
    from app.models.postgres import UserInvitation
    from app.routers.users import revoke_invitation

    db = world["db"]
    first = await _invite(world)
    raw = _token(first.invitation_link)
    await _accept(world, raw)
    details = []
    for bad in (raw, raw[:-2] + "zz", "not-a-token"):
        with pytest.raises(HTTPException) as exc:
            await _accept(world, bad, username=f"other-{world['marker']}")
        details.append((exc.value.status_code, exc.value.detail))

    # expired
    world["emails"].append(f"exp-{world['marker']}@testlookup.dev")
    from app.models.schemas import InviteUserRequest
    from app.routers.users import invite_user

    expiring = await invite_user(InviteUserRequest(email=f"exp-{world['marker']}@testlookup.dev"), db=db,
                                 current_user=world["admin"])
    row = (await db.execute(select(UserInvitation).where(UserInvitation.id == expiring.id))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await _accept(world, _token(expiring.invitation_link), username=f"exp-{world['marker']}")
    details.append((exc.value.status_code, exc.value.detail))

    # revoked
    world["emails"].append(f"rev-{world['marker']}@testlookup.dev")
    revoked = await invite_user(InviteUserRequest(email=f"rev-{world['marker']}@testlookup.dev"), db=db,
                                current_user=world["admin"])
    summary = await revoke_invitation(revoked.id, db=db, current_user=world["admin"])
    assert summary.status == "revoked"
    with pytest.raises(HTTPException) as exc:
        await _accept(world, _token(revoked.invitation_link), username=f"rev-{world['marker']}")
    details.append((exc.value.status_code, exc.value.detail))

    assert details == [(400, INVALID)] * 5


async def test_resend_kills_the_first_link_and_emails_when_smtp_is_on(world):
    from app.routers.users import list_invitations, resend_invitation

    world["smtp"]["enabled"] = True
    first = await _invite(world)
    assert first.email_sent is True
    assert len(world["sent"]) == 1 and world["sent"][0]["to"] == first.email
    assert first.invitation_link in world["sent"][0]["text"]

    second = await resend_invitation(first.id, db=world["db"], current_user=world["admin"])
    assert second.email_sent is True and second.invitation_link != first.invitation_link
    with pytest.raises(HTTPException) as exc:
        await _accept(world, _token(first.invitation_link))
    assert exc.value.detail == INVALID

    pending = await list_invitations(db=world["db"], current_user=world["admin"], include_closed=False)
    mine = [p for p in pending if p.id == first.id]
    assert len(mine) == 1 and mine[0].status == "pending"
    assert mine[0].project_memberships[0].project_name == world["project"].name
    assert "token" not in mine[0].model_dump()

    await _accept(world, _token(second.invitation_link))


async def test_a_failed_send_still_returns_the_link(world, monkeypatch):
    from app.services.notification import email_service

    async def _boom(*a, **k):
        raise ConnectionRefusedError("relay down")

    world["smtp"]["enabled"] = True
    monkeypatch.setattr(email_service, "send_html_email", _boom)
    answer = await _invite(world)
    assert answer.email_sent is False and "#token=" in answer.invitation_link


async def test_accept_works_with_registration_disabled_and_refuses_a_taken_username(world, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REGISTRATION_ENABLED", False)
    answer = await _invite(world)
    with pytest.raises(HTTPException) as exc:
        await _accept(world, _token(answer.invitation_link), username=world["admin"].username)
    assert exc.value.status_code == 409 and "username" in exc.value.detail
    tokens = await _accept(world, _token(answer.invitation_link))  # the 409 did not burn the link
    assert tokens.access_token


async def test_a_second_active_invite_for_the_same_email_is_409(world):
    await _invite(world)
    with pytest.raises(HTTPException) as exc:
        await _invite(world)
    assert exc.value.status_code == 409


async def test_an_unknown_project_is_404(world):
    from app.models.schemas import InviteProjectMembership, InviteUserRequest
    from app.routers.users import invite_user

    payload = InviteUserRequest(
        email=f"new-{world['marker']}@testlookup.dev",
        project_memberships=[InviteProjectMembership(project_id=uuid.uuid4())],
    )
    with pytest.raises(HTTPException) as exc:
        await invite_user(payload, db=world["db"], current_user=world["admin"])
    assert exc.value.status_code == 404
