"""Raising clean-up for the ``test_security_*_postgres.py`` modules.

CI runs every Postgres integration module against ONE shared database, so a
row a module leaves behind can break a module three files later. These
modules seed fresh projects and users (``uuid4`` ids, never shared), and this
helper removes everything that points at them:

* every row of every table with a ``project_id`` column naming a seeded project;
* every row whose foreign key names a seeded user (``users.id``);
* then the projects and the users themselves.

Deletes run table by table, each in its own transaction, and are retried
while they make progress, so the order of foreign keys never has to be
written down. Afterwards every one of those tables is counted again, and any
row still there fails the module that seeded it: :func:`purge_seeds` raises
``AssertionError`` naming each table.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: Tables whose ``project_id`` column holds the id as text, not a uuid.
_TEXT_TYPES = {"character varying", "text", "character"}


async def _project_columns(sessions) -> list[tuple[str, bool]]:
    """(table, column is text) for every base table with a ``project_id`` column."""
    async with sessions() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.table_name, c.data_type FROM information_schema.columns c "
                    "JOIN information_schema.tables t "
                    "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                    "WHERE c.table_schema = current_schema() AND c.column_name = 'project_id' "
                    "  AND t.table_type = 'BASE TABLE' ORDER BY c.table_name"
                )
            )
        ).all()
    return [(row[0], row[1] in _TEXT_TYPES) for row in rows]


async def _user_references(sessions) -> list[tuple[str, str]]:
    """(table, column) for every single-column foreign key to ``users.id``."""
    async with sessions() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT cl.relname, att.attname FROM pg_constraint con "
                    "JOIN pg_class cl ON cl.oid = con.conrelid "
                    "JOIN pg_namespace ns ON ns.oid = cl.relnamespace "
                    "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1] "
                    "WHERE con.contype = 'f' AND con.confrelid = 'users'::regclass "
                    "  AND array_length(con.conkey, 1) = 1 AND ns.nspname = current_schema() "
                    "ORDER BY cl.relname, att.attname"
                )
            )
        ).all()
    return [(row[0], row[1]) for row in rows]


def _ids(values: Iterable, as_text: bool) -> list:
    return [str(v) if as_text else uuid.UUID(str(v)) for v in values]


async def _statements(
    sessions, project_ids: list, user_ids: list
) -> list[tuple[str, str, list]]:
    """(label, ``WHERE`` clause target, bound ids) for every place a seed can live."""
    out: list[tuple[str, str, list]] = []
    if project_ids:
        for table, as_text in await _project_columns(sessions):
            if table != "projects":
                out.append(
                    (
                        f"{table}.project_id",
                        f'"{table}" WHERE project_id = ANY(:ids)',
                        _ids(project_ids, as_text),
                    )
                )
    if user_ids:
        for table, column in await _user_references(sessions):
            if table != "users":
                out.append(
                    (
                        f"{table}.{column}",
                        f'"{table}" WHERE "{column}" = ANY(:ids)',
                        _ids(user_ids, False),
                    )
                )
    if project_ids:
        out.append(
            ("projects.id", '"projects" WHERE id = ANY(:ids)', _ids(project_ids, False))
        )
    if user_ids:
        out.append(("users.id", '"users" WHERE id = ANY(:ids)', _ids(user_ids, False)))
    return out


async def leftover_rows(
    sessions, *, project_ids: Iterable = (), user_ids: Iterable = ()
) -> dict[str, int]:
    """``{"table.column": rows}`` for every place a seeded row still lives (empty when clean)."""
    left: dict[str, int] = {}
    for label, target, ids in await _statements(
        sessions, list(project_ids), list(user_ids)
    ):
        async with sessions() as db:
            count = (
                await db.execute(text(f"SELECT count(*) FROM {target}"), {"ids": ids})
            ).scalar_one()
        if count:
            left[label] = int(count)
    return left


async def purge_seeds(
    sessions, *, project_ids: Iterable = (), user_ids: Iterable = ()
) -> None:
    """Delete every row that names a seeded project or user, then RAISE if any is left.

    ``sessions`` is an ``async_sessionmaker`` bound to the test database.
    """
    project_ids, user_ids = list(project_ids), list(user_ids)
    pending = await _statements(sessions, project_ids, user_ids)
    errors: dict[str, str] = {}
    for _attempt in range(8):
        still: list[tuple[str, str, list]] = []
        for label, target, ids in pending:
            try:
                async with sessions.begin() as db:
                    await db.execute(text(f"DELETE FROM {target}"), {"ids": ids})
                errors.pop(label, None)
            except DBAPIError as exc:  # a row that another seeded row still references
                errors[label] = str(getattr(exc, "orig", exc)).splitlines()[0][:200]
                still.append((label, target, ids))
        if not still or len(still) == len(pending):
            pending = still
            break
        pending = still
    left = await leftover_rows(sessions, project_ids=project_ids, user_ids=user_ids)
    if left or pending:
        raise AssertionError(
            f"seed clean-up left rows behind: {left}; deletes that failed: "
            + "; ".join(
                f"{label}: {errors.get(label, '?')}" for label, _t, _i in pending
            )
        )


async def purge_redis_keys(redis, *fragments: str) -> int:
    """Delete every Redis key whose name contains one of ``fragments``; return how many."""
    removed = 0
    for fragment in fragments:
        async for key in redis.scan_iter(match=f"*{fragment}*", count=500):
            removed += int(await redis.delete(key))
    return removed


#: Where a live session leaves state that its id is not part of the key name
#: (``app.services.stream_service.SESSION_TOKEN_KEY`` and the live event stream).
_LIVE_TOKEN_PATTERN = "live:session:*"
_LIVE_ACTIVE_SET = "testlookup:live:active"
_LIVE_EVENT_STREAM = "testlookup:stream:live_events"


async def purge_live_sessions(redis, session_ids: Iterable[str]) -> None:
    """Remove a live session's Redis state: its keys, its token, its stream entries.

    Raises ``AssertionError`` if any of it is still there afterwards.
    """
    ids = {str(s) for s in session_ids}
    if not ids:
        return
    await purge_redis_keys(redis, *ids)
    async for key in redis.scan_iter(match=_LIVE_TOKEN_PATTERN, count=500):
        if await redis.get(key) in ids:
            await redis.delete(key)
    await redis.srem(_LIVE_ACTIVE_SET, *ids)
    stale = [
        entry_id
        for entry_id, fields in await redis.xrange(_LIVE_EVENT_STREAM)
        if fields.get("session_id") in ids or fields.get("run_id") in ids
    ]
    if stale:
        await redis.xdel(_LIVE_EVENT_STREAM, *stale)
    left = [
        key
        async for key in redis.scan_iter(match=_LIVE_TOKEN_PATTERN, count=500)
        if await redis.get(key) in ids
    ]
    left += [
        f"{_LIVE_ACTIVE_SET}:{m}"
        for m in await redis.smembers(_LIVE_ACTIVE_SET)
        if m in ids
    ]
    left += [
        f"{_LIVE_EVENT_STREAM}:{entry_id}"
        for entry_id, fields in await redis.xrange(_LIVE_EVENT_STREAM)
        if fields.get("session_id") in ids or fields.get("run_id") in ids
    ]
    for fragment in ids:
        left += [key async for key in redis.scan_iter(match=f"*{fragment}*", count=500)]
    if left:
        raise AssertionError(f"live-session clean-up left Redis state behind: {left}")
