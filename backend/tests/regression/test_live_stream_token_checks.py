"""A live stream accepts a token only if the ordinary API would accept it.

The rule: the SSE route (``GET /api/v1/stream/sse/{project_id}``, which streams
forever) and the live WebSocket (``/ws/live/{project_id}``, whose JWT arrives
in the first frame) cannot use ``get_current_user``. They must
still refuse everything it refuses:

* a refresh token, an MFA challenge token, an MFA enrolment token;
* an access token whose jti was revoked (logout);
* an access token issued before the account's cutoff (password change, reset);
* any token while the revocation store cannot answer (fail closed, 503-class);
* a deleted or deactivated account;
* an account still on a temporary password.

and an open SSE stream ends when the token that opened it expires.

All three paths go through ``app.core.session_tokens.authenticate_access_token``.
The parity tests at the bottom run the same token matrix through
``deps.get_current_user`` and pin the calls the HTTP dependencies make, so a new
check added there cannot be forgotten here.
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import deps, session_tokens, token_revocation
from app.core.security import (
    MFA_CHALLENGE_TOKEN_TYPE,
    MFA_ENROLLMENT_TOKEN_TYPE,
    create_access_token,
    create_mfa_token,
    create_refresh_token,
)
from app.core.session_tokens import SessionTokenRefused, authenticate_access_token
from app.models.postgres import UserRole
from app.routers import live as live_router
from app.routers import stream as stream_router
from tests.conftest import FakeExecuteResult

pytestmark = pytest.mark.regression

PROJECT_ID = str(uuid.uuid4())


# ── fakes ────────────────────────────────────────────────────────────────────


def _account(**overrides):
    values = dict(
        id=uuid.uuid4(),
        username="stream-test-account",
        is_active=True,
        must_change_password=False,
        role=UserRole.ADMIN,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeDb:
    """Answers each ``execute`` with the next scripted scalar."""

    def __init__(self, *scalars):
        self._scalars = list(scalars)
        self.executed = 0

    async def execute(self, _statement):
        self.executed += 1
        value = self._scalars.pop(0) if self._scalars else None
        return FakeExecuteResult(scalar_value=value)


def _session_factory(db):
    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


class _FakeRequest:
    headers: dict = {}

    async def is_disconnected(self):
        return False


@pytest.fixture
def revocation(monkeypatch):
    """The revocation store, answering "not revoked" unless a test says otherwise."""
    state = SimpleNamespace(revoked_jti=False, before_cutoff=False, unavailable=False)

    async def _is_jti_revoked(_jti):
        if state.unavailable:
            raise token_revocation.RevocationUnavailable("store down", store="database")
        return state.revoked_jti

    async def _is_token_before_cutoff(_uid, _iat):
        if state.unavailable:
            raise token_revocation.RevocationUnavailable("store down", store="database")
        return state.before_cutoff

    monkeypatch.setattr(token_revocation, "is_jti_revoked", _is_jti_revoked)
    monkeypatch.setattr(token_revocation, "is_token_before_cutoff", _is_token_before_cutoff)
    return state


# Every case: (name, token factory, account overrides or None for "no such
# account", revocation state, expected HTTP status, expected reason).
def _access(account_id):
    return create_access_token(account_id)


def _refresh(account_id):
    return create_refresh_token(account_id)[0]


def _mfa_challenge(account_id):
    return create_mfa_token(account_id, MFA_CHALLENGE_TOKEN_TYPE)[0]


def _mfa_enroll(account_id):
    return create_mfa_token(account_id, MFA_ENROLLMENT_TOKEN_TYPE)[0]


def _expired(account_id):
    return create_access_token(
        account_id,
        expires_delta=timedelta(minutes=5),
        issued_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )


def _garbage(_account_id):
    return "not-a-token"


REFUSALS = [
    ("refresh token", _refresh, {}, {}, 401, session_tokens.REASON_WRONG_TOKEN_TYPE),
    ("mfa challenge token", _mfa_challenge, {}, {}, 401, session_tokens.REASON_WRONG_TOKEN_TYPE),
    ("mfa enrolment token", _mfa_enroll, {}, {}, 401, session_tokens.REASON_WRONG_TOKEN_TYPE),
    ("garbage", _garbage, {}, {}, 401, session_tokens.REASON_INVALID_TOKEN),
    ("expired access token", _expired, {}, {}, 401, session_tokens.REASON_EXPIRED_TOKEN),
    ("revoked jti", _access, {}, {"revoked_jti": True}, 401, session_tokens.REASON_REVOKED_TOKEN),
    ("issued before the cutoff", _access, {}, {"before_cutoff": True}, 401, session_tokens.REASON_REVOKED_TOKEN),
    ("revocation store down", _access, {}, {"unavailable": True}, 503, session_tokens.REASON_REVOCATION_UNAVAILABLE),
    ("no such account", _access, None, {}, 401, session_tokens.REASON_USER_NOT_FOUND),
    ("deactivated account", _access, {"is_active": False}, {}, 403, session_tokens.REASON_INACTIVE_USER),
    ("temporary password", _access, {"must_change_password": True}, {}, 403, session_tokens.REASON_CHANGE_REQUIRED),
]
REFUSAL_IDS = [case[0] for case in REFUSALS]


def _arrange(case, revocation):
    """Build (token, db, account) for one refusal case and set the store state."""
    _name, make_token, overrides, store_state, _status, _reason = case
    account = _account(**(overrides or {}))
    for key, value in store_state.items():
        setattr(revocation, key, value)
    db = _FakeDb(None if overrides is None else account)
    return make_token(account.id), db, account


# ── the helper ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
async def test_the_helper_refuses(case, revocation):
    token, db, _account_row = _arrange(case, revocation)

    with pytest.raises(SessionTokenRefused) as refused:
        await authenticate_access_token(token, db)

    assert refused.value.status_code == case[4]
    assert refused.value.reason == case[5]


@pytest.mark.asyncio
async def test_the_helper_accepts_a_fresh_access_token(revocation):
    account = _account()
    token = create_access_token(account.id)

    found, expires_at = await authenticate_access_token(token, _FakeDb(account))

    assert found is account
    assert expires_at > datetime.now(timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_a_wrong_type_token_is_refused_before_the_database_is_read(revocation):
    db = _FakeDb(_account())

    with pytest.raises(SessionTokenRefused):
        await authenticate_access_token(_refresh(uuid.uuid4()), db)

    assert db.executed == 0


# ── the SSE route ────────────────────────────────────────────────────────────


@pytest.fixture
def sse_backends(monkeypatch):
    """Stand-ins for the Redis reads the SSE bootstrap makes after authentication."""
    from contextlib import asynccontextmanager

    from app.streams import live_fanout, live_run_state

    @asynccontextmanager
    async def _lock(_project_id):
        yield

    async def _latest(_project_id):
        return "0-0"

    async def _active():
        return []

    monkeypatch.setattr(live_fanout, "project_delivery_lock", _lock)
    monkeypatch.setattr(live_fanout, "latest_live_sequence", _latest)
    monkeypatch.setattr(live_run_state.RedisLiveRunState, "get_all_active", staticmethod(_active))


def _use_db(monkeypatch, db):
    import app.db.postgres as postgres

    monkeypatch.setattr(postgres, "AsyncSessionLocal", _session_factory(db))
    monkeypatch.setattr(live_router, "AsyncSessionLocal", _session_factory(db))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
async def test_the_sse_route_refuses(case, revocation, sse_backends, monkeypatch):
    token, db, _account_row = _arrange(case, revocation)
    _use_db(monkeypatch, db)

    with pytest.raises(HTTPException) as refused:
        await stream_router.sse_stream(PROJECT_ID, _FakeRequest(), token)

    assert refused.value.status_code == case[4]
    assert PROJECT_ID not in stream_router._sse_subscribers


@pytest.mark.asyncio
async def test_the_sse_route_streams_for_a_fresh_access_token(revocation, sse_backends, monkeypatch):
    account = _account()
    _use_db(monkeypatch, _FakeDb(account))

    response = await stream_router.sse_stream(
        PROJECT_ID, _FakeRequest(), create_access_token(account.id)
    )
    try:
        assert response.media_type == "text/event-stream"
        first = await response.body_iterator.__anext__()
        assert json.loads(first.split("data: ", 1)[1])["type"] == "initial_state"
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_the_sse_route_still_refuses_a_non_member(revocation, sse_backends, monkeypatch):
    account = _account(role=UserRole.VIEWER)
    # Second query is the membership lookup: no row.
    _use_db(monkeypatch, _FakeDb(account, None))

    with pytest.raises(HTTPException) as refused:
        await stream_router.sse_stream(
            PROJECT_ID, _FakeRequest(), create_access_token(account.id)
        )

    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_an_open_sse_stream_ends_when_its_token_expires(revocation, sse_backends, monkeypatch):
    account = _account()
    _use_db(monkeypatch, _FakeDb(account))
    response = await stream_router.sse_stream(
        PROJECT_ID, _FakeRequest(), create_access_token(account.id)
    )
    body = response.body_iterator
    try:
        await body.__anext__()  # the initial state, while the token is valid

        # Thirteen hours on: past the 12-hour default access-token lifetime.
        later = datetime.now(timezone.utc).timestamp() + 13 * 3600
        monkeypatch.setattr(stream_router, "_now", lambda: later)

        last = await body.__anext__()
        assert json.loads(last.split("data: ", 1)[1])["type"] == "token_expired"
        with pytest.raises(StopAsyncIteration):
            await body.__anext__()
    finally:
        await body.aclose()
    assert PROJECT_ID not in stream_router._sse_subscribers


# ── the WebSocket seam ───────────────────────────────────────────────────────


@pytest.fixture
def every_project_accessible(monkeypatch):
    async def _all(_db, _account_row):
        return None  # ADMIN: no project filter

    monkeypatch.setattr(live_router, "get_accessible_project_ids", _all)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
async def test_the_websocket_seam_refuses(case, revocation, every_project_accessible, monkeypatch):
    token, db, _account_row = _arrange(case, revocation)
    _use_db(monkeypatch, db)

    found, exp_epoch, close = await live_router._validate_token_and_membership(
        token, uuid.UUID(PROJECT_ID)
    )

    assert found is None and exp_epoch is None
    assert close is not None
    code, reason = close
    # 4401 tells the client to sign in again; 1013 ("try again later") is used
    # only when the server could not check the token.
    assert code == (1013 if case[4] == 503 else 4401), reason
    assert reason


@pytest.mark.asyncio
async def test_the_websocket_seam_accepts_a_fresh_access_token(revocation, every_project_accessible, monkeypatch):
    account = _account()
    _use_db(monkeypatch, _FakeDb(account))

    found, exp_epoch, close = await live_router._validate_token_and_membership(
        create_access_token(account.id), uuid.UUID(PROJECT_ID)
    )

    assert close is None
    assert found is account
    assert exp_epoch > datetime.now(timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_the_websocket_seam_still_refuses_a_non_member(revocation, monkeypatch):
    account = _account(role=UserRole.VIEWER)
    _use_db(monkeypatch, _FakeDb(account))

    async def _none_accessible(_db, _account_row):
        return set()

    monkeypatch.setattr(live_router, "get_accessible_project_ids", _none_accessible)

    found, _exp, close = await live_router._validate_token_and_membership(
        create_access_token(account.id), uuid.UUID(PROJECT_ID)
    )

    assert found is None
    assert close is not None and close[0] == 4403


# ── parity with the HTTP dependencies ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REFUSALS, ids=REFUSAL_IDS)
async def test_the_http_dependencies_refuse_the_same_tokens_with_the_same_status(case, revocation):
    """The matrix above, through ``get_current_user`` then ``get_current_active_user``.

    If the two paths ever disagree on a case, one of them is wrong.
    """
    token, db, _account_row = _arrange(case, revocation)
    request = SimpleNamespace(method="GET", scope={"path": f"/api/v1/stream/sse/{PROJECT_ID}"})

    with pytest.raises(HTTPException) as refused:
        found = await deps.get_current_user(db=db, token=token)
        await deps.get_current_active_user(request, found)

    assert refused.value.status_code == case[4]


def _called_names(func) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


#: Every function the two HTTP dependencies call today, and what the stream
#: helper does about each. A call that is not listed here fails the test below.
_HTTP_PATH_CALLS = {
    # get_current_user
    "decode_token": "mirrored: decode_token(token, expected_type='access')",
    "is_jti_revoked": "mirrored",
    "is_token_before_cutoff": "mirrored",
    "unavailable_detail": "mirrored (the 503 message)",
    "_count_auth_failure": "mirrored (same counter)",
    "HTTPException": "n/a: the helper raises SessionTokenRefused with the same status",
    "UUID": "mirrored (sub must be a UUID)",
    "select": "mirrored (load the user row)",
    "where": "mirrored",
    "execute": "mirrored",
    "scalar_one_or_none": "mirrored",
    "get": "n/a: reading claims",
    "isinstance": "n/a",
    "float": "n/a",
    "str": "n/a",
    "_bind_api_key_grant": "n/a: request-local API-key bookkeeping",
    "_bind_credential_kind": "n/a: request-local bookkeeping",
    "_bind_api_key_project": "n/a: request-local API-key bookkeeping",
    # get_current_active_user (its user comes from get_current_user for a JWT)
    "Depends": "n/a",
    "refuse_until_password_changed": "mirrored (must_change_password refuses)",
    "routed_path": "n/a: a stream is never a password-change path",
    "_is_write": "n/a: scoped API keys only; a stream is opened with a JWT",
    "_route_takes_scoped_key_writes": "n/a: scoped API keys only",
    "_refuse_scoped_key_without_write_scope": "n/a: scoped API keys only",
    "_bind_project_role_floor": "n/a: records the role a WRITE needs; a stream only reads",
}


def test_a_new_check_in_the_http_dependencies_must_be_considered_for_streams():
    """Pins what ``get_current_user`` and ``get_current_active_user`` call.

    A failure here means one of them gained (or lost) a call. If it is a new
    refusal, add the same refusal to
    ``app.core.session_tokens.authenticate_access_token`` and a case to
    ``REFUSALS`` above; then list the call in ``_HTTP_PATH_CALLS``.
    """
    called = _called_names(deps.get_current_user) | _called_names(deps.get_current_active_user)

    unlisted = called - set(_HTTP_PATH_CALLS)
    assert not unlisted, (
        f"get_current_user / get_current_active_user now call {sorted(unlisted)}. "
        "Decide whether the live-stream token helper needs the same check "
        "(app/core/session_tokens.py), then list the call in _HTTP_PATH_CALLS."
    )
    helper_calls = _called_names(authenticate_access_token)
    for name in ("decode_token", "is_jti_revoked", "is_token_before_cutoff", "unavailable_detail"):
        assert name in helper_calls, f"the stream token helper no longer calls {name}"


def test_both_stream_entry_points_use_the_helper():
    assert "authenticate_access_token" in _called_names(stream_router.sse_stream)
    assert "authenticate_access_token" in _called_names(live_router._validate_token_and_membership)
