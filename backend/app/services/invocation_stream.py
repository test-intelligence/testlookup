"""Single-use stream tickets for agent-invocation progress (architecture E1.2, section 3.2).

A browser ``EventSource`` cannot send an ``Authorization`` header. So a caller
who may see an invocation first asks for a ticket, an authenticated POST, and
then opens the event stream with the ticket as a query parameter.

The ticket is:

* short-lived: ``AGENT_INVOKE_STREAM_TICKET_SECONDS``, 60 by default;
* single-use: redeemed with an atomic GETDEL;
* bound to one invocation: a ticket for one id does not open another's stream.

Only a SHA-256 of the ticket is stored, so reading Redis does not yield
usable tickets.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any

from app.core.config import settings
from app.db.redis_client import get_redis

_KEY = "testlookup:invocation-stream-ticket:{digest}"
_ISSUE_ATTEMPTS = 3


def _key(ticket: str) -> str:
    return _KEY.format(digest=hashlib.sha256(ticket.encode("utf-8")).hexdigest())


def ticket_ttl_seconds() -> int:
    return max(5, int(settings.AGENT_INVOKE_STREAM_TICKET_SECONDS))


async def issue_stream_ticket(invocation_id: uuid.UUID, user_id: Any) -> tuple[str, int]:
    """A new ticket for ``invocation_id`` and its lifetime in seconds."""
    ttl = ticket_ttl_seconds()
    payload = json.dumps({"invocation_id": str(invocation_id), "user_id": str(user_id)})
    redis = get_redis()
    for _ in range(_ISSUE_ATTEMPTS):
        ticket = secrets.token_urlsafe(32)
        if await redis.set(_key(ticket), payload, ex=ttl, nx=True):
            return ticket, ttl
    raise RuntimeError("could not allocate a unique stream ticket")


async def redeem_stream_ticket(ticket: str, invocation_id: uuid.UUID) -> bool:
    """Consume ``ticket``; True only when it was live and issued for ``invocation_id``.

    Fails closed: an unreachable Redis or an unreadable payload is a refusal.
    """
    try:
        raw = await get_redis().getdel(_key(ticket))
    except Exception:  # noqa: BLE001 -- no ticket store means no stream, never an open one
        return False
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("invocation_id") == str(invocation_id)
