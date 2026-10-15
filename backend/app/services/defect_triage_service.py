"""Defect triage: change a defect's status, assignee, owner team and Jira link (DEF-01).

``PATCH /api/v1/projects/{project_id}/defects/{defect_id}`` (``routers/defect_jira.py``)
is the only caller of :func:`update_defect`; the review route's rejection
(``routers/deep_investigation.py``) is the only caller of
:func:`reject_pending_defect`. Both are stage-only: they validate, apply and
record the activity-ledger rows on the request session and ``flush()``; the
router commits, bumps the analytics epoch, and only then talks to Jira.

Every id is checked here, wherever it arrives, because the authorization
ratchet cannot see body ids:

* the path ``defect_id`` is loaded ``WHERE id = :id AND project_id = :pid``,
  with ONE 404 text whether it is missing or another project's (no oracle);
* the body ``assignee_user_id`` must name an eligible person of THIS project
  (the W05 release-owner rule: active, not a service or synthetic account,
  ADMIN or a member), with ONE 422 text for every way it can fail;
* a Jira key is normalised and pattern-checked; its link is built by the
  server from the configured domain, never taken from the client.

Status changes go through ``defect_status.apply_status`` (the only writer of
``resolution_status`` on an existing row). Re-opening into a test that already
has a live defect is refused before the flush (D-3): to ``OPEN`` when another
defect of the test is ``OPEN`` (the partial unique index would refuse it too,
and the router maps that ``IntegrityError`` to the same 409), and from a closed
status to ``IN_PROGRESS`` when another is ACTIVE (no index covers it).

Ledger rows carry labels and names, never user ids, in ``before``/``after``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect, ProjectMember, User
from app.services import defect_status
from app.services.activity.service import ActorRef, record as record_activity
from app.services.defect_jira_service import availability_reason, jira_browse_url
from app.services.release_owner_service import display_name, is_admin_user, is_eligible_person

logger = structlog.get_logger("services.defect_triage")

DEFECT_NOT_FOUND = "Defect not found in this project."
ASSIGNEE_INELIGIBLE = "That user cannot be assigned defects in this project."
JIRA_KEY_INVALID = "Not a Jira issue key (expected something like ABC-123)."
REOPEN_CONFLICT = "defect_already_open"
NOTHING_TO_CHANGE = "Nothing to change."

#: What a reader sees for "nobody" / "no team" in a ledger row.
UNASSIGNED_LABEL = "Unassigned"
NO_TEAM_LABEL = "none"

#: The ``changed_fields`` names a PATCH can report.
CHANGEABLE = ("resolution_status", "assignee", "owner_team", "jira")


@dataclass(frozen=True)
class TriageOutcome:
    changed_fields: tuple[str, ...]               # subset of CHANGEABLE, in that order
    status_change: Optional[tuple[str, str]]      # (before, after)
    jira: Optional[dict[str, Any]]                # the DefectJiraLinkResult fields
    write_back_target: Optional[str]              # the new status when a write-back is due
    jira_key: Optional[str]
    ledger_events: tuple[str, ...] = field(default=())


def patch_fields(patch: Any) -> set[str]:
    """The fields a PATCH body SENT, minus the note (which changes nothing)."""
    return set(getattr(patch, "model_fields_set", set()) or set()) - {"note"}


def defect_label(defect: Any) -> str:
    """The ledger's ``entity_label``: the Jira key, else the title, else the short id."""
    return (
        getattr(defect, "jira_ticket_id", None)
        or (getattr(defect, "title", None) or "")[:120]
        or str(getattr(defect, "id", ""))[:8]
    )


def _page_key(defect: Any) -> str:
    """What the Defects page's Key column shows: the Jira key, else the short id."""
    return getattr(defect, "jira_ticket_id", None) or str(getattr(defect, "id", ""))[:8]


def _status_label(word: Optional[str]) -> str:
    status = (word or "").strip().upper()
    if status in defect_status.STATUS_LABELS:
        return defect_status.STATUS_LABELS[status]
    return status.replace("_", " ").capitalize() or "Unknown"


def _team_of(value: Optional[str]) -> Optional[str]:
    team = (value or "").strip()
    if not team or team.lower() == "unknown":
        return None
    return team[:255]


async def get_defect_in_project(
    db: AsyncSession, project_id: uuid.UUID, defect_id: uuid.UUID
) -> Defect:
    """The defect, or 404 :data:`DEFECT_NOT_FOUND` (missing and foreign alike)."""
    defect = (
        await db.execute(
            select(Defect).where(Defect.id == defect_id, Defect.project_id == project_id)
        )
    ).scalar_one_or_none()
    if defect is None:
        raise HTTPException(status_code=404, detail=DEFECT_NOT_FOUND)
    return defect


def _refuse_assignee() -> HTTPException:
    return HTTPException(status_code=422, detail=ASSIGNEE_INELIGIBLE)


async def validate_assignee(db: AsyncSession, project_id: uuid.UUID, value: str) -> uuid.UUID:
    """The assignee id to store, or 422 :data:`ASSIGNEE_INELIGIBLE`.

    One text for a malformed id, an unknown user, an inactive / service /
    synthetic account, and a user who is neither an ADMIN nor a member of
    ``project_id``: a distinct text per case would let any QA engineer probe
    which user ids exist.
    """
    try:
        wanted = uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError) as exc:
        raise _refuse_assignee() from exc
    user = await db.get(User, wanted)
    if not is_eligible_person(user):
        raise _refuse_assignee()
    if is_admin_user(user):
        return wanted
    member = await db.scalar(
        select(ProjectMember.id)
        .where(ProjectMember.user_id == wanted, ProjectMember.project_id == project_id)
        .limit(1)
    )
    if member is None:
        raise _refuse_assignee()
    return wanted


def normalize_jira_key(value: str) -> str:
    """``value`` as a Jira issue key (stripped, upper-cased), or 422 :data:`JIRA_KEY_INVALID`."""
    key = defect_status.parse_jira_key(value) if isinstance(value, str) else None
    if key is None:
        raise HTTPException(status_code=422, detail=JIRA_KEY_INVALID)
    return key


async def _assignee_name(db: AsyncSession, user_id: Optional[uuid.UUID]) -> str:
    """The name the page shows for an assignee: the person, else "Unassigned"."""
    if user_id is None:
        return UNASSIGNED_LABEL
    user = await db.get(User, user_id)
    if not is_eligible_person(user):
        return UNASSIGNED_LABEL
    return display_name(user) or UNASSIGNED_LABEL


async def _live_twin(db: AsyncSession, defect: Defect, statuses: tuple[str, ...]) -> Optional[Defect]:
    """Another defect of the same project and test case whose status is in ``statuses``."""
    if defect.test_case_id is None:
        return None
    return (
        await db.execute(
            select(Defect)
            .where(
                Defect.project_id == defect.project_id,
                Defect.test_case_id == defect.test_case_id,
                Defect.id != defect.id,
                Defect.resolution_status.in_(statuses),
            )
            .order_by(Defect.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


def reopen_conflict(twin: Any) -> HTTPException:
    """The 409 for a re-open into a test that already has a live defect."""
    return HTTPException(
        status_code=409,
        detail={
            "code": REOPEN_CONFLICT,
            "message": (
                f"This test already has an open defect ({_page_key(twin)}). Close that "
                "one first, or link this failure to it."
            ),
            "defect_id": str(twin.id),
        },
    )


async def _check_reopen(db: AsyncSession, defect: Defect, current: str, target: str) -> None:
    if defect.test_case_id is None:
        return
    twin: Optional[Defect] = None
    if target == defect_status.OPEN and current != defect_status.OPEN:
        twin = await _live_twin(db, defect, (defect_status.OPEN,))
    elif target == defect_status.IN_PROGRESS and (
        current in defect_status.CLOSED_STATUSES or current == "CLOSED"
    ):
        twin = await _live_twin(db, defect, tuple(defect_status.ACTIVE_STATUSES))
    if twin is not None:
        raise reopen_conflict(twin)


async def update_defect(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    defect_id: uuid.UUID,
    actor: Any,
    patch: Any,
    jira_cfg: Optional[dict],
    jira_issue: Optional[dict],
) -> TriageOutcome:
    """Validate and apply a triage PATCH; stage its ledger rows; flush. Never commits.

    Re-loads the row (the router may have handed its connection back before a
    Jira lookup, which detaches what it loaded). Only the fields SENT are
    applied; re-sending a current value is not a change and records nothing.
    """
    defect = await get_defect_in_project(db, project_id, defect_id)
    fields = patch_fields(patch)
    now = datetime.now(timezone.utc)
    reason = availability_reason(jira_cfg) if jira_cfg is not None else None
    label = defect_label(defect)

    changed: list[str] = []
    parts: list[str] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    status_change: Optional[tuple[str, str]] = None
    jira_result: Optional[dict[str, Any]] = None
    linked_key: Optional[str] = None
    linked_validated = False

    # Validate everything before changing anything: a 422 on the second field
    # must not leave the first applied on the session.
    target: Optional[str] = None
    if "resolution_status" in fields:
        try:
            target = defect_status.normalize_status(patch.resolution_status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=defect_status.STATUS_INVALID_DETAIL) from exc

    new_assignee: Optional[uuid.UUID] = None
    if "assignee_user_id" in fields:
        raw_assignee = patch.assignee_user_id
        if raw_assignee is not None and str(raw_assignee).strip():
            new_assignee = await validate_assignee(db, project_id, raw_assignee)

    new_key: Optional[str] = None
    if "jira_key" in fields and patch.jira_key is not None and str(patch.jira_key).strip():
        new_key = normalize_jira_key(patch.jira_key)

    # ── status ────────────────────────────────────────────────────────────
    if target is not None:
        current = (defect.resolution_status or "").strip().upper()
        if current != target:
            await _check_reopen(db, defect, current, target)
        if defect_status.apply_status(defect, target, now=now):
            changed.append("resolution_status")
            status_change = (current, target)
            parts.append(f"status → {defect_status.STATUS_LABELS[target]}")
            before["status"] = _status_label(current)
            after["status"] = defect_status.STATUS_LABELS[target]

    # ── assignee ──────────────────────────────────────────────────────────
    if "assignee_user_id" in fields and new_assignee != defect.assignee_user_id:
        before["assignee"] = await _assignee_name(db, defect.assignee_user_id)
        after_name = await _assignee_name(db, new_assignee)
        after["assignee"] = after_name
        defect.assignee_user_id = new_assignee
        changed.append("assignee")
        parts.append(f"assignee → {after_name}")

    # ── owner team ────────────────────────────────────────────────────────
    if "owner_team" in fields:
        new_team = _team_of(patch.owner_team)
        old_team = _team_of(defect.owner_team)
        if new_team != old_team:
            defect.owner_team = new_team
            changed.append("owner_team")
            parts.append(f"owner team → {new_team or NO_TEAM_LABEL}")
            before["owner_team"] = old_team or NO_TEAM_LABEL
            after["owner_team"] = new_team or NO_TEAM_LABEL

    # ── Jira link ─────────────────────────────────────────────────────────
    if "jira_key" in fields:
        if new_key is None:
            jira_result = {"status": "unlinked", "reason": None, "jira_key": None,
                           "jira_url": None, "summary": None}
            if defect.jira_ticket_id or defect.jira_ticket_url:
                before["jira"] = defect.jira_ticket_id or "linked"
                after["jira"] = "none"
                defect.jira_ticket_id = None
                defect.jira_ticket_url = None
                defect.jira_status = None
                defect.external_status_at = None
                defect.external_status_conflict = False
                changed.append("jira")
                parts.append("Jira link removed")
        else:
            url = jira_browse_url(jira_cfg or {}, new_key)
            validated = (
                reason is None
                and jira_issue is not None
                and str(jira_issue.get("key") or new_key).upper() == new_key
            )
            summary = (jira_issue or {}).get("summary") if validated else None
            jira_result = {
                "status": "validated" if validated else "saved_locally",
                "reason": None if validated else reason,
                "jira_key": new_key,
                "jira_url": url,
                "summary": (str(summary)[:255] if summary else None),
            }
            if new_key != defect.jira_ticket_id or url != defect.jira_ticket_url:
                defect.jira_ticket_id = new_key
                defect.jira_ticket_url = url
                defect.external_status_conflict = False
                if validated:
                    status_name = (jira_issue or {}).get("status")
                    defect.jira_status = str(status_name)[:50] if status_name else None
                    defect.external_status_at = now
                else:
                    defect.jira_status = None
                    defect.external_status_at = None
                changed.append("jira")
                linked_key = new_key
                linked_validated = validated

    # ── write-back plan ───────────────────────────────────────────────────
    write_back_target: Optional[str] = None
    write_back_note: Optional[str] = None
    if status_change is not None and defect.jira_ticket_id:
        if reason is None and jira_cfg is not None:
            write_back_target = status_change[1]
            write_back_note = "planned"
        else:
            write_back_note = f"not_sent:{reason or 'not_configured'}"

    # ── ledger ────────────────────────────────────────────────────────────
    actor_ref = ActorRef.from_user(actor)
    events: list[str] = []
    note = (getattr(patch, "note", None) or "").strip()
    if parts:
        context: dict[str, Any] = {"changed": ", ".join(parts), "jira_write_back": write_back_note}
        if note:
            context["note"] = note[:1000]
        await record_activity(
            db,
            project_id=defect.project_id,
            event_type="defect.updated",
            actor=actor_ref,
            entity_id=defect.id,
            entity_label=label,
            entity_type="defect",
            release_id=defect.release_id,
            context=context,
            before=before,
            after=after,
            # A NEW link has its own row (defect.ticket_linked); an unlink is a change here.
            changed_fields=[
                f for f in CHANGEABLE
                if f in changed and not (f == "jira" and linked_key is not None)
            ],
        )
        events.append("defect.updated")
    if linked_key is not None:
        await record_activity(
            db,
            project_id=defect.project_id,
            event_type="defect.ticket_linked",
            actor=actor_ref,
            entity_id=defect.id,
            entity_label=label,
            entity_type="defect",
            release_id=defect.release_id,
            context={"ticket_key": linked_key, "validated": linked_validated},
        )
        events.append("defect.ticket_linked")

    await db.flush()
    ordered = tuple(f for f in CHANGEABLE if f in changed)
    logger.info(
        "defect_triaged",
        defect_id=str(defect.id), project_id=str(defect.project_id),
        changed=list(ordered), write_back=write_back_note,
    )
    return TriageOutcome(
        changed_fields=ordered,
        status_change=status_change,
        jira=jira_result,
        write_back_target=write_back_target,
        jira_key=defect.jira_ticket_id,
        ledger_events=tuple(events),
    )


async def reject_pending_defect(
    db: AsyncSession, *, defect: Defect, actor: Any, reason: str
) -> None:
    """Close a rejected suggestion as ``INVALID`` and record why (D-11). Stage-only.

    ``reject_action`` only flips ``approval_status``; a rejected CRITICAL left
    ``OPEN`` would keep blocking the release gate. The reason goes into the
    ledger row's context, nowhere on the defect.
    """
    label = defect_label(defect)
    current = (defect.resolution_status or "").strip().upper()
    defect_status.apply_status(defect, defect_status.INVALID, now=datetime.now(timezone.utc))
    await record_activity(
        db,
        project_id=defect.project_id,
        event_type="defect.updated",
        actor=ActorRef.from_user(actor),
        entity_id=defect.id,
        entity_label=label,
        entity_type="defect",
        release_id=defect.release_id,
        context={
            "changed": "status → Invalid (rejected in review)",
            "reason": (reason or "").strip()[:1000],
        },
        before={"status": _status_label(current)},
        after={"status": defect_status.STATUS_LABELS[defect_status.INVALID]},
        changed_fields=["resolution_status"],
    )
    await db.flush()
