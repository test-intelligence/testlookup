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
from typing import Optional, Set

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
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    get_accessible_project_ids,
    get_streaming_api_key_context,
)
from app.core.security import decode_token
from app.db.postgres import AsyncSessionLocal, get_db
from app.models.postgres import User
from app.services.access_audit_service import log_access_change

logger = logging.getLogger(__name__)

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

router = APIRouter(prefix="/ws", tags=["Live Reporting"])

# ── Connection manager ─────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket connections per project channel with connection limits."""

    def __init__(self):
        # project_id -> set of WebSocket connections
        self._channels: dict[str, Set[WebSocket]] = {}

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

    def register(self, project_id: str, websocket: WebSocket) -> None:
        """Register an already-accepted, authenticated socket in the broadcast channel."""
        if project_id not in self._channels:
            self._channels[project_id] = set()
        self._channels[project_id].add(websocket)
        self._publish_active_total()
        logger.info(f"WS connect: project={project_id} total={len(self._channels[project_id])}")

    def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        channel = self._channels.get(project_id, set())
        channel.discard(websocket)
        if not channel:
            self._channels.pop(project_id, None)
        self._publish_active_total()
        logger.info(f"WS disconnect: project={project_id}")

    async def broadcast(self, project_id: str, message: dict) -> None:
        """Send a message to all connected clients with per-send timeout to drop dead connections."""
        channel = self._channels.get(project_id, set())
        if not channel:
            return
        dead: Set[WebSocket] = set()
        payload = json.dumps(message)
        timeout = settings.WS_BROADCAST_TIMEOUT

        async def _send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug(f"ws_broadcast_timeout project_id={project_id}")
                dead.add(ws)
            except (RuntimeError, ConnectionError):
                # RuntimeError: WebSocket already in CLOSED state.
                # ConnectionError: peer vanished mid-send.
                dead.add(ws)
            except Exception as exc:  # noqa: BLE001 — defensive: never let one dead socket poison gather
                logger.warning(
                    f"ws_broadcast_failed project_id={project_id}: {exc}"
                )
                dead.add(ws)

        await asyncio.gather(*[_send(ws) for ws in list(channel)], return_exceptions=True)
        for ws in dead:
            try:
                await asyncio.wait_for(
                    ws.close(code=1013, reason="Live stream delivery timeout"),
                    timeout=timeout,
                )
            except Exception:
                pass
            self.disconnect(project_id, ws)

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

    return WsSession(
        user_id=user.id,
        user_name=user.username,
        token_exp=exp_epoch,
        last_event_id=last_event_id,
    )


async def _audit_ws_event(
    action: str,
    user_id: Optional[_uuid.UUID],
    user_name: Optional[str],
    project_uuid: Optional[_uuid.UUID],
    detail: Optional[dict] = None,
) -> None:
    """Best-effort audit write — never let an audit failure break the WS."""
    try:
        async with AsyncSessionLocal() as db:
            entry_actor = None
            if user_id is not None:
                # Build a lightweight stand-in so log_access_change can pull id/username
                entry_actor = await db.get(User, user_id)
            await log_access_change(
                db,
                action=action,
                actor=entry_actor,
                project_id=project_uuid,
                after_value=detail,
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — audit is fire-and-forget
        logger.debug(f"ws_audit_skipped action={action}: {exc}")


# ── WebSocket endpoint ─────────────────────────────────────────────────────

@router.websocket("/live/{project_id}")
async def live_updates(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for real-time test run updates.

    Auth handshake (required, ~10s timeout):
        client → ``{"type":"auth","token":"<JWT access token>"}``

    On success, server sends ``{"type":"connected", ...}``.
    On failure, server closes with a ``4xxx`` code and a reason.

    Message types sent to client:
    - connected: Auth successful, subscription active
    - run_started / run_updated / run_completed: lifecycle of a test run
    - test_failed / ai_analysis_ready: per-test events
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

    # Audit the connect — non-blocking, best-effort
    try:
        project_uuid_for_audit = _uuid.UUID(project_id)
    except ValueError:
        project_uuid_for_audit = None
    await _audit_ws_event(
        "ws_connect",
        session.user_id,
        session.user_name,
        project_uuid_for_audit,
        detail={"channel": "live"},
    )

    close_reason_for_audit = "client_disconnect"

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "project_id": project_id,
            "user_id": str(session.user_id),
            "message": "Subscribed to live test updates",
        })

        from app.streams.live_fanout import (
            latest_live_sequence,
            project_delivery_lock,
            replay_live_notifications,
        )
        from app.streams.live_run_state import RedisLiveRunState

        # The local relay takes this same lock before broadcasting. Capture a
        # high-water mark, bootstrap through it, and only then register the
        # socket; later events wait and are delivered after bootstrap.
        async with asyncio.timeout(settings.WS_BROADCAST_TIMEOUT):
            async with project_delivery_lock(project_id):
                high_water = await latest_live_sequence(project_id)
                active = await RedisLiveRunState.get_all_active()
                project_sessions = [s for s in active if s.get("project_id") == project_id]
                if session.last_event_id:
                    replay, reconcile = await replay_live_notifications(
                        project_id, session.last_event_id, through_id=high_water,
                    )
                    if reconcile:
                        await websocket.send_json({
                            "type": "reconcile_required",
                            "sessions": project_sessions,
                            "sequence_id": high_water,
                        })
                    else:
                        for event in replay:
                            await websocket.send_json(event)
                else:
                    await websocket.send_json({
                        "type": "initial_state",
                        "sessions": project_sessions,
                        "sequence_id": high_water,
                    })
                manager.register(project_id, websocket)

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
        await _audit_ws_event(
            "ws_disconnect",
            session.user_id,
            session.user_name,
            project_uuid_for_audit,
            detail={"channel": "live", "reason": close_reason_for_audit},
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
    # Names the auto-created live session (re-audit N14). The key's own name
    # when the full check ran; a credential-cache hit does not carry it.
    api_key_name = "ws-events"
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
        )
        if outcome.staged:
            # The stream router's order: commit what was staged, then finalise
            # Redis. A close that fails to commit must not be finalised there.
            await db.commit()
            await after_commit(outcome)
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
    # its run_start found no state and was dropped. RedisLiveRunState.start is
    # idempotent and never resets counters, so the consumer's later call is a
    # no-op rather than a wipe.
    from app.streams.live_run_state import RedisLiveRunState

    if event_type == "run_start":
        await RedisLiveRunState.start(
            run_id,
            str(safe_event.get("project_id")),
            str(safe_event.get("build_number") or run_id),
            _as_count(safe_event.get("total_tests")),
        )
    elif event_type == "test_result":
        await RedisLiveRunState.record_test_event(
            run_id,
            str(safe_event.get("status") or "UNKNOWN"),
            str(safe_event.get("test_name") or ""),
            total_tests=_as_count(safe_event.get("total_tests")),
        )

    # Enqueue to Redis Stream — returns immediately regardless of processing load
    await publish_live_event(run_id, safe_event)
    return {"accepted": True, "run_id": run_id, "event_type": event_type}


def _as_count(value) -> int:
    """A client-supplied count, or 0. Never lets a bad value 500 the ingest."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
