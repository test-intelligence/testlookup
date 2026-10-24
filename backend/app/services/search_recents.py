"""A person's recent searches, kept in Redis (W12 SRCH-03).

One list per user at ``search:recent:{user_id}``: the newest
``RECENT_MAX`` (20) searches, newest first, each a small JSON object
``{q, scope, mode, project_id, result_count, counts_exact, ts}`` (``mode`` is
the mode that ANSWERED: a semantic search that fell back stores
``keyword``). The key expires ``RECENT_TTL_DAYS`` (30) days after the last
write. Written only after a /search page search the page asked to record
(``record=true``, ``routers/search.py``'s after-response writer); read by
``GET /search/recent``, which filters each entry against the reader's CURRENT
projects (a GET never deletes); cleared by ``DELETE /search/recent``.

This is the ONE place the raw query text is kept server-side (besides the
searches a person chose to save): per user, bounded, expiring, cleared on
request -- and never logged. A Redis fault is logged by its exception TYPE
only and never raised into a search (``push_recent`` / ``read_recents``);
only the explicit clear reports it (``RecentsUnavailable``: the DELETE 503s).

No database session: nothing here touches Postgres.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Mapping, Optional, cast

import structlog

from app.core.search_vocab import (
    MAX_QUERY_CHARS,
    RECENT_KEY_FMT,
    RECENT_MAX,
    RECENT_TTL_DAYS,
    SEARCH_MODES,
    SEARCH_SCOPES,
)
from app.db.redis_client import get_redis
from app.services.search_usage_service import normalize_query

logger = structlog.get_logger("services.search_recents")

#: The list's TTL, refreshed on every write (30 days).
RECENT_TTL_SECONDS = RECENT_TTL_DAYS * 24 * 60 * 60


class RecentsUnavailable(RuntimeError):
    """Redis could not clear the list (the DELETE answers 503)."""


def recent_key(user_id: str) -> str:
    return RECENT_KEY_FMT.format(user_id=user_id)


def _project(value: Any) -> tuple[bool, Optional[str]]:
    if value is None or value == "":
        return True, None
    try:
        return True, str(uuid.UUID(str(value)))
    except ValueError:
        return False, None


def _timestamp(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def normalise_entry(entry: Mapping[str, Any]) -> Optional[dict]:
    """``entry`` in the stored shape, or ``None`` when any field is malformed."""
    q = entry.get("q")
    if not isinstance(q, str) or not q.strip() or len(q.strip()) > MAX_QUERY_CHARS:
        return None
    scope, mode = entry.get("scope"), entry.get("mode")
    if scope not in SEARCH_SCOPES or mode not in SEARCH_MODES:
        return None
    ok, project_id = _project(entry.get("project_id"))
    count = entry.get("result_count")
    if not ok or isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    exact = entry.get("counts_exact", True)
    ts = _timestamp(entry.get("ts"))
    if not isinstance(exact, bool) or ts is None:
        return None
    return {
        "q": q.strip(),
        "scope": scope,
        "mode": mode,
        "project_id": project_id,
        "result_count": count,
        "counts_exact": exact,
        "ts": ts,
    }


def _decode(raw: Any) -> Optional[dict]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return normalise_entry(value) if isinstance(value, dict) else None


def _identity(entry: Mapping[str, Any]) -> tuple:
    return (normalize_query(entry["q"]), entry["scope"], entry["mode"], entry["project_id"])


async def push_recent(user_id: str, entry: Mapping[str, Any]) -> bool:
    """Put ``entry`` first in the user's list (an equal search moves up); True when written.

    ``LRANGE`` the list, drop the entry with the same normalised query, scope,
    mode and project, keep the newest 20, then one transactional pipeline:
    ``DEL``, ``RPUSH`` every entry, ``EXPIRE`` 30 days. Never raises.
    """
    new = normalise_entry(entry)
    if new is None:
        return False
    key = recent_key(user_id)
    try:
        redis = get_redis()
        stored = await cast(Awaitable[list[Any]], redis.lrange(key, 0, RECENT_MAX - 1))
        rest = [e for e in (_decode(raw) for raw in stored) if e is not None]
        identity = _identity(new)
        entries = [new, *(e for e in rest if _identity(e) != identity)][:RECENT_MAX]
        pipe = redis.pipeline(transaction=True)
        pipe.delete(key)
        pipe.rpush(key, *(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries))
        pipe.expire(key, RECENT_TTL_SECONDS)
        await pipe.execute()
        return True
    except Exception as exc:  # noqa: BLE001 -- a recent is best effort, never a failed search
        logger.warning("search_recents_unavailable", error_type=type(exc).__name__)
        return False


async def read_recents(user_id: str) -> list[dict]:
    """The user's stored entries, newest first (malformed ones skipped); ``[]`` on a fault."""
    try:
        stored = await cast(Awaitable[list[Any]], get_redis().lrange(recent_key(user_id), 0, RECENT_MAX - 1))
    except Exception as exc:  # noqa: BLE001 -- the GET then says nothing is stored
        logger.warning("search_recents_unavailable", error_type=type(exc).__name__)
        return []
    return [e for e in (_decode(raw) for raw in stored or ()) if e is not None]


async def clear_recents(user_id: str) -> None:
    """Delete the user's list; ``RecentsUnavailable`` when Redis cannot."""
    try:
        await get_redis().delete(recent_key(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("search_recents_unavailable", error_type=type(exc).__name__)
        raise RecentsUnavailable("recent searches could not be cleared") from exc
