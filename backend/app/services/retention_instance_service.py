"""The single-row instance retention policy (W22b ACT-06, contract §2.2 W-6).

Every retention field a project leaves NULL inherits the instance value, and an
instance NULL falls back to the code default. ``retention_service`` resolves
those three steps per field in ONE place (``get_effective_policy`` and its SQL
twins ``effective_policy_columns`` / ``effective_enabled_expr``); this module
owns the instance row itself:

* ``get_instance_policy`` — ONE statement; a missing row reads as the code
  defaults (``enabled = False``); a read ERROR propagates (fail closed: an
  unreadable instance policy fails the preview / purge / GET rather than
  silently applying code defaults that may be SHORTER than the instance's);
* ``upsert_instance_policy`` — the plain audited save: values, ``clear`` (back
  to the code default) and switching the default OFF (the safe direction).
  Switching it ON here is refused (``enable_via_flow``);
* ``instance_enable_preview`` / ``enable_instance_policy`` — D12: the projects
  whose effective ``enabled`` would flip (no policy row, or a NULL ``enabled``;
  an explicit ``false`` stays off — per field, explicit wins), a hash of that
  set plus the instance values, and the typed phrase. The preview writes
  NOTHING; the enable takes the instance row ``FOR UPDATE``, recomputes the set
  in the same transaction and refuses a stale hash or an inexact phrase.

Stage-only (``STAGE_ONLY_SERVICES``): every write is ``flush``ed on the
caller's session and the router commits. Every write stages ONE
``settings_audit_log`` row (``setting_key = "retention_instance_policy"``,
action ``updated`` / ``disabled`` / ``enabled``). No ``app.services.activity``
import: the settings router is unscoped (no ledger row). Log lines carry
counts, field names and the actor id — never a project name.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy import DateTime, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    InstanceRetentionPolicy,
    Project,
    ProjectRetentionPolicy,
    SettingsAuditLog,
    TestRun,
    User,
)
from app.services.canonical_json import stable_json_sha256
from app.services.retention_vocab import (
    INHERITABLE_FIELDS,
    INSTANCE_ENABLE_PHRASE,
    INSTANCE_ENABLE_PHRASE_ONE,
    INSTANCE_POLICY_AUDIT_KEY,
    INSTANCE_PREVIEW_MAX_ITEMS,
    KEEP_LAST_RUNS_MAX,
    KEEP_LAST_RUNS_MIN,
    POLICY_ERROR_SENTENCES,
    PURGE_WINDOW_MAX,
    PURGE_WINDOW_MIN,
)

_slog = structlog.get_logger(__name__)

#: The instance policy is ONE row (``ck_instance_retention_single_row``).
INSTANCE_ID = 1
#: The instance fields ``clear`` may reset to the code default. ``enabled`` is
#: not among them: it is switched off with ``enabled: false`` and on only
#: through the preview + typed confirmation.
CLEARABLE_FIELDS: tuple[str, ...] = tuple(f for f in INHERITABLE_FIELDS if f != "enabled")
#: The fields ``effective_after`` reports per affected project (what it would
#: run with): the four windows, the floor and the window hour.
_AFTER_FIELDS = CLEARABLE_FIELDS


class InstancePolicyError(Exception):
    """A refusal on the instance routes: ``code`` is a ``POLICY_ERROR_SENTENCES``
    key, ``status_code`` the HTTP status the router answers with."""

    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(POLICY_ERROR_SENTENCES[code])
        self.code = code
        self.status_code = status_code

    @property
    def message(self) -> str:
        return POLICY_ERROR_SENTENCES[self.code]


@dataclass(frozen=True)
class InstanceRetentionValues:
    """The stored instance values (``None`` = the code default) plus who / when.

    The metadata fields are excluded from ``==`` so two reads of the same policy
    compare equal whatever their timestamps.
    """

    enabled: bool = False
    raw_events_days: Optional[int] = None
    runs_days: Optional[int] = None
    artifacts_days: Optional[int] = None
    audit_days: Optional[int] = None
    keep_last_runs: Optional[int] = None
    purge_window_start_utc: Optional[int] = None
    enabled_at: Optional[datetime] = dc_field(default=None, compare=False)
    enabled_by_id: Optional[uuid.UUID] = dc_field(default=None, compare=False)
    updated_at: Optional[datetime] = dc_field(default=None, compare=False)

    def stored(self) -> dict[str, Any]:
        """The seven inheritable fields as stored (``None`` = the code default)."""
        return {f: getattr(self, f) for f in INHERITABLE_FIELDS}


def _int_or_none(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _values_from_row(row: Any) -> InstanceRetentionValues:
    """The row as values. ``getattr`` keeps ``SimpleNamespace`` fakes valid."""
    if row is None:
        return InstanceRetentionValues()
    return InstanceRetentionValues(
        enabled=bool(getattr(row, "enabled", False)),
        raw_events_days=_int_or_none(getattr(row, "raw_events_days", None)),
        runs_days=_int_or_none(getattr(row, "runs_days", None)),
        artifacts_days=_int_or_none(getattr(row, "artifacts_days", None)),
        audit_days=_int_or_none(getattr(row, "audit_days", None)),
        keep_last_runs=_int_or_none(getattr(row, "keep_last_runs", None)),
        purge_window_start_utc=_int_or_none(getattr(row, "purge_window_start_utc", None)),
        enabled_at=getattr(row, "enabled_at", None),
        enabled_by_id=getattr(row, "enabled_by_id", None),
        updated_at=getattr(row, "updated_at", None),
    )


def code_defaults() -> dict[str, Any]:
    """The seven code defaults — what a NULL means at both levels."""
    from app.services import retention_service

    return dict(retention_service.CODE_DEFAULTS)


def instance_effective(values: InstanceRetentionValues) -> dict[str, Any]:
    """``COALESCE(instance, code default)`` per field (``enabled`` as stored)."""
    defaults = code_defaults()
    out: dict[str, Any] = {"enabled": bool(values.enabled)}
    for field in CLEARABLE_FIELDS:
        value = getattr(values, field)
        out[field] = int(value) if value is not None else defaults[field]
    return out


def enable_phrase(count: int) -> str:
    """The exact confirmation an admin types to switch the default on."""
    return INSTANCE_ENABLE_PHRASE_ONE if count == 1 else INSTANCE_ENABLE_PHRASE.format(count=count)


def preview_hash(project_ids: Sequence[uuid.UUID], values: InstanceRetentionValues) -> str:
    """sha256 of the sorted affected ids + the instance's effective values."""
    return stable_json_sha256(
        {"project_ids": sorted(str(pid) for pid in project_ids), "values": instance_effective(values)}
    )


def _actor_name(actor: Any) -> Optional[str]:
    if actor is None:
        return None
    return getattr(actor, "username", None) or getattr(actor, "full_name", None)


# ── Reads ────────────────────────────────────────────────────────────────────


async def get_instance_policy(db: AsyncSession) -> InstanceRetentionValues:
    """The instance policy: ONE statement. A missing row reads as the code
    defaults (``enabled = False``); a statement ERROR propagates (fail closed)."""
    row = (
        await db.execute(
            select(InstanceRetentionPolicy).where(InstanceRetentionPolicy.id == INSTANCE_ID)
        )
    ).scalar_one_or_none()
    return _values_from_row(row)


async def inheriting_counts(db: AsyncSession) -> dict[str, int]:
    """Per inheritable field, the projects whose value comes from the instance
    (no policy row, or a NULL column): ONE statement over ``projects LEFT JOIN
    project_retention_policies``, one ``count(*) FILTER`` per field. Like the
    beat, it does not filter ``is_active``."""
    prp = ProjectRetentionPolicy
    columns = [
        func.count().filter(or_(prp.id.is_(None), getattr(prp, field).is_(None))).label(field)
        for field in INHERITABLE_FIELDS
    ]
    row = (
        await db.execute(
            select(*columns).select_from(Project).outerjoin(prp, prp.project_id == Project.id)
        )
    ).one()
    return {field: int(row[index] or 0) for index, field in enumerate(INHERITABLE_FIELDS)}


async def instance_policy_read(db: AsyncSession) -> dict[str, Any]:
    """The ``InstanceRetentionPolicyRead`` payload: the stored values (NULL =
    the code default), ``code_defaults``, ``effective``, ``inheriting_projects``
    and who switched it on and when. Reads only."""
    values = await get_instance_policy(db)
    counts = await inheriting_counts(db)
    enabled_by_name: Optional[str] = None
    if values.enabled_by_id is not None:
        names = (
            await db.execute(
                select(User.full_name, User.username).where(User.id == values.enabled_by_id)
            )
        ).first()
        if names is not None:
            enabled_by_name = names[0] or names[1]
    return {
        **values.stored(),
        "code_defaults": code_defaults(),
        "effective": instance_effective(values),
        "inheriting_projects": counts,
        "enabled_at": values.enabled_at,
        "enabled_by_name": enabled_by_name,
        "updated_at": values.updated_at,
    }


async def _locked_row(db: AsyncSession) -> Any:
    """The instance row ``FOR UPDATE`` (serialises two saves / enables), its
    attributes re-read under the lock. A missing row (a database that never ran
    0242's insert) is created OFF first."""
    stmt = (
        select(InstanceRetentionPolicy)
        .where(InstanceRetentionPolicy.id == INSTANCE_ID)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = InstanceRetentionPolicy(id=INSTANCE_ID, enabled=False)
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


# ── The plain save (values, clear, switch OFF) ───────────────────────────────


async def upsert_instance_policy(
    db: AsyncSession,
    *,
    actor: Any,
    values: Mapping[str, Any],
    clear: Sequence[str] = (),
    enabled: Optional[bool] = None,
) -> InstanceRetentionValues:
    """Save instance values. ``values`` maps a ``CLEARABLE_FIELDS`` name to its
    new value (``None`` = keep); ``clear`` resets fields to the code default
    (NULL). ``enabled=True`` is refused (``enable_via_flow``: the preview and the
    typed phrase are the only way on); ``enabled=False`` switches the default
    off at once — the safe direction — and the audit row says ``disabled``.

    The merged EFFECTIVE values (given ▸ cleared = code default ▸ current) pass
    the project policy's bounds and ``audit_days >= runs_days`` check
    (``RetentionValidationError``, the same sentences). Stages + flushes only.
    """
    from app.services import retention_service

    if enabled is True:
        raise InstancePolicyError("enable_via_flow", 422)
    clear_list = list(clear or ())
    if any(field not in CLEARABLE_FIELDS for field in clear_list):
        raise InstancePolicyError("unknown_inherit_field", 422)
    given = {
        field: int(value)
        for field, value in (values or {}).items()
        if field in CLEARABLE_FIELDS and value is not None
    }
    if set(given) & set(clear_list):
        raise InstancePolicyError("inherit_conflict", 422)

    defaults = code_defaults()
    row = await _locked_row(db)
    before = _values_from_row(row)
    merged: dict[str, int] = {}
    for field in CLEARABLE_FIELDS:
        current = getattr(before, field)
        if field in given:
            merged[field] = given[field]
        elif field in clear_list or current is None:
            merged[field] = int(defaults[field])
        else:
            merged[field] = int(current)
    retention_service._validate_merged(merged)
    if not KEEP_LAST_RUNS_MIN <= merged["keep_last_runs"] <= KEEP_LAST_RUNS_MAX:
        raise retention_service.RetentionValidationError(
            f"keep_last_runs must be between {KEEP_LAST_RUNS_MIN} and {KEEP_LAST_RUNS_MAX} "
            f"(got {merged['keep_last_runs']})"
        )
    if not PURGE_WINDOW_MIN <= merged["purge_window_start_utc"] <= PURGE_WINDOW_MAX:
        raise retention_service.RetentionValidationError(
            f"purge_window_start_utc must be between {PURGE_WINDOW_MIN} and {PURGE_WINDOW_MAX} "
            f"(got {merged['purge_window_start_utc']})"
        )

    changes: dict[str, Any] = {}
    for field in CLEARABLE_FIELDS:
        if field in given:
            new_value: Optional[int] = given[field]
        elif field in clear_list:
            new_value = None
        else:
            continue
        old_value = getattr(before, field)
        if old_value != new_value:
            changes[field] = {"old": old_value, "new": new_value}
            setattr(row, field, new_value)
    action = "updated"
    if enabled is False and before.enabled:
        changes["enabled"] = {"old": True, "new": False}
        row.enabled = False
        action = "disabled"
    row.updated_by_user_id = getattr(actor, "id", None)
    db.add(
        SettingsAuditLog(
            setting_key=INSTANCE_POLICY_AUDIT_KEY,
            action=action,
            actor_id=getattr(actor, "id", None),
            actor_name=_actor_name(actor),
            changed_fields={"changes": changes},
        )
    )
    await db.flush()
    await db.refresh(row)
    _slog.info(
        "retention_instance_policy_saved",
        action=action,
        fields=sorted(changes),
        actor_id=str(getattr(actor, "id", None)),
    )
    return _values_from_row(row)


# ── D12: the enable preview and the typed enable ─────────────────────────────


def _affected_predicate() -> Any:
    """The projects whose effective ``enabled`` comes from the instance: no
    policy row, or a NULL ``enabled``. An explicit ``false`` stays off."""
    prp = ProjectRetentionPolicy
    return or_(prp.id.is_(None), prp.enabled.is_(None))


async def _affected(db: AsyncSession, *, now: datetime) -> tuple[list[Any], dict[uuid.UUID, int]]:
    """Every affected project (ordered by name) with what it would run with,
    and the runs older than each one's effective runs window: TWO statements
    (the set, then ONE GROUP BY over ``test_runs``). Reads only."""
    from app.services import retention_service

    prp, inst = ProjectRetentionPolicy, InstanceRetentionPolicy
    columns = retention_service.effective_policy_columns(prp, inst)
    rows = (
        await db.execute(
            select(
                Project.id,
                Project.name,
                Project.is_active,
                prp.id.is_not(None).label("has_policy_row"),
                *(columns[field].label(field) for field in _AFTER_FIELDS),
            )
            .select_from(Project)
            .outerjoin(prp, prp.project_id == Project.id)
            .outerjoin(inst, inst.id == INSTANCE_ID)
            .where(_affected_predicate())
            .order_by(Project.name, Project.id)
        )
    ).all()
    past: dict[uuid.UUID, int] = {}
    if rows:
        cutoff = literal(now, DateTime(timezone=True)) - func.make_interval(0, 0, 0, columns["runs_days"])
        counted = (
            await db.execute(
                select(TestRun.project_id, func.count(TestRun.id))
                .select_from(TestRun)
                .outerjoin(prp, prp.project_id == TestRun.project_id)
                .outerjoin(inst, inst.id == INSTANCE_ID)
                .where(_affected_predicate(), TestRun.created_at < cutoff)
                .group_by(TestRun.project_id)
            )
        ).all()
        past = {project_id: int(count or 0) for project_id, count in counted}
    return list(rows), past


def _preview_payload(
    rows: Sequence[Any], past: Mapping[uuid.UUID, int], values: InstanceRetentionValues,
) -> dict[str, Any]:
    ids = [row[0] for row in rows]
    items = []
    for row in rows[:INSTANCE_PREVIEW_MAX_ITEMS]:
        after = {field: int(row[4 + index]) for index, field in enumerate(_AFTER_FIELDS)}
        after["audit_days"] = max(after["audit_days"], after["runs_days"])   # the resolver's clamp (D-9)
        items.append(
            {
                "project_id": row[0],
                "name": row[1],
                "is_active": bool(row[2]),
                "has_policy_row": bool(row[3]),
                "effective_after": after,
                "runs_past_window": int(past.get(row[0], 0)),
            }
        )
    return {
        "items": items,
        "total": len(ids),
        "truncated": len(ids) > INSTANCE_PREVIEW_MAX_ITEMS,
        "preview_hash": preview_hash(ids, values),
        "confirmation_phrase": enable_phrase(len(ids)),
        "already_enabled": False,
    }


async def instance_enable_preview(db: AsyncSession, *, now: datetime) -> dict[str, Any]:
    """``InstanceEnablePreviewResponse``: the projects switching the default on
    would affect (≤ ``INSTANCE_PREVIEW_MAX_ITEMS`` listed, ``total`` always),
    the hash the enable must echo and the phrase it must type. Writes NOTHING.
    Already on → ``already_enabled`` with no items."""
    values = await get_instance_policy(db)
    if values.enabled:
        return {
            "items": [],
            "total": 0,
            "truncated": False,
            "preview_hash": preview_hash([], values),
            "confirmation_phrase": enable_phrase(0),
            "already_enabled": True,
        }
    rows, past = await _affected(db, now=now)
    return _preview_payload(rows, past, values)


async def enable_instance_policy(
    db: AsyncSession, *, actor: Any, confirmation: str, preview_hash: str, now: datetime,
) -> InstanceRetentionValues:
    """Switch the instance default ON (D12). Under ``FOR UPDATE`` on the
    instance row: already on → 409 ``already_enabled``; the affected set
    recomputed in this transaction must hash to ``preview_hash`` (else 409
    ``preview_stale``) and ``confirmation`` must equal the phrase exactly (else
    422 ``confirmation_mismatch``). Stages ``enabled`` / ``enabled_at`` /
    ``enabled_by_id`` and ONE ``settings_audit_log`` row; flushes only."""
    row = await _locked_row(db)
    values = _values_from_row(row)
    if values.enabled:
        raise InstancePolicyError("already_enabled", 409)
    rows, past = await _affected(db, now=now)
    payload = _preview_payload(rows, past, values)
    if payload["preview_hash"] != preview_hash:
        raise InstancePolicyError("preview_stale", 409)
    if confirmation != payload["confirmation_phrase"]:
        raise InstancePolicyError("confirmation_mismatch", 422)
    row.enabled = True
    row.enabled_at = now
    row.enabled_by_id = getattr(actor, "id", None)
    row.updated_by_user_id = getattr(actor, "id", None)
    project_ids = [str(r[0]) for r in rows]
    db.add(
        SettingsAuditLog(
            setting_key=INSTANCE_POLICY_AUDIT_KEY,
            action="enabled",
            actor_id=getattr(actor, "id", None),
            actor_name=_actor_name(actor),
            changed_fields={
                "count": len(project_ids),
                "project_ids": project_ids[:INSTANCE_PREVIEW_MAX_ITEMS],
                "preview_hash": payload["preview_hash"],
                "values": {**instance_effective(values), "enabled": True},
            },
        )
    )
    await db.flush()
    await db.refresh(row)
    _slog.info(
        "retention_instance_policy_enabled",
        count=len(project_ids),
        actor_id=str(getattr(actor, "id", None)),
    )
    return _values_from_row(row)
