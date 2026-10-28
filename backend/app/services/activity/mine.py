"""``GET /api/v1/activity/mine``: what I did, and what involves me, across my projects (W21d ACT-02).

Four parts, one SELECT each, every one keyset-ordered ``(occurred_at DESC,
id DESC)`` with the window and the cursor INSIDE it and its own
``LIMIT limit + 1``; the view picks the parts (``mine`` = A, ``involving`` =
B ∪ C ∪ D, ``all`` = all four):

* **A ``actor``** — rows I wrote as a signed-in person (``actor_type='user'``;
  my API keys' CI rows stay in the project feeds), ``ix_pae_actor_time``.
* **B ``mentioned``** — ``test_case.commented`` rows whose ``context.mentions``
  holds my id, read ONLY in projects I am a member of
  (``ix_pae_project_event_type``).
* **C ``assigned`` / ``authored``** — rows about a test case I am the assignee
  or the author of (``ix_mtc_assignee`` / ``ix_mtc_author``, joined on the
  project too).
* **D ``assigned``** — rows about a defect assigned to me.

No OR across tables (the OR in C is between two columns of ONE table). The
parts are merged in Python: sorted, de-duplicated by id keeping the highest
priority involvement (``INVOLVEMENTS`` order), cut at ``limit``. Exact keyset:
each part returned its own top ``limit + 1`` under the same cursor, so the
merged top ``limit`` is the union's. ``total`` is ONE count over the parts' id
UNION (window only). A read: nothing here writes, flushes or commits.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import String, Select, cast, func, or_, select, tuple_, union
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.activity.live_vocab import INVOLVEMENTS, MINE_DEFAULT_LIMIT, MINE_MAX_LIMIT
from app.services.activity.query import InvalidCursor, serialize

#: The one event whose context names mentioned users (G-C's producer).
MENTION_EVENT_TYPE = "test_case.commented"
#: Ledger entity types that address a managed test case (``test`` is the
#: lifecycle rows' older spelling, F-1).
TEST_CASE_ENTITY_TYPES = ("test", "test_case")
DEFECT_ENTITY_TYPE = "defect"

_PRIORITY = {name: index for index, name in enumerate(INVOLVEMENTS)}


@dataclass(slots=True)
class MyActivityFilters:
    """The ``/activity/mine`` query, built by the router's dependency.

    A dataclass, not ``Query(...)`` defaults on the handler: a direct handler
    call then gets real values (the repo's ``Query(None)`` rule).
    """

    view: str = "all"
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = MINE_DEFAULT_LIMIT
    cursor: Optional[str] = None
    #: The QUERY pin (the Inbox's active project); authorized by the router.
    project_id: Optional[uuid.UUID] = None


# ── The cursor, bound to the caller ──────────────────────────────────────────


def _user_fingerprint(user_id: uuid.UUID) -> str:
    """Binds a cursor to the user it was minted for (query.py's format, the
    user in place of the project)."""
    return hashlib.sha256(f"user:{user_id}".encode()).hexdigest()[:12]


def encode_user_cursor(occurred_at: datetime, row_id: uuid.UUID, user_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{row_id}|{_user_fingerprint(user_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_user_cursor(cursor: str, user_id: uuid.UUID) -> tuple[datetime, uuid.UUID]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        ts_str, id_str, fingerprint = raw.split("|")
        parsed_ts = datetime.fromisoformat(ts_str)
        parsed_id = uuid.UUID(id_str)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("Cursor is malformed") from exc
    if fingerprint != _user_fingerprint(user_id):
        raise InvalidCursor("Cursor was issued for a different user")
    if parsed_ts.tzinfo is None:
        parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
    return parsed_ts, parsed_id


# ── The parts ────────────────────────────────────────────────────────────────


async def member_project_ids(db: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """The projects the caller is a MEMBER of (part B never reads beyond them,
    ADMIN included: a mention is only ever of a member)."""
    from app.models.postgres import ProjectMember

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
    )
    return {row[0] for row in result.all()}


def _windowed(stmt: Select, model: Any, filters: MyActivityFilters, after: Any) -> Select:
    if filters.since is not None:
        stmt = stmt.where(model.occurred_at >= filters.since)
    if filters.until is not None:
        stmt = stmt.where(model.occurred_at <= filters.until)
    if after is not None:
        stmt = stmt.where(tuple_(model.occurred_at, model.id) < tuple_(after[0], after[1]))
    return stmt


def _part_statements(
    user_id: uuid.UUID,
    filters: MyActivityFilters,
    *,
    scope: Optional[set[uuid.UUID]],
    member_projects: set[uuid.UUID],
    columns: str,
    after: Any = None,
) -> dict[str, Select]:
    """The view's parts, keyed A / B / C / D. ``columns='rows'`` selects the
    rows (plus the assignee for C), ``'ids'`` only the ids (for the count)."""
    from app.models.postgres import Defect, ManagedTestCase
    from app.models.postgres import ProjectActivityEvent as M

    view = filters.view
    involving_only = view == "involving"

    def _base(*extra: Any) -> Select:
        if columns == "ids":
            return select(M.id)
        return select(M, *extra)

    parts: dict[str, Select] = {}
    if view in ("all", "mine"):
        a = _base().where(M.actor_type == "user", M.actor_id == user_id)
        if scope is not None:
            a = a.where(M.project_id.in_(sorted(scope)))
        parts["A"] = a
    if view == "mine":
        return {k: _windowed(s, M, filters, after) for k, s in parts.items()}

    if member_projects:
        b = _base().where(
            M.project_id.in_(sorted(member_projects)),
            M.event_type == MENTION_EVENT_TYPE,
            cast(M.context, JSONB)["mentions"].contains([str(user_id)]),
        )
        if involving_only:
            b = b.where(M.actor_id.is_distinct_from(user_id))
        parts["B"] = b

    mtc = ManagedTestCase.__table__.alias("mtc")
    c = (
        _base(mtc.c.assignee_id)
        .join(
            mtc,
            (M.entity_id == cast(mtc.c.id, String)) & (M.project_id == mtc.c.project_id),
        )
        .where(
            M.entity_type.in_(TEST_CASE_ENTITY_TYPES),
            or_(mtc.c.assignee_id == user_id, mtc.c.author_id == user_id),
        )
    )
    if scope is not None:
        c = c.where(mtc.c.project_id.in_(sorted(scope)))
    if involving_only:
        c = c.where(M.actor_id.is_distinct_from(user_id))
    parts["C"] = c

    d_table = Defect.__table__.alias("d")
    d = (
        _base()
        .join(
            d_table,
            (M.entity_id == cast(d_table.c.id, String)) & (M.project_id == d_table.c.project_id),
        )
        .where(M.entity_type == DEFECT_ENTITY_TYPE, d_table.c.assignee_user_id == user_id)
    )
    if scope is not None:
        d = d.where(d_table.c.project_id.in_(sorted(scope)))
    if involving_only:
        d = d.where(M.actor_id.is_distinct_from(user_id))
    parts["D"] = d
    return {k: _windowed(s, M, filters, after) for k, s in parts.items()}


def _involvement(part: str, assignee_id: Any, user_id: uuid.UUID) -> str:
    if part == "A":
        return "actor"
    if part == "B":
        return "mentioned"
    if part == "C":
        return "assigned" if assignee_id == user_id else "authored"
    return "assigned"


async def list_my_activity(
    db: AsyncSession,
    user: Any,
    filters: MyActivityFilters,
    *,
    scope: Optional[set[uuid.UUID]],
    member_projects: set[uuid.UUID],
) -> dict:
    """The §1.2 answer. ``scope`` is the authorized project set (None = every
    project, ADMIN unpinned; an empty set = no membership: no query at all);
    ``member_projects`` the caller's own memberships within it (part B)."""
    from app.models.postgres import ProjectActivityEvent as M

    user_id: uuid.UUID = user.id
    limit = max(1, min(int(filters.limit or MINE_DEFAULT_LIMIT), MINE_MAX_LIMIT))
    answer: dict[str, Any] = {
        "items": [],
        "total": 0,
        "next_cursor": None,
        "window": {
            "since": filters.since.isoformat() if filters.since else None,
            "until": filters.until.isoformat() if filters.until else None,
        },
        "view": filters.view,
        "projects": len(scope) if scope is not None else None,
    }
    if scope is not None and not scope:
        return answer

    after = decode_user_cursor(filters.cursor, user_id) if filters.cursor else None
    parts = _part_statements(
        user_id, filters, scope=scope, member_projects=member_projects, columns="rows", after=after
    )
    found: dict[uuid.UUID, tuple[Any, str]] = {}
    for part, stmt in parts.items():
        stmt = stmt.order_by(M.occurred_at.desc(), M.id.desc()).limit(limit + 1)
        result = await db.execute(stmt)
        for record in result.all():
            row, assignee_id = record[0], (record[1] if len(record) > 1 else None)
            involvement = _involvement(part, assignee_id, user_id)
            kept = found.get(row.id)
            if kept is None or _PRIORITY[involvement] < _PRIORITY[kept[1]]:
                found[row.id] = (row, involvement)

    ordered = sorted(found.values(), key=lambda pair: (pair[0].occurred_at, pair[0].id), reverse=True)
    page = ordered[:limit]
    answer["items"] = [serialize(row) | {"involvement": inv} for row, inv in page]
    if len(ordered) > limit and page:
        last = page[-1][0]
        answer["next_cursor"] = encode_user_cursor(last.occurred_at, last.id, user_id)
    answer["total"] = await _count(db, user_id, filters, scope=scope, member_projects=member_projects)
    return answer


async def _count(
    db: AsyncSession,
    user_id: uuid.UUID,
    filters: MyActivityFilters,
    *,
    scope: Optional[set[uuid.UUID]],
    member_projects: set[uuid.UUID],
) -> int:
    """Distinct rows of the view in the window: ONE count over the parts' id
    UNION (``UNION`` de-duplicates; no cursor, no limit)."""
    ids = list(
        _part_statements(
            user_id, filters, scope=scope, member_projects=member_projects, columns="ids"
        ).values()
    )
    if not ids:
        return 0
    combined = ids[0] if len(ids) == 1 else union(*ids)
    result = await db.execute(select(func.count()).select_from(combined.subquery()))
    return int(result.scalar_one() or 0)
