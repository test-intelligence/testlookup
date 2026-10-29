"""TRND-03 expected run cadence — the store, the check and the status (STAGE-ONLY).

A cadence is TestLookup's own EXPECTATION ("a run of <suite | any run of the
project> arrives by <local time>"): nothing here reads or writes CI. The
window math is :mod:`app.services.run_cadence_schedule` (pure); this module
adds the rows:

* C-1 create / update / delete (``{cadence_id}`` always loaded ``WHERE id AND
  project_id``, so another project's id is the unknown-id 404);
* C-3 the matching runs of a cadence (ONE statement, the run-level
  effective-suite rule ``analytics_scope.run_touches_suite_clause``);
* C-4 ``check_project_cadences`` — the beat's per-project step: decides every
  window that ENDED since the cursor into ``run_cadence_windows`` (one
  ``ON CONFLICT DO NOTHING … RETURNING`` insert) and stages ONE ledger row and
  ONE notification staging call per cadence for the NEWLY inserted misses;
* C-5 pause / resume (TestLookup's expectation only);
* C-6 ``cadence_status`` — read-only (a GET never writes).

Services flush; the router (``routers/run_cadences.py``) and the beat
(``worker/tasks.check_run_cadences``) commit. Nothing here calls ``commit(`` or
``rollback(`` (``STAGE_ONLY_SERVICES``). Log lines carry ids and counts only —
never a suite name, a pause reason or an e-mail.
"""

from __future__ import annotations

import bisect
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    NotificationEventType,
    Project,
    RunCadence,
    RunCadenceWindow,
    TestRun,
    User,
)
from app.services.analytics_scope import run_touches_suite_clause, suite_keys
from app.services.run_cadence_schedule import (
    CadenceError,
    CadenceSpec,
    Window,
    deadlines_between,
    describe,
    first_window_start,
    local_label,
    next_deadline,
    normalise_cron,
    spec_from,
    weekdays_mask,
    weekdays_names,
    windows_ending_in,
    windows_for,
)
from app.services.run_cadence_vocab import (
    CADENCE_MODES,
    CHECK_LOOKBACK_DAYS,
    CHECK_MAX_WINDOWS_PER_TICK,
    MAX_CADENCES_PER_PROJECT,
    MISSED_LIST_MAX,
    PAUSE_MAX_DAYS,
)

__all__ = [
    "CadenceError",
    "cadence_read",
    "cadence_status",
    "check_project_cadences",
    "create_cadence",
    "delete_cadence",
    "get_cadence",
    "is_paused_at",
    "list_cadences",
    "matching_run_times",
    "pause_cadence",
    "resume_cadence",
    "update_cadence",
]

_slog = structlog.get_logger(__name__)

#: The scope's words when a cadence names no suite (the ledger label, the e-mail).
ANY_RUN_SCOPE = "any run of the project"
#: ``RunCadenceStatusItem.reason`` when no window could be judged (never "all met").
NO_EXPECTED_RUN_REASON = "No expected run fell in this window yet."
#: ``RunCadenceStatusItem.reason`` when a stored definition no longer validates
#: (a zone removed from the IANA data): nothing is judged, nothing is "met".
UNREADABLE_DEFINITION_REASON = "This expected cadence could not be read; edit it to set it again."
#: The ledger / e-mail name at most this many missed days before "and n more".
MISSED_LABELS_SHOWN = 10
#: The order the ledger's ``changed`` lists the definition fields in.
DEFINITION_FIELDS = ("suite_name", "mode", "weekdays", "expected_by", "cron", "timezone", "grace_minutes")
_TITLE_MAX = 200
_DUPLICATE_ANY_RUN = "This project already has an expected cadence for any run."


# ── small helpers ─────────────────────────────────────────────────────────────


def _utc(value: datetime) -> datetime:
    """UTC-aware (a naive stored value is UTC: every column is ``timestamptz``)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    return None if value is None else _utc(value)


def _clean_suite(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def scope_label(row: Any) -> str:
    """The ledger's ``entity_label`` and the e-mail's scope: the suite, or "any run of the project"."""
    return row.suite_name or ANY_RUN_SCOPE


def _duplicate(suite_key: Optional[str]) -> CadenceError:
    if suite_key is None:
        return CadenceError("duplicate_scope", _DUPLICATE_ANY_RUN)
    return CadenceError("duplicate_scope")


def _hhmm(value: Optional[time]) -> Optional[str]:
    return value.strftime("%H:%M") if value is not None else None


def is_paused_at(row: Any, instant: datetime) -> bool:
    """``paused_at <= instant < paused_until`` (``paused_at`` inclusive, ``paused_until`` exclusive)."""
    start, end = _aware(row.paused_at), _aware(row.paused_until)
    if start is None or end is None:
        return False
    return start <= _utc(instant) < end


def is_paused_now(row: Any, now: datetime) -> bool:
    """``RunCadenceRead.paused``: the pause has not run out (``paused_until > now``)."""
    end = _aware(row.paused_until)
    return end is not None and end > _utc(now)


def spec_of(row: Any) -> CadenceSpec:
    """The stored definition's spec (raises ``CadenceError`` if it no longer validates)."""
    return spec_from(row.mode, row.weekdays, row.expected_by, row.cron, row.timezone, row.grace_minutes)


def cadence_values(row: Any) -> dict[str, Any]:
    """The user-visible definition (no user ids): the ledger's ``before`` / ``after``."""
    return {
        "suite_name": row.suite_name,
        "mode": row.mode,
        "weekdays": weekdays_names(row.weekdays) if row.weekdays else None,
        "expected_by": _hhmm(row.expected_by),
        "cron": row.cron,
        "timezone": row.timezone,
        "grace_minutes": row.grace_minutes,
    }


def summary_of(row: Any) -> str:
    """``RunCadenceRead.summary`` ("Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min"); "" if unreadable."""
    try:
        return describe(spec_of(row), suite_name=row.suite_name)
    except CadenceError:
        return ""


def cadence_read(row: Any, *, names: Mapping[uuid.UUID, str], now: datetime) -> dict[str, Any]:
    """The ``RunCadenceRead`` body of one row (``names`` maps user ids to display names)."""
    try:
        spec: Optional[CadenceSpec] = spec_of(row)
    except CadenceError:
        spec = None
    return {
        "id": row.id,
        "project_id": row.project_id,
        **cadence_values(row),
        "summary": describe(spec, suite_name=row.suite_name) if spec else "",
        "effective_from": _aware(row.effective_from),
        "paused": is_paused_now(row, now),
        "paused_at": _aware(row.paused_at),
        "paused_until": _aware(row.paused_until),
        "paused_reason": row.paused_reason,
        "paused_by_name": names.get(row.paused_by_id) if row.paused_by_id else None,
        "next_deadline": next_deadline(spec, now) if spec else None,
        "created_by_name": names.get(row.created_by_id) if row.created_by_id else None,
        "created_at": _aware(row.created_at),
        "updated_at": _aware(row.updated_at),
    }


async def user_names(db: AsyncSession, ids: Sequence[Optional[uuid.UUID]]) -> dict[uuid.UUID, str]:
    """``{user_id: full_name or username}`` for the ids given (ONE select; none → no statement)."""
    wanted = sorted({i for i in ids if i is not None}, key=str)
    if not wanted:
        return {}
    rows = (await db.execute(
        select(User.id, User.full_name, User.username).where(User.id.in_(wanted))
    )).all()
    names: dict[uuid.UUID, str] = {}
    for uid, full_name, username in rows:
        name = (full_name or "").strip() or (username or "").strip()
        if name:
            names[uid] = name
    return names


def until_label(row: Any) -> str:
    """The pause end in the cadence's zone: ``"Fri 30 Oct 23:59 (Europe/Berlin)"`` (the ledger's ``{until}``)."""
    end = _aware(row.paused_until)
    if end is None:
        return ""
    try:
        tz = spec_of(row).tz
    except CadenceError:
        tz = ZoneInfo("UTC")
    return f"{local_label(end, tz)} {end.astimezone(tz).strftime('%H:%M')} ({tz.key})"


def people_of(rows: Sequence[Any]) -> list[Optional[uuid.UUID]]:
    """Every user id a ``RunCadenceRead`` names (creator, pauser)."""
    return [r.created_by_id for r in rows] + [r.paused_by_id for r in rows]


# ── reading ───────────────────────────────────────────────────────────────────


async def count_cadences(db: AsyncSession, project_id: uuid.UUID) -> int:
    """``count(*)`` of the project's cadences (the list's ``total``; the create's 20 bound)."""
    return int((await db.execute(
        select(func.count()).select_from(RunCadence).where(RunCadence.project_id == project_id)
    )).scalar() or 0)


async def list_cadences(db: AsyncSession, project_id: uuid.UUID) -> list[RunCadence]:
    """The project's cadences: the "any run" one first, then by suite (≤ 20)."""
    rows = (await db.execute(
        select(RunCadence)
        .where(RunCadence.project_id == project_id)
        .order_by(RunCadence.suite_key.isnot(None), RunCadence.suite_key, RunCadence.id)
        .limit(MAX_CADENCES_PER_PROJECT)
    )).scalars().all()
    return list(rows)


async def get_cadence(
    db: AsyncSession, project_id: uuid.UUID, cadence_id: uuid.UUID, *, for_update: bool = False
) -> RunCadence:
    """The cadence ``WHERE id AND project_id`` (another project's id and an unknown id: the same 404)."""
    stmt = select(RunCadence).where(RunCadence.id == cadence_id, RunCadence.project_id == project_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        raise CadenceError("cadence_not_found")
    return row


# ── writing (flush only; the router commits) ─────────────────────────────────


def _definition(
    mode: Optional[str],
    weekdays: Optional[Sequence[str]],
    expected_by: Optional[Any],
    cron: Optional[str],
    tz_name: str,
    grace_minutes: int,
) -> dict[str, Any]:
    """Validate one definition (C-1, the §1.1 sentences) → the row's definition columns."""
    if mode not in CADENCE_MODES:
        raise CadenceError("unknown_mode")
    if mode == "weekly":
        mask: Optional[int] = weekdays_mask(list(weekdays or []))
    else:
        if weekdays:
            raise CadenceError("cron_fields")
        mask = None
    spec = spec_from(mode, mask, expected_by, cron, tz_name, grace_minutes)
    return {
        "mode": mode,
        "weekdays": mask if mode == "weekly" else None,
        "expected_by": spec.times[0] if mode == "weekly" else None,
        "cron": normalise_cron(cron or "") if mode == "cron" else None,
        "timezone": tz_name,
        "grace_minutes": int(grace_minutes),
    }


async def _assert_scope_free(
    db: AsyncSession, project_id: uuid.UUID, suite_key: Optional[str], *, exclude: Optional[uuid.UUID] = None
) -> None:
    """The pre-check of C-1 (the partial unique indexes are the race backstop)."""
    stmt = select(RunCadence.id).where(RunCadence.project_id == project_id)
    if suite_key is None:
        stmt = stmt.where(RunCadence.suite_key.is_(None))
    else:
        stmt = stmt.where(RunCadence.suite_key == suite_key)
    if exclude is not None:
        stmt = stmt.where(RunCadence.id != exclude)
    if (await db.execute(stmt.limit(1))).first() is not None:
        raise _duplicate(suite_key)


async def _flush(db: AsyncSession, suite_key: Optional[str]) -> None:
    """Flush; a partial-unique-index race becomes the same 409 (``get_db`` rolls the request back)."""
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_run_cadences_project_" in str(getattr(exc, "orig", exc)):
            raise _duplicate(suite_key) from None
        raise


async def create_cadence(db: AsyncSession, project_id: uuid.UUID, body: Any, *, user: Any, now: datetime) -> RunCadence:
    """C-1: validate, refuse the 21st and a taken scope, add, flush; ``effective_from = checked_through = now``."""
    suite_name = _clean_suite(body.suite_name)
    suite_key = suite_keys([suite_name])[0] if suite_name else None
    definition = _definition(body.mode, body.weekdays, body.expected_by, body.cron,
                             body.timezone, body.grace_minutes)
    if await count_cadences(db, project_id) >= MAX_CADENCES_PER_PROJECT:
        raise CadenceError("too_many")
    await _assert_scope_free(db, project_id, suite_key)
    row = RunCadence(
        id=uuid.uuid4(), project_id=project_id, suite_name=suite_name, suite_key=suite_key,
        effective_from=now, checked_through=now, created_by_id=getattr(user, "id", None),
        **definition,
    )
    db.add(row)
    await _flush(db, suite_key)
    await db.refresh(row)
    return row


def _patched_definition(row: RunCadence, body: Any) -> dict[str, Any]:
    """The definition a PATCH asks for: sent fields over the stored ones.

    Switching the mode drops the other mode's stored fields (a weekly cadence
    turned cron keeps no weekdays), so only what was SENT can conflict.
    """
    mode = body.mode if body.mode is not None else row.mode
    same_mode = mode == row.mode
    if mode == "weekly":
        weekdays = body.weekdays if body.weekdays is not None else (
            weekdays_names(row.weekdays) if same_mode and row.weekdays else None)
        expected_by = body.expected_by if body.expected_by is not None else (
            row.expected_by if same_mode else None)
        cron = body.cron
    else:
        weekdays = body.weekdays
        expected_by = body.expected_by
        cron = body.cron if body.cron is not None else (row.cron if same_mode else None)
    tz_name = body.timezone if body.timezone is not None else row.timezone
    grace = body.grace_minutes if body.grace_minutes is not None else row.grace_minutes
    return _definition(mode, weekdays, expected_by, cron, tz_name, grace)


async def update_cadence(
    db: AsyncSession, project_id: uuid.UUID, cadence_id: uuid.UUID, body: Any, *, now: datetime
) -> tuple[RunCadence, list[str], dict[str, Any], dict[str, Any]]:
    """C-1 PATCH → ``(row, changed, before, after)``; nothing changed → no flush, no ledger row.

    Any change re-defines the expectation from NOW (``effective_from =
    checked_through = now``): earlier windows keep what was decided.
    """
    if body.clear_suite and body.suite_name is not None:
        raise CadenceError("suite_conflict")
    row = await get_cadence(db, project_id, cadence_id, for_update=True)
    if body.clear_suite:
        suite_name: Optional[str] = None
    elif body.suite_name is not None:
        suite_name = _clean_suite(body.suite_name)
    else:
        suite_name = row.suite_name
    suite_key = suite_keys([suite_name])[0] if suite_name else None
    new = {"suite_name": suite_name, **_patched_definition(row, body)}
    old = {"suite_name": row.suite_name, "mode": row.mode, "weekdays": row.weekdays,
           "expected_by": row.expected_by, "cron": row.cron, "timezone": row.timezone,
           "grace_minutes": row.grace_minutes}
    changed = [f for f in DEFINITION_FIELDS if new[f] != old[f]]
    if not changed:
        return row, [], {}, {}
    before_values = cadence_values(row)
    if suite_key != row.suite_key:
        await _assert_scope_free(db, project_id, suite_key, exclude=row.id)
    for field in DEFINITION_FIELDS:
        setattr(row, field, new[field])
    row.suite_key = suite_key
    row.effective_from = now
    row.checked_through = now
    await _flush(db, suite_key)
    await db.refresh(row)
    after_values = cadence_values(row)
    before = {f: before_values[f] for f in changed}
    after = {f: after_values[f] for f in changed}
    return row, changed, before, after


async def delete_cadence(db: AsyncSession, project_id: uuid.UUID, cadence_id: uuid.UUID) -> dict[str, Any]:
    """Delete (its windows cascade) and flush; returns the row's values for the ledger row."""
    row = await get_cadence(db, project_id, cadence_id, for_update=True)
    values = {"id": row.id, "scope": scope_label(row), "summary": summary_of(row), **cadence_values(row)}
    await db.delete(row)
    await db.flush()
    return values


async def pause_cadence(
    db: AsyncSession, project_id: uuid.UUID, cadence_id: uuid.UUID,
    *, until: datetime, reason: str, user: Any, now: datetime,
) -> RunCadence:
    """C-5: ``now < until <= now + 90 d``, a stripped non-empty reason; re-pausing REPLACES the pause."""
    row = await get_cadence(db, project_id, cadence_id, for_update=True)
    end = _utc(until)
    if end <= now:
        raise CadenceError("pause_until_past")
    if end > now + timedelta(days=PAUSE_MAX_DAYS):
        raise CadenceError("pause_too_long")
    text = (reason or "").strip()
    if not text:
        raise CadenceError("pause_reason_required")
    row.paused_at = now
    row.paused_until = end
    row.paused_reason = text
    row.paused_by_id = getattr(user, "id", None)
    await db.flush()
    await db.refresh(row)
    return row


async def resume_cadence(db: AsyncSession, project_id: uuid.UUID, cadence_id: uuid.UUID, *, now: datetime) -> RunCadence:
    """C-5: clear a CURRENT pause (else 409 ``not_paused``); decided ``paused`` windows stay as decided."""
    row = await get_cadence(db, project_id, cadence_id, for_update=True)
    if not is_paused_now(row, now):
        raise CadenceError("not_paused")
    row.paused_at = None
    row.paused_until = None
    row.paused_reason = None
    row.paused_by_id = None
    await db.flush()
    await db.refresh(row)
    return row


# ── C-3 matching runs ─────────────────────────────────────────────────────────


async def matching_run_times(db: AsyncSession, row: Any, *, after: datetime, until: datetime) -> list[datetime]:
    """``created_at`` of the cadence's runs with ``after < created_at <= until``, oldest first (ONE statement).

    Any status counts (the expectation is that a run ARRIVES). A suite
    cadence uses the run-level effective-suite rule: the run's label OR a row
    in that suite.
    """
    stmt = (
        select(TestRun.created_at)
        .where(TestRun.project_id == row.project_id, TestRun.created_at > after, TestRun.created_at <= until)
        .order_by(TestRun.created_at)
    )
    if row.suite_name:
        clause = run_touches_suite_clause([row.suite_name])
        if clause is not None:
            stmt = stmt.where(clause)
    times = (await db.execute(stmt)).scalars().all()
    return [t for t in (_aware(v) for v in times) if t is not None]


def _runs_in(times: Sequence[datetime], window_start: datetime, window_end: datetime) -> tuple[int, Optional[datetime]]:
    """(count, earliest) of the sorted ``times`` with ``start < t <= end``."""
    lo = bisect.bisect_right(times, window_start)
    hi = bisect.bisect_right(times, window_end)
    return hi - lo, (times[lo] if hi > lo else None)


# ── C-4 the check (the beat's per-project step) ───────────────────────────────


def _labels_line(labels: Sequence[str]) -> str:
    shown = list(labels[:MISSED_LABELS_SHOWN])
    more = len(labels) - len(shown)
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


def render_missed_message(
    *, project_name: str, row: Any, spec: CadenceSpec, deadlines: Sequence[datetime], dashboard_url: str,
) -> tuple[str, str]:
    """The ``run.missed`` title and body (one line per newly missed window)."""
    n = len(deadlines)
    scope = scope_label(row)
    head = "Expected run missed" if n == 1 else f"{n} expected runs missed"
    title = f"{head} — {project_name} · {scope}"
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1] + "…"
    grace = int(spec.grace.total_seconds() // 60)
    subject = f"No run of {row.suite_name}" if row.suite_name else "No run"
    lines = [
        f"{subject} arrived by {d.astimezone(spec.tz).strftime('%H:%M')} {spec.tz.key} "
        f"on {local_label(d, spec.tz)} (grace {grace} min)."
        for d in deadlines[:MISSED_LABELS_SHOWN]
    ]
    if n > MISSED_LABELS_SHOWN:
        lines.append(f"…and {n - MISSED_LABELS_SHOWN} more.")
    lines += ["Paused days are not counted.", f"Open Trends: {dashboard_url}"]
    return title, "\n".join(lines)


async def _stage_missed_alerts(
    db: AsyncSession, *, row: Any, spec: CadenceSpec, missed: Sequence[tuple[datetime, datetime]],
    project_name: str,
) -> None:
    """ONE ledger row and ONE staging call for this cadence's NEWLY missed windows.

    ``missed`` = ``(window_start, deadline)`` pairs, oldest first. The delivery
    scope names the first new window, so a redelivered tick (whose insert
    returned nothing) can never reach here for the same windows.
    """
    from app.core.config import settings
    from app.services.activity.service import ActorRef, record as record_activity
    from app.services.notification.manager import stage_scoped_preference_deliveries

    deadlines = [d for _start, d in missed]
    labels = [local_label(d, spec.tz) for d in deadlines]
    scope = scope_label(row)
    await record_activity(
        db,
        project_id=row.project_id,
        event_type="run_cadence.missed",
        actor=ActorRef.system("run-cadence-check"),
        entity_id=str(row.id),
        entity_type="run_cadence",
        entity_label=scope,
        context={"count": len(deadlines), "days": _labels_line(labels), "suite": scope},
    )
    dashboard_url = f"{settings.public_base_url}/trends"

    def build(_event: NotificationEventType) -> tuple[str, str]:
        return render_missed_message(project_name=project_name, row=row, spec=spec,
                                     deadlines=deadlines, dashboard_url=dashboard_url)

    first_start = min(start for start, _d in missed)
    await stage_scoped_preference_deliveries(
        db,
        project_id=row.project_id,
        run_id=None,
        events=[NotificationEventType.RUN_MISSED],
        build_title_fn=build,
        metadata={
            "project_name": project_name,
            "cadence_id": str(row.id),
            "dashboard_url": dashboard_url,
            "missed": [d.isoformat() for d in deadlines],
        },
        delivery_scope=f"run-missed:{row.id}:{first_start.isoformat()}",
    )


def _decide(row: Any, windows: Sequence[Window], times: Sequence[datetime], now: datetime) -> list[dict[str, Any]]:
    """One ``run_cadence_windows`` row per ENDED window: ``paused`` / ``met`` / ``missed``."""
    values = []
    for w in windows:
        count, first = _runs_in(times, w.start, w.end)
        if is_paused_at(row, w.deadline):
            outcome = "paused"
        else:
            outcome = "met" if count > 0 else "missed"
        values.append({
            "id": uuid.uuid4(), "cadence_id": row.id, "project_id": row.project_id,
            "window_start": w.start, "window_end": w.end, "deadline": w.deadline,
            "outcome": outcome, "run_count": count, "first_run_at": first, "checked_at": now,
        })
    return values


async def check_project_cadences(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> dict[str, int]:
    """C-4, stage-only (the beat commits): decide every window that ENDED since each cursor.

    (1) the project's cadences ``FOR UPDATE SKIP LOCKED`` (one a route is
    editing is skipped this tick); (2) per cadence the windows whose end is in
    ``(max(checked_through, now − 7 d), now]``, oldest first, ≤ 200; (3) ONE
    ``ON CONFLICT DO NOTHING … RETURNING`` insert — a window decided before
    returns nothing; (4) the cursor moves to the last decided end; (5) only the
    NEWLY inserted misses stage ONE ledger row and ONE notification call.
    """
    total = await count_cadences(db, project_id)
    rows = (await db.execute(
        select(RunCadence)
        .where(RunCadence.project_id == project_id)
        .order_by(RunCadence.id)
        .with_for_update(skip_locked=True)
    )).scalars().all()
    summary = {"cadences": len(rows), "decided": 0, "missed": 0, "alerts": 0,
               "skipped_locked": max(0, int(total) - len(rows))}
    project_name: Optional[str] = None
    floor = now - timedelta(days=CHECK_LOOKBACK_DAYS)
    for row in rows:
        try:
            spec = spec_of(row)
        except CadenceError as exc:
            _slog.warning("run_cadence_definition_unreadable", cadence_id=str(row.id), code=exc.code)
            continue
        cursor = _aware(row.checked_through)
        after = max(cursor, floor) if cursor is not None else floor
        windows = windows_ending_in(spec, after, now)[:CHECK_MAX_WINDOWS_PER_TICK]
        if not windows:
            continue
        times = await matching_run_times(db, row, after=windows[0].start, until=windows[-1].end)
        stmt = (
            pg_insert(RunCadenceWindow)
            .values(_decide(row, windows, times, now))
            .on_conflict_do_nothing(index_elements=["cadence_id", "window_start"])
            .returning(RunCadenceWindow.window_start, RunCadenceWindow.deadline, RunCadenceWindow.outcome)
        )
        inserted = (await db.execute(stmt)).all()
        # The cursor only; ``updated_at`` keeps meaning "the definition was edited".
        await db.execute(
            update(RunCadence)
            .where(RunCadence.id == row.id)
            .values(checked_through=windows[-1].end, updated_at=RunCadence.updated_at)
        )
        summary["decided"] += len(inserted)
        new_missed = sorted(
            ((_utc(r.window_start), _utc(r.deadline)) for r in inserted if r.outcome == "missed"),
            key=lambda pair: pair[1],
        )
        if not new_missed:
            continue
        if project_name is None:
            project_name = (await db.execute(
                select(Project.name).where(Project.id == project_id)
            )).scalar() or str(project_id)
        await _stage_missed_alerts(db, row=row, spec=spec, missed=new_missed, project_name=project_name)
        summary["missed"] += len(new_missed)
        summary["alerts"] += 1
    if summary["decided"] or summary["alerts"]:
        _slog.info("run_cadence_project_checked", project_id=str(project_id), **summary)
    return summary


# ── C-6 the status (read-only: no INSERT, no UPDATE, no commit) ───────────────


def _window_status(*, deadline: datetime, start: datetime, end: datetime, outcome: str,
                   recorded: bool, run_count: Optional[int], spec: CadenceSpec) -> dict[str, Any]:
    return {"deadline": deadline, "window_start": start, "window_end": end, "outcome": outcome,
            "recorded": recorded, "run_count": run_count, "local_label": local_label(deadline, spec.tz)}


async def _status_item(
    db: AsyncSession, row: RunCadence, stored: Sequence[Any], *,
    names: Mapping[uuid.UUID, str], start: datetime, now: datetime,
) -> dict[str, Any]:
    """One ``RunCadenceStatusItem``: stored rows as decided, the rest computed (never recorded)."""
    item: dict[str, Any] = {
        "cadence": cadence_read(row, names=names, now=now), "measured": False, "reason": None,
        "expected": 0, "met": 0, "missed": 0, "paused": 0, "pending": 0,
        "missed_windows": [], "last_run_at": None,
    }
    try:
        spec = spec_of(row)
    except CadenceError:
        item["reason"] = UNREADABLE_DEFINITION_REASON
        return item
    entries: list[dict[str, Any]] = []
    decided: set[datetime] = set()
    for w in stored:
        deadline = _utc(w.deadline)
        decided.add(deadline)
        entries.append(_window_status(deadline=deadline, start=_utc(w.window_start), end=_utc(w.window_end),
                                      outcome=w.outcome, recorded=True, run_count=w.run_count, spec=spec))
    effective = _aware(row.effective_from) or start
    computed: list[Window] = []
    deadlines = deadlines_between(spec, start, now)
    if deadlines:
        windows = windows_for(spec, deadlines, first_start=first_window_start(spec, deadlines[0]))
        computed = [w for w in windows if w.end > effective and w.deadline not in decided]
    lo = min([start] + [w.start for w in computed])
    times = await matching_run_times(db, row, after=lo, until=now)
    recent = [t for t in times if t > start]
    item["last_run_at"] = recent[-1] if recent else None
    for w in computed:
        count, _first = _runs_in(times, w.start, w.end)
        if now <= w.end:
            outcome = "pending"
        elif is_paused_at(row, w.deadline):
            outcome = "paused"
        else:
            outcome = "met" if count > 0 else "missed"
        entries.append(_window_status(deadline=w.deadline, start=w.start, end=w.end, outcome=outcome,
                                      recorded=False, run_count=count, spec=spec))
    for entry in entries:
        item[entry["outcome"]] += 1
    item["expected"] = item["met"] + item["missed"]
    item["measured"] = (item["expected"] + item["paused"]) > 0
    if not item["measured"]:
        item["reason"] = NO_EXPECTED_RUN_REASON
    missed = sorted((e for e in entries if e["outcome"] == "missed"), key=lambda e: e["deadline"], reverse=True)
    item["missed_windows"] = missed[:MISSED_LIST_MAX]
    return item


async def cadence_status(db: AsyncSession, project_id: uuid.UUID, *, days: int, now: datetime) -> dict[str, Any]:
    """C-6, read-only: per cadence the windows whose deadline is in ``(now − days, now]``.

    A stored ``run_cadence_windows`` row is shown as the beat decided it
    (``recorded: true``); a deadline of the CURRENT definition without a row is
    computed now — ``pending`` inside the grace, ``paused`` inside a pause,
    else ``met`` / ``missed`` (``recorded: false``; never written). A window
    nobody could judge is not "met": ``measured`` is false with its sentence.
    """
    start = now - timedelta(days=days)
    body: dict[str, Any] = {
        "configured": False, "days": days, "window_start": start, "window_end": now,
        "items": [], "total": 0, "expected": 0, "met": 0, "missed": 0, "paused": 0,
    }
    rows = await list_cadences(db, project_id)
    if not rows:
        return body
    stored_rows = (await db.execute(
        select(RunCadenceWindow).where(
            RunCadenceWindow.project_id == project_id,
            RunCadenceWindow.deadline > start,
            RunCadenceWindow.deadline <= now,
        )
    )).scalars().all()
    by_cadence: dict[uuid.UUID, list[Any]] = {}
    for w in stored_rows:
        by_cadence.setdefault(w.cadence_id, []).append(w)
    names = await user_names(db, people_of(rows))
    items = [
        await _status_item(db, row, by_cadence.get(row.id, []), names=names, start=start, now=now)
        for row in rows
    ]
    body.update(configured=True, items=items, total=len(items))
    for key in ("expected", "met", "missed", "paused"):
        body[key] = sum(i[key] for i in items)
    return body
