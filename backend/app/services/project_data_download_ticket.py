"""Single-use browser download tickets for a project export (W37 F-W30-6, contract §2.4 / D-4).

A browser navigation carries no ``Authorization`` header, so it cannot fetch the
Bearer download. A caller who may download an export first asks for a ticket
(an authenticated POST), then navigates to the public download URL with the
ticket as a query parameter; the browser streams the archive straight to disk.

The ticket is (``services/invocation_stream.py`` is the precedent):

* short-lived: ``DOWNLOAD_TICKET_SECONDS`` (60), never past the export's
  ``expires_at``; an export with under a second left gets no ticket;
* single-use: redeemed with an atomic GETDEL;
* bound to one (job, user): the download re-checks THAT user's access to THAT job.

Only a SHA-256 of the ticket is stored, so reading Redis does not yield usable
tickets. Nothing here logs the ticket or anything derived from it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime
from typing import Any, Optional

from app.db.redis_client import get_redis
from app.services.project_export_vocab import DOWNLOAD_TICKET_SECONDS

_KEY = "testlookup:project-data-download:{digest}"
_ISSUE_ATTEMPTS = 3


class TicketUnavailable(Exception):
    """The ticket store cannot be reached (503 ``ticket_unavailable``)."""


class ExportExpiring(Exception):
    """Under one second of the export's life is left: no ticket (410 ``export_expired``)."""


def _key(ticket: str) -> str:
    return _KEY.format(digest=hashlib.sha256(ticket.encode("utf-8")).hexdigest())


def ticket_ttl(expires_at: datetime, *, now: datetime) -> int:
    """``min(DOWNLOAD_TICKET_SECONDS, whole seconds until expires_at)``; 0 when under a second is left."""
    left = int((expires_at - now).total_seconds())
    return max(0, min(DOWNLOAD_TICKET_SECONDS, left))


async def issue_download_ticket(
    job_id: uuid.UUID, user_id: Any, *, expires_at: datetime, now: datetime,
) -> tuple[str, int]:
    """A new ticket for ``job_id`` issued to ``user_id`` and its lifetime in seconds.

    Raises :class:`ExportExpiring` when the export has under a second left and
    :class:`TicketUnavailable` when the ticket store cannot take the ticket.
    """
    ttl = ticket_ttl(expires_at, now=now)
    if ttl < 1:
        raise ExportExpiring()
    payload = json.dumps({"job_id": str(job_id), "user_id": str(user_id)})
    try:
        redis = get_redis()
        for _ in range(_ISSUE_ATTEMPTS):
            ticket = secrets.token_urlsafe(32)
            if await redis.set(_key(ticket), payload, ex=ttl, nx=True):
                return ticket, ttl
    except Exception as exc:  # noqa: BLE001 -- no ticket store means no ticket, said as a 503
        raise TicketUnavailable() from exc
    raise TicketUnavailable()


async def redeem_download_ticket(ticket: str, job_id: uuid.UUID) -> Optional[uuid.UUID]:
    """Consume ``ticket``; the user it was issued to, only when it was live and issued for ``job_id``.

    Fails closed: an unreachable Redis, an unreadable payload or another job's
    ticket is ``None`` (and the ticket is spent either way).
    """
    try:
        raw = await get_redis().getdel(_key(ticket))
    except Exception:  # noqa: BLE001 -- no ticket store means no download, never an open one
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("job_id") != str(job_id):
        return None
    try:
        return uuid.UUID(str(data.get("user_id")))
    except ValueError:
        return None
