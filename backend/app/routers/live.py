"""
Live test reporting via WebSocket.
Clients subscribe to a project channel and receive real-time updates
when new test runs are ingested or test case statuses change.
"""
import asyncio
import hmac
import json
import logging
import re
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Set

import structlog

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    get_accessible_project_ids,
    get_streaming_api_key_context,
)
from app.core.security import JWTError, decode_token
from app.db.postgres import AsyncSessionLocal, get_db
from app.models.postgres import User
from app.services import ws_audit_policy
from app.services.access_audit_service import log_access_change
from app.streams import live_frames
from app.streams.live_frames import LIVE_PROTOCOL_BATCH, LIVE_PROTOCOL_LEGACY, LiveFramePacer

logger = logging.getLogger(__name__)
_slog = structlog.get_logger(__name__)

# WS auth handshake timeout — clients must send {"type": "auth", "token": "..."}
# within this window of opening the socket, or we close the connection.
WS_AUTH_TIMEOUT_SECONDS = 10.0

# Grace period after token expiry before we close the socket — gives a
# well-behaved client time to send {"type":"refresh","token":"<new>"}.
WS_TOKEN_GRACE_SECONDS = 5.0


@dataclass
class WsSession:
    """Per-connection auth state held for the lifetime of the WebSocket."""
    user_id: _uuid.UUID
    user_name: Optional[str]
    token_exp: float  # epoch seconds; 0 means "no exp claim"
    last_event_id: Optional[str] = None
    # W25e: the frame protocol the client asked for in its auth frame (2 = live_batch frames).
    protocol: int = LIVE_PROTOCOL_LEGACY

router = APIRouter(prefix="/ws", tags=["Live Reporting"])

# The monotonic clock's tick (15.6 ms on Windows, ~1 ns on Linux). A flush timer is
# scheduled one tick late so the event loop, which runs a timer up to one tick early,
# never wakes it before the pacer's interval has elapsed.
_CLOCK_TICK_SECONDS = time.get_clock_info("monotonic").resolution


@dataclass
class _PacedSocket:
    """One registered socket's pacer and its pending flush (W25e E-2, contract §1.2)."""
    project_id: str
    pacer: LiveFramePacer
    flush_handle: Optional[asyncio.TimerHandle] = None
    flush_task: Optional[asyncio.Task[None]] = None
    # True while a data frame is being sent: one send at a time per socket. A send slower
    # than the interval keeps folding into the pacer instead of stacking concurrent sends.
    sending: bool = False

# ── Connection manager ─────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket connections per project channel with connection limits.

    W25e (E-2): every registered socket has a ``LiveFramePacer``; a socket receives at
    most one data frame per ``live_frames.FRAME_INTERVAL_MS``. The first frame after an
    idle interval is sent INLINE inside ``broadcast`` (byte-identical to the event, with
    the synchronous 1013 dead-socket handling); events inside the interval are folded and
    flushed by a per-socket timer. Control frames (``connected``, ``initial_state``, the
    bootstrap ``reconcile_required``, ``ping`` / ``pong`` / ``refreshed``) are sent by the
    endpoint directly and are never paced.
    """

    def __init__(self) -> None:
        # project_id -> set of WebSocket connections
        self._channels: dict[str, Set[WebSocket]] = {}
        # socket -> its pacer and pending flush (keyed by the socket object)
        self._paced: dict[Any, _PacedSocket] = {}

    async def accept(self, project_id: str, websocket: WebSocket) -> bool:
        """Accept the WS handshake if limits permit; do NOT yet register in the
        broadcast channel — caller must call ``register`` after authenticating.

        Limits are checked against the registered-channel count, so a flood of
        un-authenticated sockets cannot exceed the per-project cap once they
        time out within ``WS_AUTH_TIMEOUT_SECONDS``.
        """
        total = self.active_connections
        if total >= settings.WS_MAX_TOTAL_CONNECTIONS:
            await websocket.close(code=1008, reason="Server connection limit reached")
            logger.warning(
                f"ws_rejected_global_limit limit={settings.WS_MAX_TOTAL_CONNECTIONS}"
            )
            return False

        project_count = len(self._channels.get(project_id, set()))
        if project_count >= settings.WS_MAX_CONNECTIONS_PER_PROJECT:
            await websocket.close(code=1008, reason="Project connection limit reached")
            logger.warning(
                f"ws_rejected_project_limit project_id={project_id} "
                f"limit={settings.WS_MAX_CONNECTIONS_PER_PROJECT}"
            )
            return False

        await websocket.accept()
        return True

    def _publish_active_total(self) -> None:
        """Republish the live socket count. Never raises.

        ``websocket_connections_active`` was declared and never emitted, so the
        gauge read a flat 0 whether the live page had a thousand watchers or
        none. Set from the authoritative structure rather than incremented and
        decremented: a dropped socket that skips ``disconnect`` would otherwise
        leak the gauge upward for the life of the process, and a gauge that
        only ever climbs is worse than no gauge.
        """
        try:
            from app.core.metrics import websocket_connections_active

            websocket_connections_active.set(
                sum(len(channel) for channel in self._channels.values())
            )
        except Exception:  # noqa: BLE001 -- telemetry must never break the socket
            pass

    def register(
        self, project_id: str, websocket: WebSocket, protocol: int = LIVE_PROTOCOL_LEGACY
    ) -> None:
        """Register an already-accepted, authenticated socket in the broadcast channel.

        No I/O: creates the socket's pacer (``protocol`` 2 = ``live_batch`` frames).
        """
        if project_id not in self._channels:
            self._channels[project_id] = set()
        self._channels[project_id].add(websocket)
        previous = self._paced.pop(websocket, None)
        if previous is not None:
            self._cancel_flush(previous)
        self._paced[websocket] = _PacedSocket(project_id, LiveFramePacer(protocol))
        self._publish_active_total()
        logger.info(f"WS connect: project={project_id} total={len(self._channels[project_id])}")

    def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        channel = self._channels.get(project_id, set())
        channel.discard(websocket)
        if not channel:
            self._channels.pop(project_id, None)
        paced = self._paced.pop(websocket, None)
        if paced is not None:
            self._cancel_flush(paced)
        self._publish_active_total()
        logger.info(f"WS disconnect: project={project_id}")

    @staticmethod
    def _cancel_flush(paced: _PacedSocket) -> None:
        """Cancel the socket's flush timer and flush task (never the task calling this)."""
        if paced.flush_handle is not None:
            paced.flush_handle.cancel()
            paced.flush_handle = None
        task = paced.flush_task
        paced.flush_task = None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:  # no running loop (a synchronous caller)
                current = None
            if task is not current:
                task.cancel()

    def _paced_for(self, project_id: str, websocket: Any) -> _PacedSocket:
        paced = self._paced.get(websocket)
        if paced is None:  # defensive: a socket that reached a channel without register()
            paced = _PacedSocket(project_id, LiveFramePacer(LIVE_PROTOCOL_LEGACY))
            self._paced[websocket] = paced
        return paced

    async def _send_payload(self, project_id: str, websocket: Any, payload: str) -> bool:
        """One send with the per-send timeout; False when the socket is dead."""
        try:
            await asyncio.wait_for(
                websocket.send_text(payload), timeout=settings.WS_BROADCAST_TIMEOUT
            )
            return True
        except asyncio.TimeoutError:
            logger.debug(f"ws_broadcast_timeout project_id={project_id}")
        except (RuntimeError, ConnectionError):
            # RuntimeError: WebSocket already in CLOSED state.
            # ConnectionError: peer vanished mid-send.
            pass
        except Exception as exc:  # noqa: BLE001 — defensive: never let one dead socket poison gather
            logger.warning(
                f"ws_broadcast_failed project_id={project_id}: {type(exc).__name__}"
            )
        return False

    async def _drop_dead(self, project_id: str, websocket: Any) -> None:
        """Close a socket that failed a send with 1013 (the client reconnects) and forget it."""
        try:
            await asyncio.wait_for(
                websocket.close(code=1013, reason="Live stream delivery timeout"),
                timeout=settings.WS_BROADCAST_TIMEOUT,
            )
        except Exception:
            pass
        self.disconnect(project_id, websocket)

    def _schedule_flush(self, websocket: Any, paced: _PacedSocket) -> None:
        """Arm the socket's flush timer for when its pacer can hand out the next frame."""
        if paced.sending or paced.flush_handle is not None:
            return
        if paced.flush_task is not None and not paced.flush_task.done():
            return
        delay = paced.pacer.due_in()
        if delay is None:
            return
        paced.flush_handle = asyncio.get_running_loop().call_later(
            delay + _CLOCK_TICK_SECONDS, self._start_flush, websocket, paced
        )

    def _start_flush(self, websocket: Any, paced: _PacedSocket) -> None:
        paced.flush_handle = None
        if self._paced.get(websocket) is not paced:
            return  # disconnected (or re-registered) since the timer was armed
        paced.flush_task = asyncio.get_running_loop().create_task(self._flush(websocket))

    async def _flush(self, websocket: Any) -> None:
        """Timer-driven: take the pacer's frame → send (timeout) → 1013 on failure → re-arm."""
        paced = self._paced.get(websocket)
        if paced is None:
            return
        if paced.sending:  # an inline send is in flight; its completion re-arms the timer
            paced.flush_task = None
            return
        frame = paced.pacer.take_frame()
        if frame is not None:
            paced.sending = True
            try:
                sent = await self._send_payload(paced.project_id, websocket, json.dumps(frame))
            finally:
                paced.sending = False
            if not sent:
                await self._drop_dead(paced.project_id, websocket)
                return
        if self._paced.get(websocket) is paced:
            paced.flush_task = None
            self._schedule_flush(websocket, paced)

    async def broadcast(self, project_id: str, message: dict) -> None:
        """Offer a message to every socket of the channel; send what each pacer allows now.

        A socket whose slot is free and has nothing pending receives the message itself,
        inline, with the per-send timeout; a dead socket is closed 1013 and dropped here.
        A socket inside its interval folds the message and a timer flushes it later.
        """
        channel = self._channels.get(project_id, set())
        if not channel:
            return
        payload = json.dumps(message)  # today's encoding: the inline frame's bytes are unchanged
        sends: list[tuple[Any, _PacedSocket, str]] = []
        for ws in list(channel):
            paced = self._paced_for(project_id, ws)
            paced.pacer.offer(message)
            if paced.sending:
                continue  # the in-flight send re-arms the timer when it completes
            frame = paced.pacer.take_frame()
            if frame is None:
                self._schedule_flush(ws, paced)
                continue
            data = payload if frame is message else json.dumps(frame)
            paced.sending = True  # before any await: a concurrent caller buffers
            sends.append((ws, paced, data))
        if not sends:
            return
        try:
            results = await asyncio.gather(
                *(self._send_payload(project_id, ws, data) for ws, _paced, data in sends),
                return_exceptions=True,
            )
        finally:
            for _ws, paced, _data in sends:
                paced.sending = False
        for (ws, paced, _data), sent in zip(sends, results):
            if sent is True:
                if self._paced.get(ws) is paced:
                    self._schedule_flush(ws, paced)  # anything folded during the send
            else:
                await self._drop_dead(project_id, ws)

    async def send_replay(self, project_id: str, websocket: WebSocket, events: list[dict]) -> None:
        """The bootstrap replay, offered to the socket's pacer (register first).

        Protocol 2 receives one ``live_batch``; protocol 1 at most ``LEGACY_BACKLOG_MAX``
        paced legacy frames or ONE ``reconcile_required`` carrying ``dropped``.
        """
        if not events:
            return
        paced = self._paced_for(project_id, websocket)
        for event in events:
            paced.pacer.offer(event)
        if paced.sending:
            return
        frame = paced.pacer.take_frame()
        if frame is None:
            self._schedule_flush(websocket, paced)
            return
        paced.sending = True
        try:
            sent = await self._send_payload(project_id, websocket, json.dumps(frame))
        finally:
            paced.sending = False
        if not sent:
            await self._drop_dead(project_id, websocket)
            return
        if self._paced.get(websocket) is paced:
            self._schedule_flush(websocket, paced)

    async def broadcast_all(self, message: dict) -> None:
        """Broadcast to all connected clients across all projects."""
        for project_id in list(self._channels.keys()):
            await self.broadcast(project_id, message)

    @property
    def active_connections(self) -> int:
        return sum(len(v) for v in self._channels.values())


manager = ConnectionManager()


# ── Authentication ─────────────────────────────────────────────────────────

async def _validate_token_and_membership(
    token: str,
    project_uuid: _uuid.UUID,
) -> tuple[Optional[User], Optional[float], Optional[tuple[int, str]]]:
    """
    Decode a JWT, load the user, and check project access.

    Returns ``(user, exp_epoch, None)`` on success.
    Returns ``(None, None, (close_code, close_reason))`` on failure so the
    caller can close the socket with the right code.
    """
    try:
        payload = decode_token(token)
    except JWTError:
        return None, None, (4401, "Invalid token")

    if payload.get("type") != "access":
        return None, None, (4401, "Wrong token type")

    user_id = payload.get("sub")
    if not user_id:
        return None, None, (4401, "Missing sub claim")

    try:
        uid = _uuid.UUID(str(user_id))
    except ValueError:
        return None, None, (4401, "Invalid sub claim")

    exp_raw = payload.get("exp")
    exp_epoch: float = float(exp_raw) if isinstance(exp_raw, (int, float)) else 0.0

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if user is None or not user.is_active:
            return None, None, (4401, "User not found or inactive")

        accessible = await get_accessible_project_ids(db, user)
        if accessible is not None and project_uuid not in accessible:
            return None, None, (4403, "Not a member of this project")

    return user, exp_epoch, None


async def _authenticate_ws(
    websocket: WebSocket, project_id: str
) -> Optional[WsSession]:
    """
    Validate the post-connect auth handshake.

    Expected first client message: ``{"type": "auth", "token": "<JWT>"}``.
    Returns a ``WsSession`` on success; closes the socket and returns None on
    any failure. Caller must not send further frames if None.
    """
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=WS_AUTH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        await websocket.close(code=4401, reason="Auth timeout")
        return None
    except WebSocketDisconnect:
        return None

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.close(code=4400, reason="Malformed auth payload")
        return None

    if msg.get("type") != "auth" or not isinstance(msg.get("token"), str):
        await websocket.close(code=4400, reason="Expected {type:'auth',token:'...'}")
        return None

    try:
        project_uuid = _uuid.UUID(project_id)
    except ValueError:
        await websocket.close(code=4400, reason="Invalid project_id")
        return None

    user, exp_epoch, err = await _validate_token_and_membership(msg["token"], project_uuid)
    if err is not None or user is None or exp_epoch is None:
        code, reason = err if err is not None else (4401, "Auth failed")
        await websocket.close(code=code, reason=reason)
        return None

    last_event_id = msg.get("last_event_id")
    if last_event_id is not None and (
        not isinstance(last_event_id, str)
        or re.fullmatch(r"\d+-\d+", last_event_id) is None
    ):
        await websocket.close(code=4400, reason="Invalid last_event_id")
        return None

    # W25e: the frame protocol is opt-in and lenient — never a close for it. Only an int
    # >= 2 selects live_batch frames (a bool, a string or an absent value is legacy; a
    # future 3 gets this server's best, 2).
    requested = msg.get("protocol")
    protocol = (
        LIVE_PROTOCOL_BATCH
        if type(requested) is int and requested >= LIVE_PROTOCOL_BATCH
        else LIVE_PROTOCOL_LEGACY
    )

    return WsSession(
        user_id=user.id,
        user_name=user.username,
        token_exp=exp_epoch,
        last_event_id=last_event_id,
        protocol=protocol,
    )


# ── Session audit (HYG-06 a, D27 a): AT MOST ONE ws_session row per live session, append-only ─────


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _ws_session_detail(session: dict, started_at: float, ended_at: float, reason: str) -> dict:
    """The row's ``after_value``, written ONCE at the disconnect that took the session to 5 s.

    It carries that connection's timing (``started_at`` / ``ended_at`` / ``reason``), the session's start and
    the connected time accumulated so far, and states the reconnect rule as a fixed fact: the row is never
    updated, so it claims no reconnect count and no total duration it cannot know.
    """
    return {
        "channel": "live",
        "reason": reason,
        "started_at": _iso(started_at),
        "ended_at": _iso(max(ended_at, started_at)),
        "session_started_at": _iso(session["started_at"]),
        "connected_s_at_write": round(session["connected_ms"] / 1000),
        "reconnect_policy": ws_audit_policy.RECONNECT_POLICY,
    }


async def _read_ws_session(key: str) -> Optional[dict]:
    """The user's open session on this project, or None — a Redis error is "no session", never a raise."""
    try:
        from app.db import redis_client

        raw = await redis_client.get_redis().get(key)
        if not raw:
            return None
        stored = json.loads(raw)
        return stored if isinstance(stored, dict) else None
    except Exception as exc:  # noqa: BLE001 — no Redis = no collapse for this connection
        _slog.debug("ws_session_state_unread", error_type=type(exc).__name__)
        return None


async def _keep_ws_session(key: str, session: dict) -> None:
    """Best effort: without it the next reconnect starts a new session (one more row), nothing worse."""
    try:
        from app.db import redis_client

        await redis_client.get_redis().set(
            key, json.dumps(session), ex=ws_audit_policy.RECONNECT_WINDOW_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        _slog.debug("ws_session_state_unkept", error_type=type(exc).__name__)


async def _insert_ws_session_row(
    user_id: _uuid.UUID,
    project_uuid: Optional[_uuid.UUID],
    detail: dict,
) -> None:
    """INSERT the session's one row in a short session of its own, committed here; a DB error raises.

    A WebSocket has no request session, so this opens its own (as the connect / disconnect audit did) and
    commits it. Append-only: nothing in this module updates or deletes an audit row.
    """
    async with AsyncSessionLocal() as db:
        actor = await db.get(User, user_id)
        await log_access_change(
            db,
            action=ws_audit_policy.WS_AUDIT_ACTION,
            actor=actor,
            project_id=project_uuid,
            after_value=detail,
        )
        await db.commit()


def _count_ws_connection(outcome: str) -> None:
    """``testlookup_ws_connections_total{outcome}`` — one literal ``.inc()`` per outcome. Never raises."""
    try:
        from app.core.metrics import ws_connections_total

        if outcome == "audited":
            ws_connections_total.labels(outcome="audited").inc()
        elif outcome == "merged":
            ws_connections_total.labels(outcome="merged").inc()
        elif outcome == "short":
            ws_connections_total.labels(outcome="short").inc()
        else:
            ws_connections_total.labels(outcome="audit_failed").inc()
    except Exception:  # noqa: BLE001 — telemetry must never break the socket
        pass


async def _record_ws_session(
    user_id: _uuid.UUID,
    project_uuid: Optional[_uuid.UUID],
    started_at: float,
    ended_at: float,
    reason: str,
) -> str:
    """Record one finished live connection into the user's session on the project; returns the outcome.

    Replaces the old ``ws_connect`` + ``ws_disconnect`` pair (two ``access_audit_logs`` rows per connection).
    ``ws_audit_policy.decide`` reads the session kept in Redis (``SESSION_KEY``, TTL 60 s) and says: INSERT the
    session's one row now that its connected time reached 5 s (``audited``), nothing for a continuation of an
    already-audited session (``merged`` — only the Redis session moves), or nothing for a session still under
    5 s (``short``, metric only). Audit rows are append-only: no row is ever updated. A DB error is
    ``audit_failed`` (the session stays un-audited, so the next continuation tries again). Without Redis every
    connection of 5 s or more is its own session and row. Never raises into the socket; logs carry an
    exception TYPE — never a user name, a token or a URL.
    """
    key = ws_audit_policy.SESSION_KEY.format(user_id=user_id, project_id=project_uuid)
    prev = await _read_ws_session(key)
    decision = ws_audit_policy.decide(prev, started_at, ended_at)
    session = dict(decision.session)
    outcome = decision.outcome
    if decision.kind == "insert":
        try:
            detail = _ws_session_detail(session, started_at, ended_at, reason)
            await _insert_ws_session_row(user_id, project_uuid, detail)
            session["audited"] = True
        except Exception as exc:  # noqa: BLE001 — audit is best effort; the socket is already closing
            outcome = "audit_failed"
            _slog.debug("ws_session_audit_failed", error_type=type(exc).__name__)
    await _keep_ws_session(key, session)
    _count_ws_connection(outcome)
    return outcome


# ── WebSocket endpoint ─────────────────────────────────────────────────────

@router.websocket("/live/{project_id}")
async def live_updates(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for real-time test run updates.

    Auth handshake (required, ~10s timeout):
        client → ``{"type":"auth","token":"<JWT access token>"}``
        optional ``"last_event_id": "<ms>-<seq>"`` (reconnect replay) and
        ``"protocol": 2`` (opt in to ``live_batch`` frames; anything else is legacy).

    On success, server sends ``{"type":"connected", ..., "protocol": 1|2,
    "frame_interval_ms": 200}``.
    On failure, server closes with a ``4xxx`` code and a reason.

    Data frames are paced: at most one per ``frame_interval_ms`` per socket (W25e).

    Message types sent to client:
    - connected: Auth successful, subscription active
    - initial_state / reconcile_required: the bootstrap state (control, unpaced)
    - live_run_started / live_test_result / live_warning / live_run_complete,
      run_completed, pipeline_progress, …: data events. Protocol 1 receives them
      as themselves; several ``live_test_result`` / ``live_warning`` events of one
      run inside one interval arrive as the latest one plus ``"coalesced": n``.
    - live_batch (protocol 2 only): every data event folded inside one interval,
      in last-update order (``events``, ``counts``, the max ``sequence_id``); a lone
      event is sent as itself
    - reconcile_required with ``dropped``: the backlog overflowed — re-read the state
    - ping: Keep-alive heartbeat every 30s

    Message types received from client (post-auth):
    - ping: Client keep-alive (server responds with pong)
    """
    accepted = await manager.accept(project_id, websocket)
    if not accepted:
        return

    session = await _authenticate_ws(websocket, project_id)
    if session is None:
        # _authenticate_ws already closed the socket. Nothing registered yet.
        return

    # No audit row at connect (HYG-06 a): a session's one ws_session row is written at a disconnect.
    try:
        project_uuid_for_audit = _uuid.UUID(project_id)
    except ValueError:
        project_uuid_for_audit = None
    started_at = time.time()

    close_reason_for_audit = "client_disconnect"

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "project_id": project_id,
            "user_id": str(session.user_id),
            "message": "Subscribed to live test updates",
            "protocol": session.protocol,
            "frame_interval_ms": live_frames.FRAME_INTERVAL_MS,
        })

        from app.streams.live_fanout import (
            latest_live_sequence,
            project_delivery_lock,
            replay_live_notifications,
        )
        from app.streams.live_run_state import RedisLiveRunState

        # The local relay takes this same lock before broadcasting. Capture a
        # high-water mark, bootstrap through it, and register the socket inside
        # the lock; later events wait and are delivered after bootstrap. The
        # control frame goes out directly; a replay goes through the socket's
        # pacer (W25e), so it arrives paced like live frames.
        async with asyncio.timeout(settings.WS_BROADCAST_TIMEOUT):
            async with project_delivery_lock(project_id):
                high_water = await latest_live_sequence(project_id)
                active = await RedisLiveRunState.get_all_active()
                project_sessions = [s for s in active if s.get("project_id") == project_id]
                replay: list[dict] = []
                if session.last_event_id:
                    replay, reconcile = await replay_live_notifications(
                        project_id, session.last_event_id, through_id=high_water,
                    )
                    if reconcile:
                        replay = []
                        await websocket.send_json({
                            "type": "reconcile_required",
                            "sessions": project_sessions,
                            "sequence_id": high_water,
                        })
                else:
                    await websocket.send_json({
                        "type": "initial_state",
                        "sessions": project_sessions,
                        "sequence_id": high_water,
                    })
                manager.register(project_id, websocket, protocol=session.protocol)
                await manager.send_replay(project_id, websocket, replay)

        # Keep connection alive with ping/pong + periodic token-expiry checks.
        while True:
            # Expiry check runs on every iteration (cheap — just clock math).
            if session.token_exp and time.time() > session.token_exp + WS_TOKEN_GRACE_SECONDS:
                close_reason_for_audit = "token_expired"
                await websocket.close(code=4401, reason="Token expired — reconnect")
                break

            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "refresh" and isinstance(msg.get("token"), str):
                    # Client is renewing its access token — re-validate everything.
                    try:
                        proj_uuid = _uuid.UUID(project_id)
                    except ValueError:
                        close_reason_for_audit = "invalid_project_on_refresh"
                        await websocket.close(code=4400, reason="Invalid project_id")
                        break
                    user, exp_epoch, err = await _validate_token_and_membership(
                        msg["token"], proj_uuid
                    )
                    if err is not None or user is None or exp_epoch is None:
                        code, reason = err if err is not None else (4401, "Refresh failed")
                        close_reason_for_audit = f"refresh_failed:{reason}"
                        await websocket.close(code=code, reason=reason)
                        break
                    session.user_id = user.id
                    session.user_name = user.username
                    session.token_exp = exp_epoch
                    await websocket.send_json({"type": "refreshed", "exp": exp_epoch})
            except asyncio.TimeoutError:
                # Server-side heartbeat (and the next loop iteration re-checks expiry).
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        pass  # close_reason_for_audit stays "client_disconnect"
    except asyncio.TimeoutError:
        close_reason_for_audit = "bootstrap_timeout"
        try:
            await websocket.close(code=1013, reason="Live stream bootstrap timeout")
        except Exception:
            pass
    except Exception as e:
        close_reason_for_audit = f"server_error:{type(e).__name__}"
        logger.warning(f"WS error for project={project_id}: {e}")
    finally:
        manager.disconnect(project_id, websocket)
        await _record_ws_session(
            session.user_id,
            project_uuid_for_audit,
            started_at,
            time.time(),
            close_reason_for_audit,
        )


# ── Helper functions called from ingestion pipeline ───────────────────────

async def notify_run_started(project_id: str, run_id: str, build_number: str) -> None:
    from app.streams.live_fanout import publish_live_notification
    await publish_live_notification(project_id, {
        "type": "run_started",
        "run_id": run_id,
        "build_number": build_number,
    })


async def notify_run_completed(project_id: str, run_id: str, stats: dict) -> None:
    from app.streams.live_fanout import publish_live_notification
    await publish_live_notification(project_id, {
        "type": "run_completed",
        "run_id": run_id,
        **stats,
    })


async def notify_test_failed(project_id: str, run_id: str, test_id: str, test_name: str) -> None:
    from app.streams.live_fanout import publish_live_notification
    await publish_live_notification(project_id, {
        "type": "test_failed",
        "run_id": run_id,
        "test_id": test_id,
        "test_name": test_name,
    })


async def notify_ai_ready(project_id: str, test_id: str, confidence: int, category: str) -> None:
    from app.streams.live_fanout import publish_live_notification
    await publish_live_notification(project_id, {
        "type": "ai_analysis_ready",
        "test_id": test_id,
        "confidence_score": confidence,
        "failure_category": category,
    })


# ── Live execution event ingestion (HTTP) ──────────────────────────────────
# Called by test runners (e.g. pytest plugin, Allure listener) during execution


@router.post("/events/{run_id}", status_code=202)
async def ingest_live_event(
    run_id: str,
    event: dict = Body(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """
    Receive a live test execution event from a test runner.
    This endpoint is a thin producer — it enqueues the event to Redis Streams
    and returns 202 immediately (~1ms). The actual processing happens
    asynchronously in the LiveEventStreamConsumer background task.

    Supported event types:
      - run_start:    {type, project_id, build_number, total_tests}
      - test_result:  {type, test_name, status, duration_ms, error_message, test_case_id?}
      - run_complete: {type}

    Any event may carry an ``event_id`` (1-200 printable characters, unique
    within its run): a POST retried with the same one is counted once. Without
    one, ``run_start`` and ``run_complete`` are recognised by their content, and
    every ``test_result`` is a new result.

    With a project key the run is saved like an SDK run (re-audit N14). Its run
    id can be used again once the run completes: a ``run_start`` begins a new
    run under it, and any other event for the finished run is refused with 409.
    ``test_case_id``, and a ``test_result``'s ``total_tests``, are honoured only
    on the legacy path: the SDK stream's events carry neither.

    Authentication (re-audit H1). Preferred: a project-scoped ``X-API-Key``
    carrying the ``stream:write`` scope — the project is then derived from the
    key server-side and a caller cannot name someone else's. Legacy: the shared
    ``X-Webhook-Secret``, which authenticates a caller but names no tenant, so
    it could previously inject fabricated results into ANY project. Set
    ``LIVE_EVENTS_REQUIRE_PROJECT_KEY=true`` to refuse it outright.
    """
    from app.db.mongo import Collections, get_mongo_db
    from app.services.live_event_authz import (
        RunProjectBindingUnavailable,
        cached_streaming_project,
        remember_run_project,
        remember_streaming_project,
        resolve_run_project,
    )
    from app.services.ingestion_sanitization import (
        LIVE_SANITIZATION_VERSION,
        LIVE_SANITIZATION_VERSION_FIELD,
        sanitize_test_result_payload,
        validate_live_identifier,
    )
    from app.streams.producer import publish_live_event

    try:
        run_id = validate_live_identifier("run_id", run_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    # Re-audit M3: shed load before touching Redis at all. Every other step on
    # this route writes to Redis (the run binding, the credential cache, the
    # run's counters, the stream itself), and this was the one ingest path
    # with no backpressure gate, so it kept admitting events while Redis
    # approached OOM.
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure

    await enforce_redis_memory_backpressure()

    # ── Authenticate, and derive the tenant rather than trusting the body ──
    bound_project_id: Optional[str] = None
    # Names the auto-created live session (re-audit N14): the key's own name
    # when the full check ran. A credential-cache hit does not carry it, so it
    # stays None and the adapter looks it up if the event creates a session.
    api_key_name: Optional[str] = None
    if x_api_key:
        # Project-scoped, stream:write, hashed at rest. The project comes from
        # the key, so a caller cannot address another tenant at all.
        #
        # This endpoint is one event per call, so the full check (two SELECTs
        # plus a last_used_at UPDATE) cannot run per event against a pool of
        # three connections per worker. Verify properly on a miss, then cache
        # the ANSWER for a few seconds; a miss degrades to the full check, so
        # Redis being unavailable slows this path down, it does not open it.
        bound_project_id = await cached_streaming_project(x_api_key)
        if not bound_project_id:
            stream_ctx = await get_streaming_api_key_context(
                db=db, x_api_key=x_api_key
            )
            bound_project_id = str(stream_ctx.project_id)
            api_key_name = getattr(stream_ctx, "api_key_name", None) or api_key_name
            await remember_streaming_project(x_api_key, bound_project_id)
    elif x_webhook_secret:
        if settings.LIVE_EVENTS_REQUIRE_PROJECT_KEY:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=(
                    "This deployment requires a project-scoped API key for live "
                    "events. The shared webhook secret names no project."
                ),
            )
        if not hmac.compare_digest(
            str(x_webhook_secret), str(settings.WEBHOOK_SECRET)
        ):
            logger.warning("Live event rejected — invalid webhook secret")
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret"
            )
    else:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Live events require a project-scoped X-API-Key "
                "(or the legacy X-Webhook-Secret)."
            ),
        )

    # Re-audit M4: a per-project budget, charged once the project is known and
    # the caller authenticated -- never before, or anyone could spend another
    # tenant's quota. Single events have their own bucket; see
    # enforce_live_event_rate_limit. The legacy shared-secret path names no
    # project here and is off by default, so it is not charged.
    if bound_project_id:
        from app.services.ingestion_rate_limit import enforce_live_event_rate_limit

        await enforce_live_event_rate_limit(bound_project_id)

    event_type = event.get("type", "test_result")

    # ── Bind the run to one project, and keep it there ─────────────────────
    if event_type == "run_start":
        if bound_project_id:
            # Server-derived wins: a forged project_id in the body is ignored
            # rather than rejected, so a mis-set client cannot write elsewhere.
            #
            # This MUST run before the required-field check below. A caller
            # authenticating with a project-scoped key is told by this
            # endpoint's own documentation not to send project_id -- the whole
            # point is that the server decides it -- so validating the body
            # first rejected the correct client with a 400, on what is now the
            # only credential accepted by default.
            event = {**event, "project_id": bound_project_id}

        # Only the legacy shared-secret path still has to supply one, because
        # nothing else names a tenant for it.
        if not event.get("project_id"):
            raise HTTPException(
                400, detail="project_id required for run_start event"
            )
        opening_project = str(event.get("project_id"))
        try:
            owner = await remember_run_project(run_id, opening_project)
        except RunProjectBindingUnavailable as exc:
            # Fail closed: an unbound run has no cross-tenant check for its
            # whole life. 503 is retryable and tells the producer why.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cannot establish run ownership right now — retry",
            ) from exc
        if owner is not None and owner != opening_project:
            # run_id is caller-chosen, so two projects picking "build-42" is an
            # ordinary collision. Say so at the only moment the producer can
            # act on it: silently losing the bind used to leave every
            # subsequent event of this run 403-ing for 25 hours while the run
            # showed as started and permanently empty.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"run_id '{run_id}' is already in use by another project. "
                    "Use a run_id unique to this project."
                ),
            )
    elif bound_project_id:
        owner = await resolve_run_project(run_id)
        if owner is not None and owner != bound_project_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="This run belongs to a different project",
            )

    safe_event = sanitize_test_result_payload({**event, "run_id": run_id})
    safe_event.pop("_id", None)

    # Persist a sanitized event for the operational audit trail (non-critical).
    # Raw evidence belongs in the project-scoped artifact store, not this
    # shared live-events collection.
    try:
        mongo_db = get_mongo_db()
        await mongo_db[Collections.LIVE_EXECUTION_EVENTS].insert_one(
            {
                **safe_event,
                LIVE_SANITIZATION_VERSION_FIELD: LIVE_SANITIZATION_VERSION,
            }
        )
    except Exception as exc:  # noqa: BLE001 -- the audit copy must not block ingest
        # Non-fatal on purpose: the event is still published below, so the
        # run, the dashboard and the analysis are unaffected. What must not
        # happen is the gap going unrecorded (re-audit M10) -- this used to be
        # ``except Exception: pass``, so a Mongo outage erased the audit trail
        # for every live event in the window and left no trace that it had.
        from app.core.metrics import live_event_archive_failures_total

        live_event_archive_failures_total.inc()
        logger.warning(
            "live_event_archive_failed run_id=%s event_type=%s error=%r",
            run_id,
            event_type,
            exc,
        )

    # ── A project-key run is persisted like an SDK run (re-audit N14) ──────
    #
    # Everything below this block only ever put the event on the shared live
    # stream: no LiveSession, no TestRun, no persistence. A run streamed here
    # showed live and then vanished -- never in /runs, never analysed, never
    # finalised. An event sent with a project-scoped key now goes through the
    # SDK stream's own ingest path, which creates the session and its TestRun,
    # counts the result in its admission script, fans it out to the dashboard,
    # and on run_complete stages the persistence and finalisation every SDK
    # run gets. See services/ws_event_ingest.py.
    #
    # The legacy shared secret names no project and is refused by default, so
    # it keeps the old behaviour below: counted and shown live, never persisted.
    if bound_project_id:
        from app.services.ws_event_ingest import after_commit, ingest_one

        outcome = await ingest_one(
            db,
            project_id=_uuid.UUID(bound_project_id),
            api_key_name=api_key_name,
            run_id=run_id,
            event=safe_event,
            api_key=x_api_key,
        )
        if outcome.staged:
            # The stream router's order: commit what was staged, then finalise
            # Redis. A close that fails to commit must not be finalised there.
            await db.commit()
            if outcome.completes:
                # VIZ-212: run_complete closed the session in the staged
                # work, so the commit above wrote the run analytics reads.
                from app.services.cache_service import bump_analytics_epoch

                await bump_analytics_epoch(bound_project_id)
            await after_commit(outcome)
            if outcome.completes:
                # W25e E-1: publish the close's staged persist now instead of at
                # the next relay tick (never raises; the relay stays the safety net).
                from app.worker.downstream_dispatch import publish_staged_now

                await publish_staged_now(db, run_id=outcome.session_id)
        return {
            "accepted": True,
            "run_id": run_id,
            "event_type": event_type,
            "session_id": outcome.session_id,
        }

    # ── Count the result where it arrives (re-audit H6) ─────────────────────
    #
    # Since re-audit N14 only the legacy shared-secret path reaches this: a
    # project-key event returned above, and the SDK admission counted it.
    #
    # The consumer's result handler only READS the live-state counters and
    # broadcasts them; its own comment says the increment happens at ingest,
    # "in stream_service.ingest_event_batch()". That is true for the SDK batch
    # path, whose admission Lua recounts its ledger sets into this same hash.
    # It was never true here: nothing on this route touched the counters, and
    # RedisLiveRunState.record_test_event -- the method the consumer's own
    # docstring names for exactly this -- had no caller at all. A run streamed
    # through this endpoint showed zero results on the live dashboard, never
    # tripped the failure-rate early warning, and was finalised from counters
    # that were all zero.
    #
    # Counted here rather than in the consumer on purpose: batch events are
    # published into the same stream, so counting in the consumer would
    # double-count every SDK run. This route is the only one that needs it.
    #
    # run_start creates the state here too. The consumer creates it as well,
    # but asynchronously -- a result arriving before the consumer had processed
    # its run_start found no state and was dropped. This call resets a run id
    # being reused for a new run; the consumer's later call never resets, so it
    # is a no-op rather than a wipe.
    from app.streams.live_run_state import RedisLiveRunState

    if event_type == "run_start":
        # The producer's run_start begins a run: a caller reusing its run id
        # within the state's lifetime starts a NEW run, which must not inherit
        # the last one's counters or its "completed" status (code review of H6).
        await RedisLiveRunState.start(
            run_id,
            str(safe_event.get("project_id")),
            str(safe_event.get("build_number") or run_id),
            _as_count(safe_event.get("total_tests")),
            reset=True,
        )

    # Enqueue to Redis Stream — returns immediately regardless of processing load
    await publish_live_event(run_id, safe_event)

    if event_type == "test_result":
        # Counted only once published: counting first meant a failed publish --
        # a 500 the client retries -- counted the result twice (code review of
        # H6). The producer discards the state it would have read back.
        await RedisLiveRunState.record_test_event(
            run_id,
            str(safe_event.get("status") or "UNKNOWN"),
            str(safe_event.get("test_name") or ""),
            total_tests=_as_count(safe_event.get("total_tests")),
            return_state=False,
        )
    return {"accepted": True, "run_id": run_id, "event_type": event_type}


def _as_count(value) -> int:
    """A client-supplied count, or 0. Never lets a bad value 500 the ingest."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
