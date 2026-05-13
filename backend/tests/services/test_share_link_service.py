"""Unit tests for services.share_link_service.

Covers the security contract of report share tokens:

  - Raw token is generated via ``secrets.token_urlsafe(48)`` and never persisted.
  - Only the SHA-256 digest of the token lives in the DB.
  - ``validate_share_link`` rejects unknown / revoked / expired tokens.
  - On valid use it bumps ``access_count`` and ``last_accessed_at``.
  - ``revoke_share_link`` flips ``is_revoked`` (and raises if missing).
  - ``list_share_links`` returns metadata only (no raw tokens).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from app.core.security import hash_token  # noqa: E402
from app.services.share_link_service import (  # noqa: E402
    create_share_link,
    list_share_links,
    revoke_share_link,
    validate_share_link,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _execute_result(scalar_value=None, all_value=None):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_value)
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=all_value or [])
    result.scalars = MagicMock(return_value=scalars)
    return result


def _make_db(*execute_returns):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_execute_result(**r) for r in execute_returns])
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def _user(username="alice", full_name="Alice Example"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        full_name=full_name,
    )


def _link(
    *,
    is_revoked=False,
    expires_at=None,
    access_count=0,
    last_accessed_at=None,
    token_hash=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        token_hash=token_hash or "deadbeef",
        report_layout="executive",
        is_revoked=is_revoked,
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(days=7)),
        access_count=access_count,
        last_accessed_at=last_accessed_at,
        created_by_id=uuid.uuid4(),
        created_by_name="Alice Example",
    )


# ── create_share_link ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_persists_only_hash_and_returns_raw_token():
    db = _make_db()  # no execute calls expected

    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user = _user()

    created = await create_share_link(db, run_id, project_id, user, expiry_days=7)

    assert created.raw_token  # caller receives the raw token
    assert len(created.raw_token) >= 60  # secrets.token_urlsafe(48) → ~64 chars

    # The persisted row stores the SHA-256 digest of the raw token, not the token.
    assert created.link.token_hash == hash_token(created.raw_token)
    assert created.link.token_hash != created.raw_token

    db.add.assert_called_once_with(created.link)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_each_call_yields_distinct_token():
    """Token entropy: two calls with identical args must not collide."""
    db1 = _make_db()
    db2 = _make_db()

    run_id, project_id, user = uuid.uuid4(), uuid.uuid4(), _user()

    a = await create_share_link(db1, run_id, project_id, user)
    b = await create_share_link(db2, run_id, project_id, user)

    assert a.raw_token != b.raw_token
    assert a.link.token_hash != b.link.token_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_days", [1, 7, 30])
async def test_create_expiry_days_is_honored(expiry_days):
    db = _make_db()

    before = datetime.now(timezone.utc)
    created = await create_share_link(db, uuid.uuid4(), uuid.uuid4(), _user(), expiry_days=expiry_days)
    after = datetime.now(timezone.utc)

    expected_lo = before + timedelta(days=expiry_days)
    expected_hi = after + timedelta(days=expiry_days)
    assert expected_lo <= created.link.expires_at <= expected_hi


@pytest.mark.asyncio
async def test_create_uses_username_then_full_name():
    """``created_by_name`` falls back to full_name when username is missing."""
    db = _make_db()
    user_no_username = SimpleNamespace(id=uuid.uuid4(), username=None, full_name="Bob")

    created = await create_share_link(db, uuid.uuid4(), uuid.uuid4(), user_no_username)

    assert created.link.created_by_name == "Bob"


# ── validate_share_link ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_unknown_token_raises():
    db = _make_db({"scalar_value": None})
    with pytest.raises(ValueError, match="Invalid share link"):
        await validate_share_link(db, "no-such-token")


@pytest.mark.asyncio
async def test_validate_revoked_token_raises():
    link = _link(is_revoked=True)
    db = _make_db({"scalar_value": link})
    with pytest.raises(ValueError, match="revoked"):
        await validate_share_link(db, "irrelevant")


@pytest.mark.asyncio
async def test_validate_expired_token_raises():
    link = _link(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    db = _make_db({"scalar_value": link})
    with pytest.raises(ValueError, match="expired"):
        await validate_share_link(db, "irrelevant")


@pytest.mark.asyncio
async def test_validate_looks_up_by_hash_not_raw():
    """The query must use SHA-256(token), never the raw token."""
    raw = "raw-share-token-xyz"
    expected_hash = hash_token(raw)

    link = _link(token_hash=expected_hash)
    db = _make_db({"scalar_value": link})

    out = await validate_share_link(db, raw)
    assert out is link

    # Inspect the WHERE clause of the SELECT statement we executed.
    call = db.execute.await_args
    stmt = call.args[0]
    where_str = str(stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert expected_hash in where_str
    assert raw not in where_str


@pytest.mark.asyncio
async def test_validate_increments_access_count_and_stamps_time():
    link = _link(access_count=4, last_accessed_at=None)
    db = _make_db({"scalar_value": link})

    before = datetime.now(timezone.utc)
    out = await validate_share_link(db, "irrelevant")
    after = datetime.now(timezone.utc)

    assert out is link
    assert link.access_count == 5
    assert before <= link.last_accessed_at <= after


@pytest.mark.asyncio
async def test_validate_handles_null_access_count():
    """Legacy rows may have access_count = NULL; treat as 0."""
    link = _link()
    link.access_count = None
    db = _make_db({"scalar_value": link})

    await validate_share_link(db, "irrelevant")

    assert link.access_count == 1


# ── revoke_share_link ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_marks_link_revoked():
    link = _link(is_revoked=False)
    db = _make_db({"scalar_value": link})

    await revoke_share_link(db, link.id)

    assert link.is_revoked is True


@pytest.mark.asyncio
async def test_revoke_unknown_link_raises():
    db = _make_db({"scalar_value": None})
    with pytest.raises(ValueError, match="not found"):
        await revoke_share_link(db, uuid.uuid4())


# ── list_share_links ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_links_for_run():
    rows = [_link(), _link(is_revoked=True), _link()]
    db = _make_db({"all_value": rows})

    out = await list_share_links(db, rows[0].run_id)

    assert out == rows
    # No raw_token attribute should ever appear on persisted rows.
    assert all(not hasattr(r, "raw_token") for r in out)


@pytest.mark.asyncio
async def test_list_empty_returns_empty_list():
    db = _make_db({"all_value": []})
    out = await list_share_links(db, uuid.uuid4())
    assert out == []
