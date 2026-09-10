"""Reads over the activity ledger: keyset pagination, filters, export.

Two decisions here are load-bearing and easy to undo by accident.

**Keyset, not offset.** The feed pages on ``(occurred_at, id)`` rather than
``LIMIT/OFFSET``. Offset paging over an append-only table that is being written
to WHILE the user reads it either repeats or skips rows on every page turn —
and this table is written on every ingest. A cursor also means page 2 costs the
same as page 200, which the existing Audit Dashboard cannot say (it takes
``page_size`` rows per source, merges them, and ignores ``page`` entirely).

**Every filter is an ANDed column on this one table.** No OR across tables, no
join to resolve a filter. A 5-way OR over 3 tables previously turned the search
path into a Seq Scan; the indexes in migration 0165 only help while the WHERE
clause stays shaped like this.
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.activity.events import entity_href

logger = structlog.get_logger("services.activity.query")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: Below this, a trigram ILIKE matches so much that it is neither useful nor
#: indexable. Documented in the endpoint description rather than silently
#: returning everything.
MIN_QUERY_CHARS = 3
#: Hard ceiling on one export. Beyond this the caller narrows the window; the
#: response says so via X-Truncated rather than quietly returning a prefix.
EXPORT_MAX_ROWS = 100_000


class InvalidCursor(ValueError):
    """The cursor is malformed, or was minted for a different project."""


@dataclass(slots=True)
class ActivityFilters:
    """The feed's filter set.

    A dataclass rather than twelve ``Query(...)`` parameters on the handler,
    and that is deliberate: a ``Query(None)`` default is returned as the
    ``Query`` OBJECT (not ``None``) when a test calls the handler directly,
    which has broken direct-call tests in this repo five separate times. One
    dependency object has one shape whether it is built by FastAPI or by a test.
    """

    categories: Sequence[str] = field(default_factory=tuple)
    event_types: Sequence[str] = field(default_factory=tuple)
    actor_id: Optional[uuid.UUID] = None
    actor_type: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    release_id: Optional[uuid.UUID] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    q: Optional[str] = None
    limit: int = DEFAULT_LIMIT
    cursor: Optional[str] = None


# ── Cursor ───────────────────────────────────────────────────────────────────


def _project_fingerprint(project_id: uuid.UUID) -> str:
    """Short, non-reversible tag binding a cursor to its project.

    Not a security control — the endpoint's ``require_project_access`` is. This
    catches the confusing case where a client reuses a cursor across projects
    and would otherwise silently get a page from the wrong keyset position.
    """
    return hashlib.sha256(str(project_id).encode()).hexdigest()[:12]


def encode_cursor(occurred_at: datetime, row_id: uuid.UUID, project_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{row_id}|{_project_fingerprint(project_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, project_id: uuid.UUID) -> tuple[datetime, uuid.UUID]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        ts_str, id_str, fingerprint = raw.split("|")
        parsed_ts = datetime.fromisoformat(ts_str)
        parsed_id = uuid.UUID(id_str)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("Cursor is malformed") from exc

    if fingerprint != _project_fingerprint(project_id):
        raise InvalidCursor("Cursor was issued for a different project")
    if parsed_ts.tzinfo is None:
        parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
    return parsed_ts, parsed_id


# ── Query construction ───────────────────────────────────────────────────────


def _apply_filters(stmt: Select, model: Any, filters: ActivityFilters) -> Select:
    if filters.categories:
        stmt = stmt.where(model.category.in_(list(filters.categories)))
    if filters.event_types:
        stmt = stmt.where(model.event_type.in_(list(filters.event_types)))
    if filters.actor_id is not None:
        stmt = stmt.where(model.actor_id == filters.actor_id)
    if filters.actor_type:
        stmt = stmt.where(model.actor_type == filters.actor_type)
    if filters.entity_type:
        stmt = stmt.where(model.entity_type == filters.entity_type)
    if filters.entity_id:
        stmt = stmt.where(model.entity_id == str(filters.entity_id))
    if filters.release_id is not None:
        stmt = stmt.where(model.release_id == filters.release_id)
    if filters.since is not None:
        stmt = stmt.where(model.occurred_at >= filters.since)
    if filters.until is not None:
        stmt = stmt.where(model.occurred_at <= filters.until)
    if filters.q and len(filters.q.strip()) >= MIN_QUERY_CHARS:
        # Unanchored ILIKE, served by the GIN trigram index from 0165.
        #
        # The needle is ESCAPED first. `%` and `_` are LIKE metacharacters, so
        # an unescaped search silently returns the wrong rows rather than
        # failing: `q=%%%` matched every event in the project, and `q=B_ild`
        # matched "Build". Not an injection risk — the value is still bound —
        # but a search that quietly lies is worse than one that errors.
        #
        # Backslash is escaped first, otherwise it would double-escape the
        # sequences added on the next two lines.
        needle = filters.q.strip()
        needle = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(model.summary.ilike(f"%{needle}%", escape="\\"))
    return stmt


async def list_events(
    db: AsyncSession,
    project_id: uuid.UUID,
    filters: ActivityFilters,
) -> dict:
    """One page of the feed, newest first, plus the cursor for the next page."""
    from app.models.postgres import ProjectActivityEvent as M

    limit = max(1, min(int(filters.limit or DEFAULT_LIMIT), MAX_LIMIT))

    stmt = select(M).where(M.project_id == project_id)
    stmt = _apply_filters(stmt, M, filters)

    if filters.cursor:
        after_ts, after_id = decode_cursor(filters.cursor, project_id)
        # Row-value comparison, so the keyset index is used as a single
        # ordered lookup instead of a filter plus a sort.
        stmt = stmt.where(tuple_(M.occurred_at, M.id) < tuple_(after_ts, after_id))

    # Fetch one extra to learn whether another page exists without a COUNT.
    stmt = stmt.order_by(M.occurred_at.desc(), M.id.desc()).limit(limit + 1)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].occurred_at, page[-1].id, project_id)
        if has_more and page
        else None
    )

    return {
        "items": [serialize(row) for row in page],
        "next_cursor": next_cursor,
        "ledger_started_at": await ledger_started_at(db, project_id),
        "window": {
            "since": filters.since.isoformat() if filters.since else None,
            "until": filters.until.isoformat() if filters.until else None,
        },
    }


async def ledger_started_at(db: AsyncSession, project_id: uuid.UUID) -> Optional[str]:
    """When this project's ledger begins.

    **Ignores every filter, deliberately.** This answers "has this project ever
    had any activity, and from when", which is what the empty state needs in
    order to say "the ledger starts on X" instead of "nothing happened". A
    release or category filter leaking in here would make a populated project
    look brand new — the same class of bug that once showed the first-run
    wizard on a project full of runs.
    """
    from app.models.postgres import ProjectActivityEvent as M

    result = await db.execute(
        select(func.min(M.occurred_at)).where(M.project_id == project_id)
    )
    earliest = result.scalar_one_or_none()
    return earliest.isoformat() if earliest else None


async def get_event(
    db: AsyncSession, project_id: uuid.UUID, event_id: uuid.UUID
) -> Optional[dict]:
    """One event with its full diff. The feed never returns ``diff``."""
    from app.models.postgres import ProjectActivityEvent as M

    result = await db.execute(
        select(M).where(M.id == event_id, M.project_id == project_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    payload = serialize(row)
    payload["diff"] = row.diff
    return payload


async def entity_timeline(
    db: AsyncSession,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    limit: int = 50,
) -> list[dict]:
    """Everything that happened to one entity, for run/release detail pages."""
    from app.models.postgres import ProjectActivityEvent as M

    result = await db.execute(
        select(M)
        .where(
            M.project_id == project_id,
            M.entity_type == entity_type,
            M.entity_id == str(entity_id),
        )
        .order_by(M.occurred_at.desc(), M.id.desc())
        .limit(max(1, min(int(limit), MAX_LIMIT)))
    )
    return [serialize(row) for row in result.scalars().all()]


# ── Serialisation ────────────────────────────────────────────────────────────


def serialize(row: Any) -> dict:
    """Feed shape. ``diff`` is omitted — only the single-event endpoint has it.

    ``href`` is resolved server-side rather than in the SPA so the CLI and the
    MCP tool get working links too, instead of three clients each re-deriving
    the route map and drifting apart.
    """
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "release_id": str(row.release_id) if row.release_id else None,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "category": row.category,
        "event_type": row.event_type,
        "actor": {
            "type": row.actor_type,
            "id": str(row.actor_id) if row.actor_id else None,
            "name": row.actor_name,
            "ref": row.actor_ref,
        },
        "entity": {
            "type": row.entity_type,
            "id": row.entity_id,
            "label": row.entity_label,
            "href": entity_href(row.entity_type, row.entity_id),
        },
        "target": (
            {"type": row.target_type, "id": row.target_id}
            if row.target_type
            else None
        ),
        "summary": row.summary,
        "context": row.context,
        "has_diff": row.diff is not None,
        "source": (
            {"table": row.source_table, "id": str(row.source_id) if row.source_id else None}
            if row.source_table
            else None
        ),
        "group_key": row.group_key,
    }


_EXPORT_COLUMNS = (
    "occurred_at",
    "category",
    "event_type",
    "actor_type",
    "actor_name",
    "entity_type",
    "entity_id",
    "entity_label",
    "summary",
    "release_id",
    "source_table",
    "source_id",
)


async def iter_export_rows(
    db: AsyncSession,
    project_id: uuid.UUID,
    filters: ActivityFilters,
) -> tuple[list[dict], bool]:
    """Rows for an export, plus whether the cap truncated them.

    No second redaction pass: values were redacted on the way IN, so anything
    reachable here is already safe. ``tests/test_activity_export.py`` asserts
    that property rather than trusting this comment.
    """
    from app.models.postgres import ProjectActivityEvent as M

    stmt = select(M).where(M.project_id == project_id)
    stmt = _apply_filters(stmt, M, filters)
    stmt = stmt.order_by(M.occurred_at.desc(), M.id.desc()).limit(EXPORT_MAX_ROWS + 1)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    truncated = len(rows) > EXPORT_MAX_ROWS
    return [_export_row(r) for r in rows[:EXPORT_MAX_ROWS]], truncated


def _export_row(row: Any) -> dict:
    return {
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else "",
        "category": row.category,
        "event_type": row.event_type,
        "actor_type": row.actor_type,
        "actor_name": row.actor_name or "",
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "entity_label": row.entity_label or "",
        "summary": row.summary,
        "release_id": str(row.release_id) if row.release_id else "",
        "source_table": row.source_table or "",
        "source_id": str(row.source_id) if row.source_id else "",
    }


def rows_to_csv(rows: Iterable[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_EXPORT_COLUMNS))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def rows_to_ndjson(rows: Iterable[dict]) -> str:
    return "".join(json.dumps(row, default=str) + "\n" for row in rows)
