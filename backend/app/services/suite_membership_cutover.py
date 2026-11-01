"""The suite-membership cutover (W25d HYG-04 phase 2; contract §1.1, §1.5, §2.1).

ONE switch decides which suite-membership model the product writes and reads:
the feature flag ``suite_membership_legacy_writes`` (``FEATURE_FLAG``), seeded
ON on every install by migration ``0251``.

* ON (the seeded state): today's behaviour exactly. Both legacy writers run
  (``suite_sync_service.sync_suite_membership`` from ingestion and finalize,
  the W24 import's I-7b mirror), the three Test Management suite routes and
  the coverage map's ``needs_review_count`` read ``suite_memberships`` /
  ``suite_membership_events``, and the daily parity beat compares the two
  models (the D26 clock).
* OFF (the cutover, a guarded admin action taken only at ``cutover_ready``):
  the legacy writers, the import mirror and the parity runner stop; the routes
  and the coverage count read ``canonical_test_cases``; the canonical
  reconciler records the ``suite.sync_deleted`` activity line.

There is no third state and no per-project cutover: the flag is read with
``any_scope=True`` (a deployment-wide question; its project and role
allow-lists are ignored). A MISSING row reads as OFF (``feature_flags``
answers False for an absent key): the old tables are then neither written nor
read, and the Storage card says so loudly.

Stage-only (``STAGE_ONLY_SERVICES``): every function here reads, or stages on
the CALLER's session; nothing commits, rolls back or opens a session of its
own (``legacy_model_active`` always passes the caller's ``db`` to
``is_enabled``). Logs carry ids, counts, codes and exception TYPE names only.
Nothing here sends mail, calls an integration or uses the LLM.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Any, Iterable, Mapping, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    CanonicalTestCase,
    SettingsAuditLog,
    SuiteMembershipParityRun,
    TestRun,
    TestSuite,
)
from app.services import feature_flags
from app.services import suite_membership_parity as parity_service
from app.services import suite_parity_vocab as parity_vocab

_slog = structlog.get_logger(__name__)

FEATURE_FLAG = "suite_membership_legacy_writes"   # read by the catalogue guard (FEATURE_FLAG = "…" pattern)

# The legacy wire statuses the three routes keep answering with (suite_sync_service vocabulary).
WIRE_ACTIVE = "active"
WIRE_NEEDS_REVIEW = "needs_review"   # retired by the catalog, not yet confirmed by a person (the expander's list)
WIRE_DELETED = "deleted"             # retired AND confirmed (retirement_confirmed_at set)
WIRE_STATUSES = (WIRE_ACTIVE, WIRE_DELETED, WIRE_NEEDS_REVIEW)   # == the route's Query pattern "^(active|deleted|needs_review)$"
DERIVED_EVENT_TYPES = ("added", "deleted")    # the canonical model cannot derive "modified" / "restored"
RETIRED_DETAIL = "Not seen in the project's recent runs; retired from the catalog."   # /changes "deleted" details
EVENT_ID_NAMESPACE = uuid.UUID("6f1d3c2e-25d0-4c4b-9a77-25d0c0ffee25")   # uuid5(ns, f"{canonical_id}:{event_type}")

# The cutover POST's refusals (409) and the parity POST's refusal after the cutover (409). Fixed sentences.
NOT_READY_SENTENCE = "Not ready: day {streak} of {required} with no differences."
ALREADY_CUT_OVER_SENTENCE = "Already cut over: the old suite-membership tables are not written."
COMPARISON_RUNNING_SENTENCE = "A comparison is running; cut over when it has finished."
COMPARISON_RETIRED_SENTENCE = "The comparison is retired: the old suite-membership tables are no longer written."
CUTOVER_REASON = "HYG-04 cutover after {streak} clean UTC days"   # the flag audit row's reason (no names)

#: ``canonical_test_cases.status`` of a test the catalog still holds (``active | deleted | needs_review``).
#: Every other value is a retirement — the ONE predicate (``status <> 'active'``) that ``/deleted``,
#: ``/changes`` and the coverage map's canonical count share.
_CANONICAL_ACTIVE = "active"

# The three routes' dict keys, IN THE ORDER the legacy branch builds them (``routers/test_management_exports.py``).
# The canonical mappers build their dicts in exactly this order; a unit test reads the legacy literals from the
# handlers' source, so a key added to one side only fails there.
MEMBERSHIP_KEYS = (
    "id", "suite_name", "test_fingerprint", "test_name", "class_name", "source", "status", "review_tag",
    "last_seen_run_id", "first_seen_run_id", "managed_test_case_id", "created_at",
)
CHANGE_KEYS = (
    "id", "suite_name", "test_fingerprint", "test_name", "event_type", "run_id", "old_values", "new_values",
    "details", "created_at",
)
DELETED_KEYS = (
    "id", "original_suite", "test_fingerprint", "test_name", "class_name", "status", "review_tag",
    "deleted_at_run_id", "last_seen_run_id", "created_at",
)

# The history GET's run summary columns: the SAME select the GET and the cutover POST read (``readiness``).
PARITY_SUMMARY_COLUMNS = (
    SuiteMembershipParityRun.id, SuiteMembershipParityRun.trigger, SuiteMembershipParityRun.status,
    SuiteMembershipParityRun.started_at, SuiteMembershipParityRun.finished_at,
    SuiteMembershipParityRun.requested_by_id, SuiteMembershipParityRun.window_runs,
    SuiteMembershipParityRun.projects_checked, SuiteMembershipParityRun.keys_compared,
    SuiteMembershipParityRun.blocking_diffs, SuiteMembershipParityRun.expected_divergences,
)

#: The flag's audit rows (``feature_flags._write_audit_entry``): ``setting_key`` and the one field whose change is
#: the cutover (the row stores field NAMES, never values). Newest first, at most this many read.
AUDIT_SETTING_KEY = f"feature_flag:{FEATURE_FLAG}"
_AUDIT_FIELD = "enabled_global"
_AUDIT_ROWS_READ = 20


async def legacy_model_active(db: AsyncSession, *, fresh: bool = False) -> bool:
    """is_enabled(FEATURE_FLAG, db=db, fresh=fresh, any_scope=True). ON = the legacy tables are written and read.

    A missing row answers False (``feature_flags.is_enabled``): the old tables are then neither written nor read.
    Always the CALLER's session (``is_enabled`` opens a short session only when ``db`` is None). ``fresh=True``
    skips the shared Redis cache (the parity runner and the cutover POST read the row itself).
    """
    return await feature_flags.is_enabled(FEATURE_FLAG, db=db, fresh=fresh, any_scope=True)


def wire_status(status: str, retirement_confirmed_at: Optional[datetime]) -> str:
    """PURE: 'active' -> active; 'deleted' / 'needs_review' -> needs_review if retirement_confirmed_at is None else deleted.

    The canonical reconciler retires a test by writing ``status = 'deleted'``; a person confirms the retirement
    (``retirement_confirmed_at``). Unconfirmed retirements are the expander's "N tests need review"; confirmed
    ones are reviewed and answer ``deleted`` (contract §0.6, D-W25d-5).
    """
    if status == _CANONICAL_ACTIVE:
        return WIRE_ACTIVE
    return WIRE_NEEDS_REVIEW if retirement_confirmed_at is None else WIRE_DELETED


async def sync_legacy_if_active(db: AsyncSession, project_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Run the legacy suite-membership writer for one run only while legacy writes are ON; True when it ran.

    The one gate of both legacy writer call sites (``services/ingestion.py`` ``process_sentinel`` and the
    ``suite_sync`` finalize step of ``services/ingestion_pipeline.py``). No ``try``: a failing flag read or a
    failing sync raises into the caller's own error handling, exactly as the ungated call did. The writer is
    imported lazily (it pulls the legacy models; the flag read must not).
    """
    if not await legacy_model_active(db):
        return False
    from app.services.suite_sync_service import sync_suite_membership

    await sync_suite_membership(db, project_id, run_id)
    return True


# ── The canonical reads behind the three Test Management suite routes (§1.3, C-1) ───────────────
# Each is ``canonical_test_cases c JOIN test_suites ts ON ts.id = c.test_suite_id WHERE ts.name = :suite_name``
# (+ ``c.project_id = :project_id`` when the caller named a project — the routes' ``if project_id:``; their scope
# guard runs first, unchanged). Suite names compare exactly, as the parity harness compares them. Reads only, on
# the caller's session; one statement per route (two for /changes); no N+1.


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _id(value: Optional[uuid.UUID]) -> Optional[str]:
    return str(value) if value else None


def _field(row: Any, name: str) -> Any:
    return row[name] if isinstance(row, Mapping) else getattr(row, name)


def _wire_predicate(wanted: str) -> Any:
    """The SQL twin of ``wire_status``: the rows whose mapped status is ``wanted`` (one predicate, three readers)."""
    status = CanonicalTestCase.status
    if wanted == WIRE_ACTIVE:
        return status == _CANONICAL_ACTIVE
    if wanted == WIRE_NEEDS_REVIEW:
        return (status != _CANONICAL_ACTIVE) & CanonicalTestCase.retirement_confirmed_at.is_(None)
    if wanted == WIRE_DELETED:
        return (status != _CANONICAL_ACTIVE) & CanonicalTestCase.retirement_confirmed_at.is_not(None)
    raise ValueError(f"unknown wire status {wanted!r}")


def _in_suite(stmt: Any, suite_name: str, project_id: Optional[uuid.UUID]) -> Any:
    stmt = stmt.join(TestSuite, TestSuite.id == CanonicalTestCase.test_suite_id).where(TestSuite.name == suite_name)
    if project_id:
        stmt = stmt.where(CanonicalTestCase.project_id == project_id)
    return stmt


def membership_row(row: Any) -> dict[str, Any]:
    """PURE: one catalog row → the ``/membership`` dict (``MEMBERSHIP_KEYS``, in order)."""
    wire = wire_status(_field(row, "status"), _field(row, "retirement_confirmed_at"))
    return {
        "id": str(_field(row, "id")),
        "suite_name": _field(row, "suite_name"),
        "test_fingerprint": _field(row, "test_fingerprint"),
        "test_name": _field(row, "test_name"),
        "class_name": _field(row, "class_name"),
        "source": _field(row, "source"),
        "status": wire,
        "review_tag": WIRE_NEEDS_REVIEW if wire == WIRE_NEEDS_REVIEW else None,
        "last_seen_run_id": _id(_field(row, "last_seen_run_id")),
        "first_seen_run_id": _id(_field(row, "first_seen_run_id")),
        "managed_test_case_id": _id(_field(row, "managed_test_case_id")),
        "created_at": _iso(_field(row, "created_at")),
    }


def deleted_row(row: Any, suite_name: str) -> dict[str, Any]:
    """PURE: one unconfirmed retirement → the ``/deleted`` dict (``DELETED_KEYS``, in order)."""
    return {
        "id": str(_field(row, "id")),
        "original_suite": suite_name,
        "test_fingerprint": _field(row, "test_fingerprint"),
        "test_name": _field(row, "test_name"),
        "class_name": _field(row, "class_name"),
        "status": WIRE_NEEDS_REVIEW,
        "review_tag": WIRE_NEEDS_REVIEW,
        "deleted_at_run_id": _id(_field(row, "deleted_at_run_id")),
        "last_seen_run_id": _id(_field(row, "last_seen_run_id")),
        "created_at": _iso(_field(row, "created_at")),
    }


def event_id(canonical_id: uuid.UUID | str, event_type: str) -> str:
    """The derived event's id: stable across reads, distinct per event type of one row."""
    return str(uuid.uuid5(EVENT_ID_NAMESPACE, f"{canonical_id}:{event_type}"))


def change_event(row: Any, event_type: str) -> dict[str, Any]:
    """PURE: one catalog row + ``added`` / ``deleted`` → the ``/changes`` dict (``CHANGE_KEYS``, in order).

    ``row`` carries ``id``, ``suite_name``, ``test_fingerprint``, ``test_name``, ``run_id`` (the pointer the event
    derives from) and ``at`` (the event's time).
    """
    if event_type not in DERIVED_EVENT_TYPES:
        raise ValueError(f"not a derived event type: {event_type!r}")
    return {
        "id": event_id(_field(row, "id"), event_type),
        "suite_name": _field(row, "suite_name"),
        "test_fingerprint": _field(row, "test_fingerprint"),
        "test_name": _field(row, "test_name"),
        "event_type": event_type,
        "run_id": _id(_field(row, "run_id")),
        "old_values": None,
        "new_values": None,
        "details": RETIRED_DETAIL if event_type == "deleted" else None,
        "created_at": _iso(_field(row, "at")),
    }


def _instant(value: Optional[datetime]) -> datetime:
    """An event's time for ordering: naive = UTC; a missing time sorts last (oldest)."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def merge_changes(added: Iterable[Any], deleted: Iterable[Any], limit: int) -> list[dict[str, Any]]:
    """PURE: both derived streams → one list, ``created_at`` DESC then ``id``, cut to ``limit``."""
    pairs = [(row, "added") for row in added] + [(row, "deleted") for row in deleted]
    events = [(_instant(_field(row, "at")), change_event(row, kind)) for row, kind in pairs]
    events.sort(key=lambda pair: pair[1]["id"])
    events.sort(key=lambda pair: pair[0], reverse=True)          # stable: equal times keep the id order
    return [event for _at, event in events[: max(int(limit), 0)]]


async def canonical_membership(
    db: AsyncSession, *, suite_name: str, project_id: Optional[uuid.UUID], status: Optional[str]
) -> list[dict[str, Any]]:
    """``GET /suites/{suite_name}/membership`` from the catalog: today's dict keys, in today's order (§1.3).

    ``status`` (default ``active``) filters on the MAPPED wire status (``wire_status``), ``ORDER BY c.test_name``.
    """
    stmt = _in_suite(
        select(
            CanonicalTestCase.id, TestSuite.name.label("suite_name"), CanonicalTestCase.test_fingerprint,
            CanonicalTestCase.test_name, CanonicalTestCase.class_name, CanonicalTestCase.source,
            CanonicalTestCase.status, CanonicalTestCase.retirement_confirmed_at,
            CanonicalTestCase.last_seen_run_id, CanonicalTestCase.first_seen_run_id,
            CanonicalTestCase.managed_test_case_id, CanonicalTestCase.created_at,
        ),
        suite_name,
        project_id,
    ).where(_wire_predicate(status or WIRE_ACTIVE)).order_by(CanonicalTestCase.test_name, CanonicalTestCase.id)
    rows = (await db.execute(stmt)).all()
    return [membership_row(row) for row in rows]


async def canonical_changes(
    db: AsyncSession,
    *,
    suite_name: str,
    project_id: Optional[uuid.UUID],
    run_id: Optional[uuid.UUID],
    limit: int,
) -> list[dict[str, Any]]:
    """``GET /suites/{suite_name}/changes`` from the catalog: DERIVED ``added`` / ``deleted`` events (§1.3).

    ``added`` = a first sighting (``first_seen_run_id``; the event's time is that run's ``created_at``, the row's
    own when the run was deleted); ``deleted`` = a retirement (``status <> 'active'`` with ``deleted_at_run_id``;
    time = ``COALESCE(deleted_observed_at, run.created_at, updated_at)``). Each read ``LIMIT :limit``, merged by
    time DESC then id, cut to ``limit``. ``modified`` / ``restored`` are not derivable from the catalog.
    """
    added_at = func.coalesce(TestRun.created_at, CanonicalTestCase.created_at)
    added_stmt = _in_suite(
        select(
            CanonicalTestCase.id, TestSuite.name.label("suite_name"), CanonicalTestCase.test_fingerprint,
            CanonicalTestCase.test_name, CanonicalTestCase.first_seen_run_id.label("run_id"), added_at.label("at"),
        ).outerjoin(TestRun, TestRun.id == CanonicalTestCase.first_seen_run_id),
        suite_name,
        project_id,
    ).where(CanonicalTestCase.first_seen_run_id.is_not(None))
    deleted_at = func.coalesce(CanonicalTestCase.deleted_observed_at, TestRun.created_at, CanonicalTestCase.updated_at)
    deleted_stmt = _in_suite(
        select(
            CanonicalTestCase.id, TestSuite.name.label("suite_name"), CanonicalTestCase.test_fingerprint,
            CanonicalTestCase.test_name, CanonicalTestCase.deleted_at_run_id.label("run_id"), deleted_at.label("at"),
        ).outerjoin(TestRun, TestRun.id == CanonicalTestCase.deleted_at_run_id),
        suite_name,
        project_id,
    ).where(CanonicalTestCase.status != _CANONICAL_ACTIVE, CanonicalTestCase.deleted_at_run_id.is_not(None))
    if run_id:
        added_stmt = added_stmt.where(CanonicalTestCase.first_seen_run_id == run_id)
        deleted_stmt = deleted_stmt.where(CanonicalTestCase.deleted_at_run_id == run_id)
    added = (await db.execute(added_stmt.order_by(added_at.desc(), CanonicalTestCase.id).limit(limit))).all()
    deleted = (await db.execute(deleted_stmt.order_by(deleted_at.desc(), CanonicalTestCase.id).limit(limit))).all()
    return merge_changes(added, deleted, limit)


async def canonical_deleted(
    db: AsyncSession, *, suite_name: str, project_id: Optional[uuid.UUID]
) -> list[dict[str, Any]]:
    """``GET /suites/{suite_name}/deleted`` from the catalog: the ``needs_review`` rows (§1.3).

    Retired and not yet confirmed (a confirmed retirement is reviewed: it answers ``status=deleted`` on
    ``/membership`` instead), ``ORDER BY c.deleted_observed_at DESC NULLS LAST, c.updated_at DESC``.
    """
    stmt = _in_suite(
        select(
            CanonicalTestCase.id, CanonicalTestCase.test_fingerprint, CanonicalTestCase.test_name,
            CanonicalTestCase.class_name, CanonicalTestCase.deleted_at_run_id, CanonicalTestCase.last_seen_run_id,
            CanonicalTestCase.created_at,
        ),
        suite_name,
        project_id,
    ).where(_wire_predicate(WIRE_NEEDS_REVIEW)).order_by(
        CanonicalTestCase.deleted_observed_at.desc().nulls_last(),
        CanonicalTestCase.updated_at.desc(),
        CanonicalTestCase.id,
    )
    rows = (await db.execute(stmt)).all()
    return [deleted_row(row, suite_name) for row in rows]


# ── The readiness and the cut-over state (§1.1, §1.4, C-6, C-7) ──────────────────────────────────


async def readiness(
    db: AsyncSession, *, now: datetime, until: Optional[datetime] = None
) -> tuple[list[Any], int, bool]:
    """(rows, streak_days, cutover_ready) over the runs started in the last HISTORY_DAYS UTC days (§1.1).

    The SAME select and the SAME ``suite_membership_parity.streak_days`` the history GET uses; the GET and the
    cutover POST both call it. ``today`` is ``now``'s UTC date. ``until`` (keyword, optional) keeps only the runs
    started BEFORE it: the window that ENDS at the cut-over instant (``readiness(db, now=cut, until=cut)``), so the
    evidence stays readable after day 14 and the dates after the cutover never read as missed days. Without it the
    select is today's, byte for byte.
    """
    today = now.astimezone(timezone.utc).date()
    since = datetime.combine(today - timedelta(days=parity_vocab.HISTORY_DAYS - 1), dt_time.min, tzinfo=timezone.utc)
    stmt = select(*PARITY_SUMMARY_COLUMNS).where(SuiteMembershipParityRun.started_at >= since)
    if until is not None:
        stmt = stmt.where(SuiteMembershipParityRun.started_at < until)
    rows = list((await db.execute(
        stmt.order_by(SuiteMembershipParityRun.started_at.desc(), SuiteMembershipParityRun.id)
    )).all())
    streak = parity_service.streak_days(rows, today)
    return rows, streak, streak >= parity_vocab.REQUIRED_CLEAN_DAYS


def cut_over_audit_row(rows: Iterable[Any]) -> Optional[Any]:
    """PURE: the cutover among the flag's audit rows (newest first), or None.

    The newest row whose ``changed_fields`` names ``enabled_global``: while the flag is OFF, the newest change of
    that field is the change TO off. Only an ``update`` counts — a ``delete`` / ``create`` row also lists every
    field, and neither is a cutover (the card then says "not written", loudly). A row that changed only the
    description is skipped.
    """
    for row in rows:
        if _AUDIT_FIELD not in list(_field(row, "changed_fields") or []):
            continue
        return row if _field(row, "action") == "update" else None
    return None


async def cutover_state(db: AsyncSession, *, now: datetime) -> dict[str, Any]:
    """``{"legacy_writes", "cut_over_at", "cut_over_by_name", "streak_days_at_cutover"}`` (§1.4); never writes.

    ON → the three nulls (no further read). OFF → the newest audit row of the flag that changed ``enabled_global``
    by an ``update`` (``LIMIT 20``, filtered here): its time and actor name, and the clean-day streak over the
    ``HISTORY_DAYS`` UTC days ending at it (computed from the runs, never stored). OFF with no such row (the row
    deleted, or flipped outside the audit trail) → ``cut_over_at`` None. ``now`` is the caller's clock (the GET
    and the POST pass one); the derivation itself needs only the audit row's time.
    """
    del now  # the state is derived from the stored rows alone; the parameter keeps the callers on one clock
    state: dict[str, Any] = {
        "legacy_writes": await legacy_model_active(db),
        "cut_over_at": None,
        "cut_over_by_name": None,
        "streak_days_at_cutover": None,
    }
    if state["legacy_writes"]:
        return state
    rows = (await db.execute(
        select(
            SettingsAuditLog.action, SettingsAuditLog.actor_name, SettingsAuditLog.changed_fields,
            SettingsAuditLog.created_at,
        )
        .where(SettingsAuditLog.setting_key == AUDIT_SETTING_KEY)
        .order_by(SettingsAuditLog.created_at.desc(), SettingsAuditLog.id.desc())
        .limit(_AUDIT_ROWS_READ)
    )).all()
    cut = cut_over_audit_row(rows)
    if cut is None or _field(cut, "created_at") is None:
        return state
    at = _instant(_field(cut, "created_at"))
    _rows, streak, _ready = await readiness(db, now=at, until=at)
    state.update(cut_over_at=at, cut_over_by_name=_field(cut, "actor_name"), streak_days_at_cutover=streak)
    return state
