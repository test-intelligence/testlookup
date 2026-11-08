"""What an identity provider's SCIM ``DELETE /Users/{id}`` does (W38, W31 F-2; contract D-8, §3).

ONE ``app_settings`` row keyed ``scim_delete_action`` = ``{"action": "deactivate" | "erase"}``. No row = deactivate
(today's soft delete, the default); a stored value that is not one of :data:`ACTIONS` also reads as deactivate (the
safe, reversible choice) and logs a warning — never the value itself.

An ``SSOConfiguration`` column was rejected: an UNBOUND SCIM token has no SSO configuration to read it from.

Stage-only: :func:`set_action` flushes at most and stages the ``settings_audit_log`` row; the route
(``routers/app_settings.py``) adds the identity event and commits. Reads (:func:`get_action`, :func:`read_setting`)
never write.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AppSetting, User
from app.services.settings_audit_service import log_settings_change
from app.services.subject_rights_vocab import ERASED_DISPLAY

_slog = structlog.get_logger(__name__)

APP_SETTINGS_KEY = "scim_delete_action"
ScimDeleteAction = Literal["deactivate", "erase"]
ACTION_DEACTIVATE: Literal["deactivate"] = "deactivate"
ACTION_ERASE: Literal["erase"] = "erase"
ACTIONS: tuple[ScimDeleteAction, ...] = (ACTION_DEACTIVATE, ACTION_ERASE)
DEFAULT_ACTION: ScimDeleteAction = ACTION_DEACTIVATE
#: The PUT's coded 422 (``detail = {"code", "message"}``) for anything but one of ``ACTIONS``.
INVALID_CODE = "scim_delete_action_invalid"
INVALID_SENTENCE = "Choose deactivate or erase."
#: The ``settings_audit_log.changed_fields`` of a change (names only, never values).
CHANGED_FIELDS = ["action"]


@dataclass(frozen=True)
class ScimDeleteSetting:
    """The effective action, when the row was last written and by whom (a display, never an e-mail)."""

    action: ScimDeleteAction
    updated_at: Optional[datetime]
    updated_by_display: Optional[str]


def action_of(value: Any) -> ScimDeleteAction:
    """The effective action of a stored ``value``: one of :data:`ACTIONS`, else deactivate (+ a warning)."""
    stored = value.get("action") if isinstance(value, dict) else None
    for action in ACTIONS:
        if stored == action:
            return action
    _slog.warning("scim_delete_action_unreadable", value_type=type(stored).__name__)
    return DEFAULT_ACTION


async def get_action(db: AsyncSession) -> ScimDeleteAction:
    """The action a SCIM ``DELETE`` takes right now. ONE read, no write."""
    value = (await db.execute(select(AppSetting.value).where(AppSetting.key == APP_SETTINGS_KEY))).first()
    return DEFAULT_ACTION if value is None else action_of(value[0])


async def _display(db: AsyncSession, user_id: Optional[uuid.UUID]) -> Optional[str]:
    """Full name, else username, of the last writer; "Erased user" once erased; None when unknown."""
    if user_id is None:
        return None
    row = (await db.execute(
        select(User.full_name, User.username, User.erased_at).where(User.id == user_id)
    )).first()
    if row is None:
        return None
    return ERASED_DISPLAY if row.erased_at is not None else (row.full_name or row.username)


async def read_setting(db: AsyncSession) -> ScimDeleteSetting:
    """The GET's answer: the effective action (deactivate when there is no row), ``updated_at`` and who wrote
    it. Reads only."""
    row = (await db.execute(
        select(AppSetting.value, AppSetting.updated_at, AppSetting.updated_by).where(AppSetting.key == APP_SETTINGS_KEY)
    )).first()
    if row is None:
        return ScimDeleteSetting(action=DEFAULT_ACTION, updated_at=None, updated_by_display=None)
    return ScimDeleteSetting(
        action=action_of(row.value), updated_at=row.updated_at, updated_by_display=await _display(db, row.updated_by),
    )


async def _locked_row(db: AsyncSession) -> Optional[AppSetting]:
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == APP_SETTINGS_KEY).with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def set_action(db: AsyncSession, action: str, actor: User) -> tuple[str, str]:
    """Store ``action`` (one of :data:`ACTIONS`; anything else is a ``ValueError`` — the route answers the coded
    422 first) and stage ONE ``settings_audit_log`` row. Returns ``(before, after)`` as effective actions.

    The same effective value again writes nothing and audits nothing (a double click leaves one row). The row is
    read ``FOR UPDATE``; a first-ever write that loses a race to another admin's first write (the primary key, in
    a SAVEPOINT) re-reads the winner's row and updates it. Flushes; never commits."""
    if action not in ACTIONS:
        raise ValueError(f"unknown SCIM delete action {action!r}")
    row = await _locked_row(db)
    before = DEFAULT_ACTION if row is None else action_of(row.value)
    if before == action:
        return before, action
    if row is None:
        try:
            async with db.begin_nested():
                db.add(AppSetting(key=APP_SETTINGS_KEY, value={"action": action}, updated_by=actor.id))
                await db.flush()
        except IntegrityError:
            # another administrator's first write won the primary key: update the row they committed
            row = await _locked_row(db)
            if row is None:
                raise
            before = action_of(row.value)
            if before == action:
                return before, action
    if row is not None:
        row.value = {"action": action}
        row.updated_by = actor.id
    await log_settings_change(db, APP_SETTINGS_KEY, "updated", actor, list(CHANGED_FIELDS))
    await db.flush()
    _slog.info("scim_delete_action_updated", before=before, after=action)
    return before, action
