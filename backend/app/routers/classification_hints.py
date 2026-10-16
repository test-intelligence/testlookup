"""Project classification hints (W07 FAIL-10): list, preview, create, update, delete.

Every route sits under ``/api/v1/projects/{project_id}`` behind
``require_project_access()``; the writes also need ``QA_LEAD`` (a
project-bound API key is let in because the path guard confines it to its own
project). ``{hint_id}`` is always loaded ``WHERE id AND project_id``, so
another project's hint and an unknown id answer the same 404.

The service flushes; this router stages the ``classification_hint.*`` ledger
rows on the same session (outcome mode: a rolled-back write leaves no row)
and commits once. A duplicate (field, lower(text)) is the unique index's
``IntegrityError``, mapped to 409 by constraint name; any other integrity
error re-raises.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    ClassificationHintCreate,
    ClassificationHintList,
    ClassificationHintPreview,
    ClassificationHintResponse,
    ClassificationHintUpdate,
)
from app.services import classification_hint_service as hints
from app.services.activity.service import ActorRef, record as record_activity

router = APIRouter(prefix="/api/v1/projects/{project_id}/classification-hints", tags=["Classification hints"])


def _is_duplicate(exc: IntegrityError) -> bool:
    """True when the violated constraint is the (project, field, lower(text)) index."""
    orig = getattr(exc, "orig", None)
    cause = getattr(orig, "__cause__", None)
    names = (
        getattr(cause, "constraint_name", None),
        getattr(getattr(orig, "diag", None), "constraint_name", None),
        str(orig or exc),
    )
    return any(hints.DUPLICATE_CONSTRAINT in (name or "") for name in names)


async def _record(
    db: AsyncSession,
    *,
    event_type: str,
    project_id: uuid.UUID,
    user: User,
    hint: Mapping[str, Any],
    changed: Optional[list[str]] = None,
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
) -> None:
    await record_activity(
        db,
        project_id=project_id,
        event_type=event_type,
        actor=ActorRef.from_user(user),
        entity_id=str(hint["id"]),
        entity_type="classification_hint",
        entity_label=hints.hint_label(hint["match_field"], hint["pattern"], hint["category"]),
        context={"changed": ", ".join(changed or [])} if changed else None,
        changed_fields=changed,
        before=before,
        after=after,
    )


def _rule_of(hint: Mapping[str, Any]) -> dict[str, Any]:
    return {key: hint.get(key) for key in ("match_field", "pattern", "category", "note", "enabled")}


@router.get("", response_model=ClassificationHintList)
async def list_classification_hints(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Every hint of the project in precedence order (the oldest matching one is used)."""
    items = await hints.list_hints(db, project_id)
    return {"items": items, "total": len(items), "max_hints": hints.MAX_HINTS_PER_PROJECT}


@router.get("/preview", response_model=ClassificationHintPreview)
async def preview_classification_hint(
    project_id: uuid.UUID,
    match_field: str = Query(..., max_length=20, description="error_message | test_name | suite_name | class_name"),
    pattern: str = Query(..., max_length=200, description="The text to look for (3-200 characters, case-insensitive)."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """How many of the project's newest 200 failures of the last 30 days the rule would match. Writes nothing."""
    return await hints.preview_hint(db, project_id, match_field, pattern)


@router.post("", response_model=ClassificationHintResponse, status_code=status.HTTP_201_CREATED)
async def create_classification_hint(
    project_id: uuid.UUID,
    body: ClassificationHintCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Add a hint (QA lead). New analyses only: existing ones are not re-classified."""
    try:
        hint = await hints.create_hint(db, project_id, current_user, body)
    except IntegrityError as exc:
        await db.rollback()
        if not _is_duplicate(exc):
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=hints.DUPLICATE_HINT) from None
    await _record(db, event_type="classification_hint.created", project_id=project_id, user=current_user,
                  hint=hint, after=_rule_of(hint))
    await db.commit()
    return hint


@router.patch("/{hint_id}", response_model=ClassificationHintResponse)
async def update_classification_hint(
    project_id: uuid.UUID,
    hint_id: uuid.UUID,
    body: ClassificationHintUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Change the fields sent (QA lead); a ledger row only when one really changed."""
    try:
        hint, before, after = await hints.update_hint(db, project_id, hint_id, current_user, body)
    except IntegrityError as exc:
        await db.rollback()
        if not _is_duplicate(exc):
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=hints.DUPLICATE_HINT) from None
    if after:
        await _record(db, event_type="classification_hint.updated", project_id=project_id, user=current_user,
                      hint=hint, changed=list(after), before=before, after=after)
        await db.commit()
    return hint


@router.delete("/{hint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_classification_hint(
    project_id: uuid.UUID,
    hint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Delete a hint (QA lead). Analyses it already classified keep their answer."""
    hint = await hints.delete_hint(db, project_id, hint_id, current_user)
    await _record(db, event_type="classification_hint.deleted", project_id=project_id, user=current_user,
                  hint=hint, before=_rule_of(hint))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
