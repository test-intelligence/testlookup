"""Search usage without query text (W12 SRCH-05).

One ``search_query_events`` row per page-1 search with words that the /search
page runs with ``record=true`` (written after the response by
``routers/search.py``). What a row holds is :func:`build_event`'s dict: a KEYED
hash of the normalised query, its length, its word and filter counts, the
scope, the mode asked for and the mode that answered, the fallback reason, the
result count (and whether it is exact) and the latency. No query text, no user
id, no IP.

Why a keyed hash and not ``sha256(q)``: a plain digest of a short query
("checkout") is reversed by hashing a dictionary. :func:`query_hash` is an
HMAC-SHA256 whose key is ``sha256("testlookup-search-usage-v1:" +
APP_SECRET_KEY)`` (the ``core/deps.py`` membership-signature precedent: the
deployment secret keys it), so it counts repeats of one query and cannot be
reversed without the server's secret. Rotating ``APP_SECRET_KEY`` simply
starts a new hash domain.

Reads (:func:`usage_summary`) are ONE statement; "no row in the window" is
``measured: False`` with every rate ``None`` -- never ``0 %``.

Transaction ratchet: commit-free. :func:`purge_expired_batch` STAGES one
delete on the caller's session; the worker task commits each batch.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, any_, bindparam, delete, func, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.search_vocab import (
    USAGE_DAYS,
    USAGE_HASH_DOMAIN,
    USAGE_PURGE_BATCH,
    USAGE_RETENTION_DAYS,
)
from app.models.postgres import SearchQueryEvent

#: The SMALLINT columns' ceiling (q_length, word_count, filter_count).
_SMALLINT_MAX = 32767
#: The INTEGER columns' ceiling (result_count, latency_ms).
_INT_MAX = 2_147_483_647
#: Column widths of the vocabulary columns (a value never fails the insert).
_SCOPE_WIDTH, _MODE_WIDTH, _REASON_WIDTH = 20, 10, 30


def normalize_query(q: str) -> str:
    """Strip, lower-case and collapse whitespace: ``" Checkout  Flow"`` -> ``"checkout flow"``."""
    return " ".join(q.split()).lower()


def _hash_key() -> bytes:
    secret = settings.APP_SECRET_KEY or ""
    return hashlib.sha256(f"{USAGE_HASH_DOMAIN}:{secret}".encode("utf-8")).digest()


def query_hash(q: str) -> str:
    """HMAC-SHA256 (hex, 64 chars) of the normalised query, keyed from ``APP_SECRET_KEY``."""
    return hmac.new(_hash_key(), normalize_query(q).encode("utf-8"), hashlib.sha256).hexdigest()


def _bounded(value: Any, ceiling: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, ceiling))


def _project_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def build_event(
    *,
    q: str,
    word_count: int,
    filter_count: int,
    project_id: uuid.UUID | None,
    entity_scope: str,
    mode_requested: str,
    mode_used: str,
    fallback_reason: str | None,
    result_count: int,
    counts_exact: bool,
    latency_ms: int,
    now: datetime | None = None,
) -> dict:
    """The column values of ONE ``search_query_events`` row (``SearchQueryEvent(**event)``).

    The query is reduced to :func:`query_hash` and its stripped length here;
    the dict holds NO key named ``q`` / ``query`` / ``text`` and no value
    equal to the query. Never raises: counts are clamped into their columns
    (``>= 0``, the check constraint) and vocabulary values cut to the column
    widths, because this runs inside a search and telemetry never fails one.
    ``created_at`` is the search's own time (``now``, default the current UTC
    time), not the later insert's.
    """
    stripped = q.strip()
    created_at = now if now is not None else datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "project_id": _project_uuid(project_id),
        "q_hash": query_hash(stripped),
        "q_length": _bounded(len(stripped), _SMALLINT_MAX),
        "word_count": _bounded(word_count, _SMALLINT_MAX),
        "filter_count": _bounded(filter_count, _SMALLINT_MAX),
        "entity_scope": str(entity_scope)[:_SCOPE_WIDTH],
        "mode_requested": str(mode_requested)[:_MODE_WIDTH],
        "mode_used": str(mode_used)[:_MODE_WIDTH],
        "fallback_reason": str(fallback_reason)[:_REASON_WIDTH] if fallback_reason else None,
        "result_count": _bounded(result_count, _INT_MAX),
        "counts_exact": bool(counts_exact),
        "latency_ms": _bounded(latency_ms, _INT_MAX),
        "created_at": created_at,
    }


def _utc(now: datetime) -> datetime:
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def usage_window_start(days: int, now: datetime) -> datetime:
    """00:00 UTC today for ``days == 1``; else ``now - days``."""
    current = _utc(now)
    if days == 1:
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    return current - timedelta(days=days)


def usage_cutoff(now: datetime) -> datetime:
    """Rows created before this are past the fixed 30-day window."""
    return _utc(now) - timedelta(days=USAGE_RETENTION_DAYS)


def _summary_body(
    *, project_id: uuid.UUID | None, days: int, since: datetime, searches: int,
    zero_results: int, fallbacks: int, p50: Any, p95: Any,
) -> dict:
    measured = searches > 0
    return {
        "project_id": project_id,
        "days": days,
        "since": since,
        "measured": measured,
        "searches": searches,
        "zero_result_searches": zero_results,
        "zero_result_rate": (zero_results / searches) if measured else None,
        "p50_latency_ms": float(p50) if measured and p50 is not None else None,
        "p95_latency_ms": float(p95) if measured and p95 is not None else None,
        "fallback_searches": fallbacks,
        "retention_days": USAGE_RETENTION_DAYS,
    }


def usage_statement(
    *, project_id: uuid.UUID | None, allowed_project_ids: set[uuid.UUID] | None, since: datetime,
):
    """The ONE aggregate statement of :func:`usage_summary` (exposed for the SQL tests)."""
    event = SearchQueryEvent
    stmt = select(
        func.count().label("searches"),
        func.count().filter(and_(event.result_count == 0, event.counts_exact.is_(True))).label("zero_results"),
        func.count().filter(event.fallback_reason.is_not(None)).label("fallbacks"),
        func.percentile_cont(0.5).within_group(event.latency_ms).label("p50"),
        func.percentile_cont(0.95).within_group(event.latency_ms).label("p95"),
    ).where(event.created_at >= since)
    if project_id is not None:
        stmt = stmt.where(event.project_id == project_id)
    elif allowed_project_ids is not None:
        ids = bindparam(
            "allowed_project_ids", value=sorted(allowed_project_ids, key=str),
            type_=ARRAY(PG_UUID(as_uuid=True)),
        )
        stmt = stmt.where(event.project_id == any_(ids))
    return stmt


async def usage_summary(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    allowed_project_ids: set[uuid.UUID] | None,
    days: int,
    now: datetime,
) -> dict:
    """A ``SearchUsageRead`` body for the window, from ONE statement.

    The scope is ``resolve_project_scope``'s answer: ``project_id`` pinned ->
    that project's rows; ``allowed_project_ids`` (a non-admin's memberships)
    -> rows of those projects (an EMPTY set answers ``measured: False``
    without a statement); both None (ADMIN, All Projects) -> every row,
    All-Projects (NULL project) searches included. ``days`` must be one of
    ``USAGE_DAYS`` (the route answers 422 first).
    """
    if days not in USAGE_DAYS:
        raise ValueError(f"days must be one of {USAGE_DAYS}")
    since = usage_window_start(days, now)
    if project_id is None and allowed_project_ids is not None and not allowed_project_ids:
        return _summary_body(
            project_id=None, days=days, since=since, searches=0, zero_results=0, fallbacks=0,
            p50=None, p95=None,
        )
    row = (
        await db.execute(
            usage_statement(project_id=project_id, allowed_project_ids=allowed_project_ids, since=since)
        )
    ).one()
    return _summary_body(
        project_id=project_id,
        days=days,
        since=since,
        searches=int(row.searches or 0),
        zero_results=int(row.zero_results or 0),
        fallbacks=int(row.fallbacks or 0),
        p50=row.p50,
        p95=row.p95,
    )


async def purge_expired_batch(db: AsyncSession, *, now: datetime, batch: int = USAGE_PURGE_BATCH) -> int:
    """Stage ONE delete of up to ``batch`` rows older than :func:`usage_cutoff`; return how many.

    ``DELETE … WHERE id IN (SELECT id … WHERE created_at < :cutoff ORDER BY
    created_at LIMIT :batch)`` -- every project and the All-Projects (NULL)
    rows alike. Never commits: the nightly worker task commits each batch and
    loops until a batch deletes nothing.
    """
    oldest = (
        select(SearchQueryEvent.id)
        .where(SearchQueryEvent.created_at < usage_cutoff(now))
        .order_by(SearchQueryEvent.created_at)
        .limit(max(1, int(batch)))
        .scalar_subquery()
    )
    result = await db.execute(
        delete(SearchQueryEvent).where(SearchQueryEvent.id.in_(oldest)).execution_options(
            synchronize_session=False
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
