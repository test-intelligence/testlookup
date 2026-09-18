"""``Idempotency-Key`` for agent invocations (architecture E1.3, section 3.1).

A client that retries ``POST /api/v1/agents/{agent_id}/invoke`` after a timeout
must not start the agent twice. With an ``Idempotency-Key`` header:

* same key and same request -> the invocation that key created, with 200, even if
  it has since failed. Failures are returned, not re-run; the retry route is the
  way to try again;
* same key and a different request -> 422;
* same key while the first request is still being handled -> 409.

The key is scoped to the calling user, the project and the route, and the
request fingerprint covers the whole body, including ``project_id``. So a key
never resolves across users or projects: a key copied into a shared Postman
collection cannot read someone else's invocation.

Two layers hold that together:

* a Redis lock (``pending:<fingerprint>``, then ``done:<fingerprint>:<id>``) gives
  the in-flight 409;
* a unique index on ``agent_invocations (requested_by, project_id, agent_id,
  idempotency_key)`` is the authority. When Redis is unavailable the lock is
  skipped, and a concurrent duplicate in the same route scope loses at the
  index instead.

Deviation from section 3.1: the stored invocation keeps answering for its key
after the lock's 24 h, because the unique index does not expire. A key is never
reusable for a different request, which is the safer reading of the contract.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import structlog

from app.db.redis_client import get_redis

logger = structlog.get_logger("services.invocation_idempotency")

#: How long the in-flight lock, and the ``done`` marker that replaces it, live.
LOCK_TTL_SECONDS = 24 * 60 * 60

ClaimState = Literal["claimed", "in_flight", "conflict", "done", "unavailable"]


def request_fingerprint(agent_id: str, body: dict[str, Any]) -> str:
    """SHA-256 of the canonical request: the agent plus the whole JSON body."""
    canonical = json.dumps(
        {"agent_id": agent_id, "body": body}, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lock_key(user_id: Any, project_id: Any, agent_id: str, key: str) -> str:
    """The Redis key for one (user, project, route, Idempotency-Key). The key itself is hashed."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"testlookup:idempotency:{user_id}:{project_id}:POST:/api/v1/agents/{agent_id}/invoke:{digest}"


def _text(raw: Any) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


async def claim(user_id: Any, project_id: Any, agent_id: str, key: str, fingerprint: str) -> ClaimState:
    """Take the in-flight lock for this key, or report who holds it.

    ``claimed``: this request owns the key. ``in_flight``: the same request is
    still being handled. ``done``: the same request finished, so read its
    invocation back. ``conflict``: the key was used for a different request.
    ``unavailable``: no lock, so the database index is the only guard.
    """
    lock = lock_key(user_id, project_id, agent_id, key)
    try:
        redis = get_redis()
        if await redis.set(lock, f"pending:{fingerprint}", ex=LOCK_TTL_SECONDS, nx=True):
            return "claimed"
        raw = await redis.get(lock)
    except Exception as exc:  # noqa: BLE001 -- no lock is degraded, not broken: the index still guards
        logger.warning("idempotency_lock_unavailable", error_type=type(exc).__name__)
        return "unavailable"
    if raw is None:
        # Expired between SET NX and GET. The index still guards the key.
        return "unavailable"
    state, _, rest = _text(raw).partition(":")
    if rest.split(":", 1)[0] != fingerprint:
        return "conflict"
    return "done" if state == "done" else "in_flight"


async def complete(
    user_id: Any, project_id: Any, agent_id: str, key: str, fingerprint: str, invocation_id: Any,
) -> None:
    """Mark the key done once its invocation is committed. Never raises."""
    lock = lock_key(user_id, project_id, agent_id, key)
    try:
        await get_redis().set(lock, f"done:{fingerprint}:{invocation_id}", xx=True, keepttl=True)
    except Exception as exc:  # noqa: BLE001 -- the committed row already answers for the key
        logger.warning("idempotency_lock_not_completed", error_type=type(exc).__name__)


async def release(user_id: Any, project_id: Any, agent_id: str, key: str, fingerprint: str) -> None:
    """Drop a lock this request still holds as pending, so the client can retry. Never raises."""
    lock = lock_key(user_id, project_id, agent_id, key)
    try:
        redis = get_redis()
        raw = await redis.get(lock)
        if raw is not None and _text(raw) == f"pending:{fingerprint}":
            await redis.delete(lock)
    except Exception as exc:  # noqa: BLE001 -- the lock expires on its own
        logger.warning("idempotency_lock_not_released", error_type=type(exc).__name__)
