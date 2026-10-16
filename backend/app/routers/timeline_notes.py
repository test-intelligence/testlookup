"""Timeline notes (W08 TRND-04): list, pin, change, remove.

Every route sits under ``/api/v1/projects/{project_id}`` behind
``require_project_access()``: a project-bound API key reaches only its own
project's notes. Pinning, changing and removing also need the instance role
``QA_ENGINEER`` (``require_role``, as every write route in the product; below
QA_LEAD a project-bound key is not refused by the role guard, so nothing here
opts in). Changing or removing a note is for its author or a QA lead or above
(``timeline_note_service.can_edit``, 403 otherwise).

``{note_id}`` is always loaded ``WHERE id AND project_id``, so another
project's note and an unknown id answer the same 404.

The service flushes; this router stages the ``timeline_note.*`` ledger rows on
the same session (outcome mode: a rolled-back write leaves no row) and commits
once. A PATCH that changes nothing commits nothing and records nothing.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import TimelineNote, User, UserRole
from app.models.schemas import (
    TimelineNoteCreate,
    TimelineNoteList,
    TimelineNoteResponse,
    TimelineNoteUpdate,
)
from app.services import timeline_note_service as notes
from app.services.activity.service import ActorRef, record as record_activity

router = APIRouter(prefix="/api/v1/projects/{project_id}/timeline-notes", tags=["Timeline notes"])


async def _record(
    db: AsyncSession,
    *,
    event_type: str,
    project_id: uuid.UUID,
    user: User,
    note: TimelineNote,
    changed: Optional[list[str]] = None,
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
) -> None:
    await record_activity(
        db,
        project_id=project_id,
        event_type=event_type,
        actor=ActorRef.from_user(user),
        entity_id=str(note.id),
        entity_type="timeline_note",
        entity_label=notes.note_label(note.text),
        context={"changed": ", ".join(changed)} if changed else None,
        changed_fields=changed,
        before=before,
        after=after,
    )


@router.get("", response_model=TimelineNoteList)
async def list_timeline_notes(
    project_id: uuid.UUID,
    start: date = Query(..., alias="from", description="The window's first UTC day (YYYY-MM-DD)."),
    end: date = Query(..., alias="to", description="The window's last UTC day; at most 366 days after `from`."),
    suite: Optional[str] = Query(
        None, max_length=500,
        description="Keep the project-wide notes and this suite's (any case). Omit for every note.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """The notes overlapping ``from`` .. ``to`` (inclusive), at most 200."""
    rows, total = await notes.list_notes(db, project_id, start=start, end=end, suite=suite)
    return {
        "items": [notes.note_response(note, current_user) for note in rows],
        "total": total,
        "truncated": total > len(rows),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


@router.post("", response_model=TimelineNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_timeline_note(
    project_id: uuid.UUID,
    body: TimelineNoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """Pin a note on one UTC day or a day range (QA engineer or above)."""
    note = await notes.create_note(db, project_id, current_user, body)
    await _record(
        db, event_type="timeline_note.created", project_id=project_id, user=current_user,
        note=note, after=notes.note_values(note),
    )
    payload = notes.note_response(note, current_user)
    await db.commit()
    return payload


@router.patch("/{note_id}", response_model=TimelineNoteResponse)
async def update_timeline_note(
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    body: TimelineNoteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """Change the fields sent (the author or a QA lead); a ledger row only when one changed."""
    note, changed, before, after = await notes.update_note(db, project_id, note_id, current_user, body)
    payload = notes.note_response(note, current_user)
    if changed:
        await _record(
            db, event_type="timeline_note.updated", project_id=project_id, user=current_user,
            note=note, changed=changed, before=before, after=after,
        )
        await db.commit()
    return payload


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_timeline_note(
    project_id: uuid.UUID,
    note_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_project_access()),
):
    """Remove a note (the author or a QA lead)."""
    note = await notes.delete_note(db, project_id, note_id, current_user)
    await _record(
        db, event_type="timeline_note.deleted", project_id=project_id, user=current_user,
        note=note, before=notes.note_values(note),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
