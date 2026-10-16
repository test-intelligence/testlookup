"""W08 TRND-04: notes pinned on a project's daily charts ("release freeze", "infra outage").

A note is an inclusive UTC day range of ONE project (``starts_on`` ..
``ends_on``; a one-day note has both equal), 1 to :data:`NOTE_TEXT_MAX`
characters of text, and optionally one suite (``None`` = every suite of the
project). The Trends hero chart and the Coverage history chart draw every note
of their window.

Rules, each with ONE fixed 422 text:

* the text is stripped; blank or longer than :data:`NOTE_TEXT_MAX` is
  :data:`NOTE_TEXT_INVALID` (the schemas' ``max_length`` refuses a longer raw
  value before the service runs);
* ``ends_on`` defaults to ``starts_on``; it must be on or after it and at most
  :data:`NOTE_RANGE_MAX_DAYS` days later (:data:`NOTE_RANGE_INVALID`);
* the list window ``from`` .. ``to``: ``from`` on or before ``to``, at most
  :data:`NOTE_RANGE_MAX_DAYS` days apart (:data:`NOTE_WINDOW_INVALID`).

A note is read ``WHERE id AND project_id``, so another project's note and an
unknown id are the same 404 (:data:`NOTE_NOT_FOUND`). Its author, or a QA lead
or above (by ROLE ORDER, never ``role == ADMIN``), may change or delete it
(:func:`can_edit`; otherwise :data:`NOTE_NOT_YOURS`).

The suite filter of the list is SQLAlchemy Core over the note's own column
(``lower(suite_name) = :suite`` or a project-wide note), not a raw
suite-match clause over executions (``tests/test_analytics_scope_ratchet.py``).
No suite filter lists every note of the window.

Services flush; the router stages the ``timeline_note.*`` ledger rows and
commits once. No analytics epoch is bumped: no cached analytics read reads
notes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Final, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TimelineNote, User, UserRole
from app.models.schemas import TimelineNoteCreate, TimelineNoteUpdate

NOTE_TEXT_MAX: Final = 280
NOTE_RANGE_MAX_DAYS: Final = 366
NOTES_LIST_MAX: Final = 200
#: The ledger's entity label: the note's first characters.
NOTE_LABEL_CHARS: Final = 60

NOTE_NOT_FOUND: Final = "Timeline note not found in this project."
NOTE_NOT_YOURS: Final = "Only the note's author or a QA lead can change this note."
NOTE_TEXT_INVALID: Final = "A note is 1 to 280 characters."
NOTE_RANGE_INVALID: Final = "A note ends on or after the day it starts, at most 366 days later."
NOTE_WINDOW_INVALID: Final = "from and to are UTC dates, from on or before to, at most 366 days apart."

#: The ``changed`` names of the ledger row, in this order.
CHANGE_TEXT: Final = "text"
CHANGE_DATES: Final = "dates"
CHANGE_SUITE: Final = "suite"


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


# ── validation (pure) ────────────────────────────────────────────────────────


def clean_text(value: Any) -> str:
    """The stripped text, or 422 :data:`NOTE_TEXT_INVALID`."""
    text = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(text) <= NOTE_TEXT_MAX:
        raise _unprocessable(NOTE_TEXT_INVALID)
    return text


def clean_suite(value: Optional[str]) -> Optional[str]:
    """Stripped; blank is ``None`` (every suite)."""
    suite = (value or "").strip()
    return suite or None


def check_range(starts_on: date, ends_on: date) -> None:
    """``ends_on`` on or after ``starts_on``, at most :data:`NOTE_RANGE_MAX_DAYS` later."""
    if ends_on < starts_on or (ends_on - starts_on).days > NOTE_RANGE_MAX_DAYS:
        raise _unprocessable(NOTE_RANGE_INVALID)


def check_window(start: date, end: date) -> None:
    """The list window: ``start`` on or before ``end``, at most :data:`NOTE_RANGE_MAX_DAYS` apart."""
    if end < start or (end - start).days > NOTE_RANGE_MAX_DAYS:
        raise _unprocessable(NOTE_WINDOW_INVALID)


def note_label(text: str) -> str:
    """The ledger's entity label: the first :data:`NOTE_LABEL_CHARS` characters."""
    return (text or "")[:NOTE_LABEL_CHARS]


# ── permission and shape (pure) ──────────────────────────────────────────────


def _at_least_qa_lead(actor: Any) -> bool:
    from app.core.deps import _ROLE_ORDER, _normalize_user_role  # noqa: PLC0415

    try:
        return _ROLE_ORDER.index(_normalize_user_role(actor.role)) >= _ROLE_ORDER.index(UserRole.QA_LEAD)
    except (ValueError, AttributeError):
        return False


def can_edit(note: TimelineNote, actor: Any) -> bool:
    """The note's author, or a QA lead or above (instance role, by ROLE ORDER)."""
    author = getattr(note, "created_by_id", None)
    if author is not None and author == getattr(actor, "id", None):
        return True
    return _at_least_qa_lead(actor)


def _name_of(full_name: Optional[str], username: Optional[str]) -> Optional[str]:
    return (full_name or "").strip() or (username or "").strip() or None


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def note_values(note: TimelineNote) -> dict[str, Any]:
    """The note's user-visible values (no user ids): the ledger's before / after."""
    return {
        "text": note.text,
        "starts_on": note.starts_on.isoformat(),
        "ends_on": note.ends_on.isoformat(),
        "suite_name": note.suite_name,
    }


def note_response(note: TimelineNote, reader: Any) -> dict[str, Any]:
    """The ``TimelineNoteResponse`` shape of one note, ``can_edit`` for THIS reader."""
    return {
        "id": str(note.id),
        "project_id": str(note.project_id),
        "starts_on": note.starts_on.isoformat(),
        "ends_on": note.ends_on.isoformat(),
        "text": note.text,
        "suite_name": note.suite_name,
        "created_by_name": getattr(note, "created_by_name", None),
        "can_edit": can_edit(note, reader),
        "created_at": _iso(note.created_at),
        "updated_at": _iso(note.updated_at),
    }


# ── reading ──────────────────────────────────────────────────────────────────


def _with_author():
    return select(TimelineNote, User.full_name, User.username).outerjoin(
        User, User.id == TimelineNote.created_by_id
    )


def _with_name(note: TimelineNote, name: Optional[str]) -> TimelineNote:
    """The author's display name rides on the instance (not a column)."""
    setattr(note, "created_by_name", name)
    return note


def _named(row: Any) -> TimelineNote:
    note: TimelineNote = row[0]
    return _with_name(note, _name_of(row[1], row[2]))


async def list_notes(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    start: date,
    end: date,
    suite: Optional[str],
) -> tuple[list[TimelineNote], int]:
    """The project's notes overlapping ``start`` .. ``end`` (inclusive UTC days),
    ``starts_on, created_at, id`` order, at most :data:`NOTES_LIST_MAX`.

    Returns ``(rows, total)``: ``total`` counts every overlapping note (one
    statement, ``COUNT(*) OVER ()``), so ``truncated`` is ``total > len(rows)``.
    A ``suite`` keeps the project-wide notes and that suite's (any case).
    """
    check_window(start, end)
    stmt = (
        _with_author()
        .add_columns(func.count().over().label("total"))
        .where(
            TimelineNote.project_id == project_id,
            TimelineNote.starts_on <= end,
            TimelineNote.ends_on >= start,
        )
    )
    wanted = clean_suite(suite)
    if wanted is not None:
        stmt = stmt.where(
            TimelineNote.suite_name.is_(None)
            | (func.lower(TimelineNote.suite_name) == wanted.lower())
        )
    stmt = stmt.order_by(
        TimelineNote.starts_on, TimelineNote.created_at, TimelineNote.id
    ).limit(NOTES_LIST_MAX)
    rows = (await db.execute(stmt)).all()
    total = int(rows[0][3]) if rows else 0
    return [_named(row[:3]) for row in rows], total


async def _load(db: AsyncSession, project_id: uuid.UUID, note_id: uuid.UUID) -> TimelineNote:
    """The note ``WHERE id AND project_id``: another project's note and an
    unknown id are the same 404 (no oracle)."""
    row = (
        await db.execute(
            _with_author().where(
                TimelineNote.id == note_id,
                TimelineNote.project_id == project_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOTE_NOT_FOUND)
    return _named(row)


async def _load_editable(
    db: AsyncSession, project_id: uuid.UUID, note_id: uuid.UUID, actor: Any
) -> TimelineNote:
    note = await _load(db, project_id, note_id)
    if not can_edit(note, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOTE_NOT_YOURS)
    return note


# ── writing (flush only; the router commits) ─────────────────────────────────


async def create_note(
    db: AsyncSession, project_id: uuid.UUID, actor: User, body: TimelineNoteCreate
) -> TimelineNote:
    """Validate, add and flush; ``ends_on`` defaults to ``starts_on``."""
    text = clean_text(body.text)
    starts_on = body.starts_on
    ends_on = body.ends_on if body.ends_on is not None else starts_on
    check_range(starts_on, ends_on)
    note = TimelineNote(
        id=uuid.uuid4(),
        project_id=project_id,
        suite_name=clean_suite(body.suite_name),
        starts_on=starts_on,
        ends_on=ends_on,
        text=text,
        created_by_id=getattr(actor, "id", None),
    )
    db.add(note)
    await db.flush()
    # Server defaults (created_at, updated_at) are not on the instance after a
    # flush; reading them unrefreshed is a lazy load (MissingGreenlet).
    await db.refresh(note)
    return _with_name(note, _name_of(getattr(actor, "full_name", None), getattr(actor, "username", None)))


async def update_note(
    db: AsyncSession,
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    actor: User,
    body: TimelineNoteUpdate,
) -> tuple[TimelineNote, list[str], dict[str, Any], dict[str, Any]]:
    """Apply only the fields SENT (``model_fields_set``).

    Returns ``(note, changed, before, after)``: ``changed`` names what really
    changed (``text``, ``dates``, ``suite``, in that order), ``before`` /
    ``after`` hold those fields' old and new values. Nothing changed (an empty
    body included) is ``([], {}, {})``: no flush, no ledger row.

    ``starts_on: null`` changes nothing; ``ends_on: null`` makes it a one-day
    note (``ends_on := starts_on``); ``suite_name: null`` or blank makes it
    project-wide; ``text: null`` is :data:`NOTE_TEXT_INVALID`.
    """
    note = await _load_editable(db, project_id, note_id, actor)
    sent = body.model_fields_set
    text = clean_text(body.text) if "text" in sent else note.text
    starts_on = body.starts_on if ("starts_on" in sent and body.starts_on is not None) else note.starts_on
    if "ends_on" in sent:
        ends_on = body.ends_on if body.ends_on is not None else starts_on
    else:
        ends_on = note.ends_on
    check_range(starts_on, ends_on)
    suite = clean_suite(body.suite_name) if "suite_name" in sent else note.suite_name

    changed: list[str] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if text != note.text:
        changed.append(CHANGE_TEXT)
        before["text"], after["text"] = note.text, text
    if (starts_on, ends_on) != (note.starts_on, note.ends_on):
        changed.append(CHANGE_DATES)
        before.update(starts_on=note.starts_on.isoformat(), ends_on=note.ends_on.isoformat())
        after.update(starts_on=starts_on.isoformat(), ends_on=ends_on.isoformat())
    if suite != note.suite_name:
        changed.append(CHANGE_SUITE)
        before["suite_name"], after["suite_name"] = note.suite_name, suite
    if not changed:
        return note, [], {}, {}
    name = getattr(note, "created_by_name", None)
    note.text, note.starts_on, note.ends_on, note.suite_name = text, starts_on, ends_on, suite
    await db.flush()
    await db.refresh(note)
    return _with_name(note, name), changed, before, after


async def delete_note(
    db: AsyncSession, project_id: uuid.UUID, note_id: uuid.UUID, actor: User
) -> TimelineNote:
    """Delete and flush; returns the note as it was (for the ledger row)."""
    note = await _load_editable(db, project_id, note_id, actor)
    await db.delete(note)
    await db.flush()
    return note
