"""The WebSocket auth handshake — the live stream's security boundary, unrun.

``routers/live.py::_authenticate_ws`` validates the post-connect handshake for
``/ws/...``: the client's first frame must be
``{"type": "auth", "token": "<JWT>"}``. It returns a ``WsSession`` on success
and closes the socket on every failure, and its docstring is explicit that the
caller **must not send further frames** when it returns ``None``.

It had **never executed** — one hit line, the ``def``.

Every branch in it is a *reject* path, which is the worst place for untested
code: a rejection that accidentally returns a session puts an unauthenticated
client on a project's live test stream, and nothing raises. The six paths each
carry a distinct close code, and those codes are a contract with the SDK —
4401 tells a client its credentials are the problem, 4400 tells it the frame
was malformed and retrying with the same token will not help.

Only the socket is faked here. That is not a mock standing in for the subject:
the frame the client sends *is* the input, and closing *is* the observable
behaviour. Token validation is stubbed at its own seam
(``_validate_token_and_membership``), which has its own tests.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.routers import live as live_router
from app.routers.live import WS_AUTH_TIMEOUT_SECONDS, WsSession, _authenticate_ws

pytestmark = pytest.mark.regression

PROJECT_ID = str(uuid.uuid4())


class _FakeWebSocket:
    """Records closes; ``receive_text`` replays a scripted frame or raises."""

    def __init__(self, first_frame=None, *, raises: BaseException | None = None):
        self._frame = first_frame
        self._raises = raises
        self.closed: list[tuple[int, str]] = []
        self.receive_calls = 0

    async def receive_text(self):
        self.receive_calls += 1
        if self._raises is not None:
            raise self._raises
        return self._frame

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed.append((code, reason))


def _auth_frame(token: str = "a.jwt.token") -> str:
    return json.dumps({"type": "auth", "token": token})


@pytest.fixture
def accepts_token(monkeypatch):
    """Stub the token/membership seam as ACCEPTING, so the handshake's own
    branches are what the test exercises."""
    user = type("U", (), {"id": uuid.uuid4(), "username": "someone"})()

    async def _ok(token, project_uuid):
        return user, 1_900_000_000.0, None

    monkeypatch.setattr(live_router, "_validate_token_and_membership", _ok)
    return user


@pytest.fixture
def rejects_token(monkeypatch):
    """Stub it as REJECTING with a specific close code, the way an expired or
    non-member token does."""
    async def _no(token, project_uuid):
        return None, None, (4403, "Not a project member")

    monkeypatch.setattr(live_router, "_validate_token_and_membership", _no)


class TestTheHappyPath:
    @pytest.mark.asyncio
    async def test_a_valid_handshake_returns_a_session_and_leaves_the_socket_open(
        self, accepts_token
    ):
        ws = _FakeWebSocket(_auth_frame())

        session = await _authenticate_ws(ws, PROJECT_ID)

        assert isinstance(session, WsSession)
        assert session.user_id == accepts_token.id
        assert session.user_name == "someone"
        assert session.token_exp == 1_900_000_000.0
        assert ws.closed == [], "a successful handshake must not close the socket"

    @pytest.mark.asyncio
    async def test_a_valid_reconnect_cursor_is_preserved(self, accepts_token):
        ws = _FakeWebSocket(json.dumps({
            "type": "auth", "token": "a.jwt.token", "last_event_id": "42-7",
        }))

        session = await _authenticate_ws(ws, PROJECT_ID)

        assert session is not None
        assert session.last_event_id == "42-7"

    @pytest.mark.asyncio
    async def test_a_malformed_reconnect_cursor_is_rejected(self, accepts_token):
        ws = _FakeWebSocket(json.dumps({
            "type": "auth", "token": "a.jwt.token", "last_event_id": "../secret",
        }))

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed == [(4400, "Invalid last_event_id")]


class TestEveryRejectionClosesTheSocket:
    """The docstring's contract: "closes the socket and returns None on any
    failure". A path that returns None *without* closing leaks a half-open
    connection; one that closes but returns a session is an auth bypass."""

    @pytest.mark.asyncio
    async def test_a_silent_client_is_timed_out_with_4401(self, accepts_token):
        """The client connects and never sends the auth frame. Without the
        timeout the socket sits open indefinitely, unauthenticated."""
        ws = _FakeWebSocket(raises=asyncio.TimeoutError())

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed == [(4401, "Auth timeout")]

    @pytest.mark.asyncio
    async def test_malformed_json_is_rejected_with_4400(self, accepts_token):
        ws = _FakeWebSocket("this is not json{")

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed[0][0] == 4400

    @pytest.mark.asyncio
    async def test_a_frame_of_the_wrong_type_is_rejected(self, accepts_token):
        """Well-formed JSON carrying a different message type must not be
        treated as an auth attempt."""
        ws = _FakeWebSocket(json.dumps({"type": "subscribe", "token": "x"}))

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed[0][0] == 4400

    @pytest.mark.asyncio
    async def test_a_missing_token_is_rejected(self, accepts_token):
        ws = _FakeWebSocket(json.dumps({"type": "auth"}))

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed[0][0] == 4400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "token", [None, 123, {"jwt": "x"}, ["a"], True],
        ids=["null", "int", "dict", "list", "bool"],
    )
    async def test_a_non_string_token_is_rejected_before_validation(
        self, monkeypatch, token
    ):
        """The type check runs BEFORE the validator, so a non-string never
        reaches `decode_token`. Passing one through would turn a malformed
        frame into a crash inside the auth path."""
        called = {"n": 0}

        async def _spy(tok, project_uuid):
            called["n"] += 1
            return None, None, (4401, "nope")

        monkeypatch.setattr(live_router, "_validate_token_and_membership", _spy)
        ws = _FakeWebSocket(json.dumps({"type": "auth", "token": token}))

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed[0][0] == 4400
        assert called["n"] == 0, "a non-string token must not reach the validator"

    @pytest.mark.asyncio
    async def test_a_non_uuid_project_id_is_rejected_before_validation(
        self, monkeypatch
    ):
        """Checked before the token is validated, so a junk path parameter
        cannot spend a token lookup."""
        called = {"n": 0}

        async def _spy(tok, project_uuid):
            called["n"] += 1
            return None, None, (4401, "nope")

        monkeypatch.setattr(live_router, "_validate_token_and_membership", _spy)
        ws = _FakeWebSocket(_auth_frame())

        assert await _authenticate_ws(ws, "not-a-uuid") is None
        assert ws.closed[0] == (4400, "Invalid project_id")
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_the_validators_close_code_is_passed_through(self, rejects_token):
        """4401 vs 4403 is what tells a client whether to re-authenticate or
        give up. Collapsing them to one code would send a non-member into a
        retry loop it can never win."""
        ws = _FakeWebSocket(_auth_frame())

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed == [(4403, "Not a project member")]


class TestDisconnectDuringHandshake:
    @pytest.mark.asyncio
    async def test_a_client_that_disconnects_is_not_closed_again(self, accepts_token):
        """The socket is already gone. Calling close() on it would raise inside
        the auth path — which is why this branch returns without closing."""
        from starlette.websockets import WebSocketDisconnect

        ws = _FakeWebSocket(raises=WebSocketDisconnect(code=1001))

        assert await _authenticate_ws(ws, PROJECT_ID) is None
        assert ws.closed == []


class TestTheTimeoutIsBounded:
    def test_the_auth_window_is_short_and_finite(self):
        """An unbounded (or very long) auth window is a cheap way to hold
        sockets open unauthenticated. Pinned as a range rather than an exact
        value so tuning stays possible."""
        assert 0 < WS_AUTH_TIMEOUT_SECONDS <= 30

    @pytest.mark.asyncio
    async def test_only_one_frame_is_read_before_a_decision(self, accepts_token):
        """The handshake must not loop waiting for a valid frame — that would
        let a client stall the socket with junk indefinitely."""
        ws = _FakeWebSocket("not json")

        await _authenticate_ws(ws, PROJECT_ID)

        assert ws.receive_calls == 1
