"""A live session token stops working when the credential behind it does.

The rule: ``POST /api/v1/stream/events/batch`` trusts only the
``X-Session-Token`` header, so the token must not outlive what opened the
session:

* revoking the API key that opened it deletes the token;
* deactivating the account that opened it deletes the token;
* removing that account from the session's project deletes the token (its
  sessions in other projects keep working);
* a session accepts events for at most 24 hours from its creation, however
  many batches keep arriving. The token's Redis lifetime never slides past
  that point.

Deleting tokens is best effort: a Redis failure is logged and never fails the
revocation that triggered it. The 24-hour limit is the backstop.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core import deps
from app.models.postgres import LiveSession
from app.services import stream_service

pytestmark = pytest.mark.regression


class _Redis:
    """Strings with a recorded TTL, and sets. No clock: TTLs are only recorded."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise ConnectionError("redis is down")

    async def get(self, key):
        self._check()
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self._check()
        self.values[key] = str(value)
        self.ttl[key] = int(ttl)

    async def expire(self, key, seconds):
        self._check()
        if key in self.values or key in self.sets:
            self.ttl[key] = int(seconds)
            return True
        return False

    async def delete(self, *keys):
        self._check()
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)
            self.ttl.pop(key, None)
        return removed

    async def sadd(self, key, *members):
        self._check()
        self.sets.setdefault(key, set()).update(str(m) for m in members)
        return len(members)

    async def smembers(self, key):
        self._check()
        return set(self.sets.get(key, set()))


@pytest.fixture
def world(monkeypatch):
    """A fake Redis, a fake session table, and the transport stubbed out."""
    redis = _Redis()
    rows: dict[uuid.UUID, LiveSession] = {}
    clock = SimpleNamespace(now=datetime.now(timezone.utc))

    monkeypatch.setattr(stream_service, "get_redis", lambda: redis)
    monkeypatch.setattr(stream_service, "_utcnow", lambda: clock.now)
    monkeypatch.setattr(stream_service, "run_is_tombstoned", AsyncMock(return_value=False))
    monkeypatch.setattr(stream_service, "_persist_event_batch", AsyncMock(return_value=1))
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.start", AsyncMock(return_value=None)
    )

    class _Lookup:
        async def __aenter__(self):
            async def _get(_model, session_uuid):
                return rows.get(session_uuid)

            return SimpleNamespace(get=_get)

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Lookup())

    async def open_session(*, project_id, account_id, api_key_id=None):
        """Open a session the way the route does; return (token, batch)."""
        opener = SimpleNamespace(id=account_id)
        if api_key_id is not None:
            deps._bind_api_key_grant(
                opener,
                deps.ApiKeyGrant(key_id=api_key_id, scopes=("stream:write",), expires_at=None),
            )

        def _add(row):
            if isinstance(row, LiveSession):
                rows[row.id] = row

        db = AsyncMock()
        db.add = MagicMock(side_effect=_add)
        db.begin_nested = MagicMock()
        db.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
        db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(
            stream_service,
            "resolve_project",
            AsyncMock(return_value=SimpleNamespace(id=project_id, name="Checkout")),
        )
        payload = SimpleNamespace(
            project_id=str(project_id), client_name="sdk", machine_id="ci-1",
            build_number="b-1", framework="pytest", branch="main", commit_hash=None,
            total_tests=1, release_name=None, launch_name=None, suite_name=None,
            metadata={},
        )
        response = await stream_service.create_session(
            db, payload, bound_project_id=project_id, user=opener,
        )
        batch = SimpleNamespace(
            session_id=response.session_id,
            run_id=response.run_id,
            events=[{"event_type": "test_result"}],
            batch_id=None,
        )
        return response.session_token, batch

    return SimpleNamespace(redis=redis, rows=rows, clock=clock, open_session=open_session)


async def _send(token, batch):
    return await stream_service.ingest_event_batch(batch, token)


async def _assert_refused(token, batch):
    with pytest.raises(HTTPException) as refused:
        await _send(token, batch)
    assert refused.value.status_code == 401
    return refused.value


# ── revocation ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoking_the_api_key_ends_the_sessions_it_opened(world):
    key_id, other_key_id = uuid.uuid4(), uuid.uuid4()
    project_id, account_id = uuid.uuid4(), uuid.uuid4()
    token, batch = await world.open_session(
        project_id=project_id, account_id=account_id, api_key_id=key_id
    )
    other_token, other_batch = await world.open_session(
        project_id=project_id, account_id=uuid.uuid4(), api_key_id=other_key_id
    )
    assert (await _send(token, batch)).accepted == 1  # works before the revocation

    removed = await stream_service.revoke_session_tokens_for_api_key(key_id)

    assert removed == 1
    await _assert_refused(token, batch)
    # Another key's session is untouched.
    assert (await _send(other_token, other_batch)).accepted == 1


@pytest.mark.asyncio
async def test_deactivating_the_account_ends_every_session_it_opened(world):
    account_id = uuid.uuid4()
    with_key = await world.open_session(
        project_id=uuid.uuid4(), account_id=account_id, api_key_id=uuid.uuid4()
    )
    signed_in = await world.open_session(project_id=uuid.uuid4(), account_id=account_id)
    someone_else = await world.open_session(project_id=uuid.uuid4(), account_id=uuid.uuid4())
    assert (await _send(*with_key)).accepted == 1

    removed = await stream_service.revoke_session_tokens_for_user(account_id)

    assert removed == 2
    await _assert_refused(*with_key)
    await _assert_refused(*signed_in)
    assert (await _send(*someone_else)).accepted == 1


@pytest.mark.asyncio
async def test_removing_a_member_ends_their_sessions_in_that_project_only(world):
    account_id = uuid.uuid4()
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    in_a = await world.open_session(project_id=project_a, account_id=account_id)
    in_b = await world.open_session(project_id=project_b, account_id=account_id)
    colleague_in_a = await world.open_session(project_id=project_a, account_id=uuid.uuid4())

    removed = await stream_service.revoke_session_tokens_for_project_member(
        project_a, account_id
    )

    assert removed == 1
    await _assert_refused(*in_a)
    assert (await _send(*in_b)).accepted == 1
    assert (await _send(*colleague_in_a)).accepted == 1


@pytest.mark.asyncio
async def test_a_redis_failure_during_revocation_is_logged_not_raised(world, caplog):
    key_id = uuid.uuid4()
    await world.open_session(
        project_id=uuid.uuid4(), account_id=uuid.uuid4(), api_key_id=key_id
    )
    world.redis.fail = True

    with caplog.at_level("WARNING"):
        removed = await stream_service.revoke_session_tokens_for_api_key(key_id)

    assert removed == 0
    assert "live_session_token_revocation_failed" in caplog.text


@pytest.mark.asyncio
async def test_revoking_with_nothing_open_is_a_no_op(world):
    assert await stream_service.revoke_session_tokens_for_api_key(uuid.uuid4()) == 0
    assert await stream_service.revoke_session_tokens_for_user(uuid.uuid4()) == 0


# ── the 24-hour limit ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_older_than_the_limit_is_refused_even_when_batches_kept_arriving(world):
    token, batch = await world.open_session(project_id=uuid.uuid4(), account_id=uuid.uuid4())
    opened_at = world.clock.now

    # A batch every hour keeps the Redis key alive, as the old sliding TTL did.
    for hour in range(1, 24):
        world.clock.now = opened_at + timedelta(hours=hour)
        assert (await _send(token, batch)).accepted == 1

    world.clock.now = opened_at + timedelta(hours=24, seconds=1)
    refused = await _assert_refused(token, batch)

    assert "24 hours" in refused.detail and "new session" in refused.detail
    # The token is gone, not just refused this once.
    assert stream_service.SESSION_TOKEN_KEY.format(token=token) not in world.redis.values


@pytest.mark.asyncio
async def test_the_token_lifetime_never_slides_past_the_limit(world):
    token, batch = await world.open_session(project_id=uuid.uuid4(), account_id=uuid.uuid4())
    token_key = stream_service.SESSION_TOKEN_KEY.format(token=token)
    opened_at = world.clock.now

    world.clock.now = opened_at + timedelta(hours=23, minutes=59)
    await _send(token, batch)

    assert world.redis.ttl[token_key] <= 60, (
        "an accepted batch extended the token past 24 hours from creation"
    )


@pytest.mark.asyncio
async def test_a_young_session_keeps_working(world):
    token, batch = await world.open_session(project_id=uuid.uuid4(), account_id=uuid.uuid4())
    world.clock.now = world.clock.now + timedelta(hours=2)

    assert (await _send(token, batch)).accepted == 1
    token_key = stream_service.SESSION_TOKEN_KEY.format(token=token)
    assert 0 < world.redis.ttl[token_key] <= stream_service.SESSION_MAX_AGE_SECONDS


# ── the three places that revoke ─────────────────────────────────────────────


@pytest.fixture
def revocations(monkeypatch):
    calls = SimpleNamespace(
        api_key=AsyncMock(return_value=0),
        account=AsyncMock(return_value=0),
        member=AsyncMock(return_value=0),
    )
    monkeypatch.setattr(stream_service, "revoke_session_tokens_for_api_key", calls.api_key)
    monkeypatch.setattr(stream_service, "revoke_session_tokens_for_user", calls.account)
    monkeypatch.setattr(stream_service, "revoke_session_tokens_for_project_member", calls.member)
    return calls


def _scalar(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _admin():
    return SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="admin", email="admin@example.com")


@pytest.mark.asyncio
async def test_deactivating_a_user_revokes_their_session_tokens(revocations, monkeypatch):
    from app.routers import users

    target = SimpleNamespace(id=uuid.uuid4(), is_active=True, role="QA_ENGINEER")
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar(target))
    monkeypatch.setattr(users, "refuse_erased", lambda _u: None)
    monkeypatch.setattr(users, "_assert_not_last_admin", AsyncMock())
    monkeypatch.setattr(users, "log_access_change", AsyncMock())

    await users.update_user_status(
        user_id=target.id,
        payload=SimpleNamespace(is_active=False),
        db=db,
        current_user=_admin(),
    )

    revocations.account.assert_awaited_once_with(target.id)
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_reactivating_a_user_revokes_nothing(revocations, monkeypatch):
    from app.routers import users

    target = SimpleNamespace(id=uuid.uuid4(), is_active=False, role="QA_ENGINEER")
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar(target))
    monkeypatch.setattr(users, "refuse_erased", lambda _u: None)
    monkeypatch.setattr(users, "log_access_change", AsyncMock())

    await users.update_user_status(
        user_id=target.id,
        payload=SimpleNamespace(is_active=True),
        db=db,
        current_user=_admin(),
    )

    revocations.account.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivating_through_the_profile_route_also_revokes(revocations, monkeypatch):
    from app.routers import users

    target = SimpleNamespace(
        id=uuid.uuid4(), is_active=True, role="QA_ENGINEER",
        email="target@example.com", username="target", full_name="Target",
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar(target))
    monkeypatch.setattr(users, "refuse_erased", lambda _u: None)
    monkeypatch.setattr(users, "refuse_reserved_username", lambda **_kw: None)
    monkeypatch.setattr(users, "_assert_not_last_admin", AsyncMock())

    await users.update_user_profile(
        user_id=target.id,
        payload=SimpleNamespace(
            email=None, username=None, full_name=None, role=None, is_active=False
        ),
        db=db,
        current_user=_admin(),
    )

    revocations.account.assert_awaited_once_with(target.id)


@pytest.mark.asyncio
async def test_removing_a_project_member_revokes_their_tokens_in_that_project(
    revocations, monkeypatch
):
    from app.routers import users

    member = MagicMock()
    member.role = "QA_ENGINEER"
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar(member))
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    monkeypatch.setattr(users, "log_access_change", AsyncMock())
    project_id, account_id = uuid.uuid4(), uuid.uuid4()

    await users.remove_project_member(
        project_id=project_id, user_id=account_id, db=db, current_user=_admin(), _=None,
    )

    revocations.member.assert_awaited_once_with(project_id, account_id)


def test_key_revocation_revokes_session_tokens_next_to_the_credential_cache():
    """The key route's cascade is PostgreSQL-only SQL, so the handler is not
    driven here; this pins that every revoked key (the key and the keys it
    minted) has its session tokens revoked where its cached credential is
    dropped."""
    import ast
    import inspect
    import textwrap

    from app.routers import api_keys

    tree = ast.parse(textwrap.dedent(inspect.getsource(api_keys.revoke_api_key)))
    loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and "forget_streaming_key_hash" in ast.unparse(node)
    ]
    assert loops, "the credential-cache loop is gone from revoke_api_key"
    body = ast.unparse(loops[0])
    assert "revoke_session_tokens_for_api_key" in body
    assert "descendants" in body
