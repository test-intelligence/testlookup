"""The release schedule as a calendar file (ICS) and a spreadsheet (CSV) (W11 REL-06).

One read (``load_schedule``: the releases of the ACTIVE projects in scope, with
their phases) and pure writers, so every byte the files carry is testable
without a database.

ICS (RFC 5545, written by hand): all-day events on UTC dates (a floating
``VALUE=DATE`` needs no VTIMEZONE), one VEVENT per dated release and one per
planned phase window, stable UIDs (``release-<uuid>@testlookup`` /
``phase-<uuid>@testlookup``) so a re-import updates instead of duplicating,
TEXT values escaped per §3.3.11, every content line folded at 75 OCTETS
(never inside a UTF-8 sequence), CRLF line ends. A cancelled release (and its
phases) is not on the calendar.

CSV: one row per phase (a release without phases gets one row), UTF-8 with a
BOM (Excel), CRLF rows, every cell neutralised against spreadsheet formula
injection (OWASP: a leading ``= + - @ TAB CR`` is prefixed with ``'``). A
cancelled release IS in the CSV, with its status: the spreadsheet is the
record, the calendar is the plan.

Download only: no subscribable URL, no token anywhere (REL-06 owner default).
Commit-free.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import ColumnElement, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.release_planning import (
    SCHEDULE_MAX_RELEASES,
    SCHEDULE_TOO_MANY,
    STAGE_RELEASED,
    release_stage,
)
from app.models.postgres import Project, Release, ReleasePhase

PRODID = "-//TestLookup//Release schedule//EN"
CSV_HEADER = (
    "project", "release", "version", "release_status", "planned_date", "released_at",
    "phase_order", "phase", "phase_type", "phase_status", "planned_start", "planned_end",
    "actual_start", "actual_end",
)
ICS_LINE_OCTETS = 75
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


class ScheduleTooLarge(ValueError):
    """More than ``SCHEDULE_MAX_RELEASES`` releases match; ``str(exc)`` is the sentence."""

    def __init__(self) -> None:
        super().__init__(SCHEDULE_TOO_MANY)


@dataclass(frozen=True)
class SchedulePhase:
    id: uuid.UUID
    name: str
    phase_type: Optional[str]
    status: Optional[str]
    order_index: int
    planned_start: Optional[datetime]
    planned_end: Optional[datetime]
    actual_start: Optional[datetime]
    actual_end: Optional[datetime]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class ScheduleRelease:
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    name: str
    version: Optional[str]
    status: Optional[str]
    planned_date: Optional[datetime]
    released_at: Optional[datetime]
    updated_at: Optional[datetime]
    phases: tuple[SchedulePhase, ...]


# ── Dates (UTC days) ─────────────────────────────────────────────────────────


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def release_day(release: ScheduleRelease) -> Optional[date]:
    """The release's day on the calendar: ``released_at`` once released, else ``planned_date``."""
    if release_stage(release.status) == "released":
        moment = _utc(release.released_at) or _utc(release.planned_date)
    else:
        moment = _utc(release.planned_date)
    return moment.date() if moment is not None else None


def phase_window(phase: SchedulePhase) -> Optional[tuple[date, date]]:
    """The phase's planned window as UTC days (inclusive), or ``None`` when it has no date.

    A phase dated at one end only is a one-day window on that day; an end
    before its start (legacy data) is read as the start day.
    """
    start, end = _utc(phase.planned_start), _utc(phase.planned_end)
    if start is None and end is None:
        return None
    first = (start or end).date()  # type: ignore[union-attr]
    last = (end or start).date()  # type: ignore[union-attr]
    return (first, max(first, last))


# ── The read ─────────────────────────────────────────────────────────────────


async def load_schedule(
    db: AsyncSession,
    *,
    project_ids: Optional[Collection[uuid.UUID]],
    start: Optional[date],
    end: Optional[date],
) -> list[ScheduleRelease]:
    """The releases in scope, with their phases: TWO statements (the rows + ``selectinload``).

    ``project_ids`` ``None`` = every active project (an ADMIN with no project
    selected); an empty collection = nothing. With ``start`` / ``end`` a
    release is kept when its day OR any phase window intersects the window.
    Ordered by project name, then the release's sort key, created_at, id.
    Raises ``ScheduleTooLarge`` above ``SCHEDULE_MAX_RELEASES`` releases.
    """
    if project_ids is not None and len(project_ids) == 0:
        return []
    stmt = (
        select(Release, Project.name)
        .join(Project, Project.id == Release.project_id)
        .where(Project.is_active.is_(True))
        .options(selectinload(Release.phases))
        .order_by(Project.name, Release.sort_key, Release.created_at, Release.id)
    )
    if project_ids is not None:
        stmt = stmt.where(Release.project_id.in_(list(project_ids)))
    if start is not None or end is not None:
        lo = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
        hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc) if end else None
        released = func.lower(Release.status).in_(STAGE_RELEASED)
        day = case(
            (released, func.coalesce(Release.released_at, Release.planned_date)),
            else_=Release.planned_date,
        )
        conditions: list[ColumnElement[bool]] = [day.is_not(None)]
        if lo is not None:
            conditions.append(day >= lo)
        if hi is not None:
            conditions.append(day < hi)
        first = func.coalesce(ReleasePhase.planned_start, ReleasePhase.planned_end)
        last = func.coalesce(ReleasePhase.planned_end, ReleasePhase.planned_start)
        phase_conditions: list[ColumnElement[bool]] = [
            ReleasePhase.release_id == Release.id, first.is_not(None),
        ]
        if hi is not None:
            phase_conditions.append(first < hi)
        if lo is not None:
            # A phase whose end precedes its start is read as its start day.
            phase_conditions.append(func.greatest(first, last) >= lo)
        stmt = stmt.where(or_(and_(*conditions), exists().where(and_(*phase_conditions))))
    stmt = stmt.limit(SCHEDULE_MAX_RELEASES + 1)

    rows = (await db.execute(stmt)).all()
    if len(rows) > SCHEDULE_MAX_RELEASES:
        raise ScheduleTooLarge()
    out: list[ScheduleRelease] = []
    for release, project_name in rows:
        phases = sorted(
            release.phases or [],
            key=lambda p: (p.order_index or 0, _utc(p.created_at) or datetime.min.replace(tzinfo=timezone.utc), str(p.id)),
        )
        out.append(
            ScheduleRelease(
                id=release.id,
                project_id=release.project_id,
                project_name=project_name or "",
                name=release.name,
                version=release.version,
                status=release.status,
                planned_date=release.planned_date,
                released_at=release.released_at,
                updated_at=release.updated_at,
                phases=tuple(
                    SchedulePhase(
                        id=p.id,
                        name=p.name,
                        phase_type=p.phase_type,
                        status=p.status,
                        order_index=p.order_index or 0,
                        planned_start=p.planned_start,
                        planned_end=p.planned_end,
                        actual_start=p.actual_start,
                        actual_end=p.actual_end,
                        updated_at=p.updated_at,
                    )
                    for p in phases
                ),
            )
        )
    return out


# ── ICS ──────────────────────────────────────────────────────────────────────

_C0_EXCEPT_LF = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def ics_escape(value: str) -> str:
    """An RFC 5545 §3.3.11 TEXT value: ``\\ ; ,`` escaped, a newline as ``\\n``, CR and other controls dropped."""
    text = (value or "").replace("\r\n", "\n")
    text = _C0_EXCEPT_LF.sub("", text)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def ics_fold(line: str) -> str:
    """One content line folded at 75 octets (RFC 5545 §3.1), never inside a UTF-8 sequence.

    Every physical line, the leading space of a continuation included, is at
    most 75 octets; the line ends are added by the caller.
    """
    if len(line.encode("utf-8")) <= ICS_LINE_OCTETS:
        return line
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    limit = ICS_LINE_OCTETS
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            pieces.append("".join(current))
            current, size = [char], width
            limit = ICS_LINE_OCTETS - 1   # a continuation starts with one space
        else:
            current.append(char)
            size += width
    pieces.append("".join(current))
    return "\r\n ".join(pieces)


def _stamp(moment: datetime) -> str:
    return _utc(moment).strftime("%Y%m%dT%H%M%SZ")  # type: ignore[union-attr]


def _day(value: date) -> str:
    return value.strftime("%Y%m%d")


def _http_base(base_url: str) -> Optional[str]:
    base = (base_url or "").strip().rstrip("/")
    if not base or re.search(r"[\s\x00-\x1f\x7f]", base):
        return None
    try:
        parts = urlsplit(base)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return base


def _iso_day(value: Optional[datetime]) -> Optional[str]:
    moment = _utc(value)
    return moment.date().isoformat() if moment is not None else None


def write_ics(releases: Sequence[ScheduleRelease], *, calendar_name: str, now: datetime, base_url: str) -> bytes:
    """The schedule as an iCalendar file (bytes, UTF-8, CRLF)."""
    base = _http_base(base_url)
    stamp = _stamp(now)
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]

    def event(uid: str, first: date, last: date, summary: str, description: str,
              modified: Optional[datetime], url: Optional[str]) -> None:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{stamp}")
        if modified is not None:
            lines.append(f"LAST-MODIFIED:{_stamp(modified)}")
        lines.append(f"DTSTART;VALUE=DATE:{_day(first)}")
        lines.append(f"DTEND;VALUE=DATE:{_day(last + timedelta(days=1))}")   # exclusive
        lines.append(f"SUMMARY:{ics_escape(summary)}")
        lines.append(f"DESCRIPTION:{ics_escape(description)}")
        if url:
            lines.append(f"URL:{url}")
        lines.append("END:VEVENT")

    for release in releases:
        stage = release_stage(release.status)
        if stage == "cancelled":
            continue
        url = f"{base}/releases/{release.id}" if base else None
        day = release_day(release)
        if day is not None:
            word = "released" if stage == "released" else "planned"
            event(
                f"release-{release.id}@testlookup", day, day,
                f"Release {release.name} ({word})",
                f"Project: {release.project_name}\nStatus: {release.status or ''}\nPhases: {len(release.phases)}",
                release.updated_at, url,
            )
        for phase in release.phases:
            window = phase_window(phase)
            if window is None:
                continue
            details = [f"Type: {phase.phase_type or ''}", f"Status: {phase.status or ''}"]
            actual_first, actual_last = _iso_day(phase.actual_start), _iso_day(phase.actual_end)
            if actual_first or actual_last:
                details.append(f"Actual: {actual_first or '?'} to {actual_last or '?'}")
            details.append(f"Release: {release.name} ({release.project_name})")
            event(
                f"phase-{phase.id}@testlookup", window[0], window[1],
                f"{release.name} · {phase.name}",
                "\n".join(details),
                phase.updated_at, url,
            )

    lines.append("END:VCALENDAR")
    return ("\r\n".join(ics_fold(line) for line in lines) + "\r\n").encode("utf-8")


# ── CSV ──────────────────────────────────────────────────────────────────────


def csv_safe(value: object) -> str:
    """A cell a spreadsheet will not run: a leading ``= + - @ TAB CR`` gets a ``'`` prefix."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _FORMULA_LEADS:
        return "'" + text
    return text


def _iso(value: Optional[datetime]) -> str:
    moment = _utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment is not None else ""


def write_csv(releases: Sequence[ScheduleRelease]) -> bytes:
    """The schedule as CSV: UTF-8 with a BOM, CRLF rows, one row per phase."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for release in releases:
        head = [
            release.project_name, release.name, release.version or "", release.status or "",
            _iso(release.planned_date), _iso(release.released_at),
        ]
        if not release.phases:
            writer.writerow([csv_safe(v) for v in (*head, "", "", "", "", "", "", "", "")])
            continue
        for phase in release.phases:
            writer.writerow([
                csv_safe(v)
                for v in (
                    *head,
                    phase.order_index, phase.name, phase.phase_type or "", phase.status or "",
                    _iso(phase.planned_start), _iso(phase.planned_end),
                    _iso(phase.actual_start), _iso(phase.actual_end),
                )
            ])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# ── File name ────────────────────────────────────────────────────────────────


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return slug[:60].strip("-")


def schedule_filename(scope_label: str, fmt: str, today: date) -> str:
    """``testlookup-releases-<slug>-YYYYMMDD.<fmt>``; the slug is ``[a-z0-9-]``, at most 60."""
    slug = _slug(scope_label) or "project"
    return f"testlookup-releases-{slug}-{today.strftime('%Y%m%d')}.{fmt}"


__all__ = [
    "CSV_HEADER",
    "PRODID",
    "ScheduleRelease",
    "SchedulePhase",
    "ScheduleTooLarge",
    "csv_safe",
    "ics_escape",
    "ics_fold",
    "load_schedule",
    "phase_window",
    "release_day",
    "schedule_filename",
    "write_csv",
    "write_ics",
]
