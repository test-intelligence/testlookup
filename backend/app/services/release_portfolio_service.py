"""The release KPI cards' twelve-week trend (W11 REL-03).

Each series is the card's OWN rule (``frontend/src/components/releases/
health.ts`` ``countReleases`` + ``mapping.ts``'s blockers), reconstructed at
``weeks`` week ends (Mondays 00:00 UTC, oldest first) plus a final point AT
NOW, which is the card's number:

* **In progress(T)** — releases created at or before T whose stage TODAY is
  ``in_progress``, or whose stage today is ``released`` with ``released_at``
  after T (not yet released at T).
* **Ready to ship(T)** — In progress(T) whose LATEST release-level decision
  (``phase_id IS NULL``) made at or before T is ``GO``.
* **Blocked(T)** — In progress(T) whose latest decision at or before T is
  ``NO_GO`` or carries a blocking reason; at T = now, also one with a phase
  in ``PHASE_FAILED_STATUSES`` today (the card's red phase blocker).
* **Released · 30d(T)** — releases released today with ``released_at`` in
  ``(T - 30 d, T]``.

The first three rest on TODAY's status (there is no status history yet): an
APPROXIMATION, named in ``approximated`` and in every point's title. A point
before the scope's first release is ``None`` for all four (no release
existed: absence, not zero); every later point is a count, and 0 is measured.

The two captions' totals are counts over stated denominators, never a bare
percentage: ``evaluated`` / ``go`` / ``conditional_go`` / ``no_go`` /
``not_evaluated`` by each release's LATEST decision in ``(now - weeks x 7 d,
now]``, and ``released`` / ``released_with_pack`` (a pack whose
``retention_expires_at`` is still ahead: a retired pack is not coverage).

Four statements, no N+1; none of (2)-(4) when the scope holds no release. A
GET: no write, no cache. Commit-free.
"""
from __future__ import annotations

import bisect
import uuid
from collections.abc import Collection, Iterable
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.release_planning import (
    PHASE_FAILED_STATUSES,
    PORTFOLIO_APPROXIMATED_SERIES,
    release_stage,
)
from app.models.postgres import CompliancePack, Project, Release, ReleaseGateDecision, ReleasePhase

SERIES = ("in_progress", "ready_to_ship", "blocked", "released_30d")
_WEEK = timedelta(days=7)
_RELEASED_WINDOW = timedelta(days=30)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _get(row: Any, name: str, index: int) -> Any:
    """A column of a SQLAlchemy Row, a tuple or an object with attributes."""
    if hasattr(row, name):
        return getattr(row, name)
    return row[index]


def portfolio_points(now: datetime, weeks: int) -> list[datetime]:
    """``weeks`` Mondays 00:00 UTC, oldest first (the last on or before now), then now."""
    now = _utc(now)  # type: ignore[assignment]
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), time.min, tzinfo=timezone.utc)
    return [monday - _WEEK * (weeks - 1 - i) for i in range(weeks)] + [now]


def bucket_portfolio(
    releases: Iterable[Any],
    decisions: Iterable[Any],
    failed_phase_release_ids: Collection[Any],
    packed_release_ids: Collection[Any],
    *,
    now: datetime,
    weeks: int,
) -> dict:
    """The series and totals from the four reads (pure).

    ``releases``: rows ``(id, status, created_at, released_at)``.
    ``decisions``: release-level rows ``(release_id, verdict, created_at,
    has_reasons)``, any order. The two id collections: releases with a failed
    phase today, releases with a live pack.
    """
    now = _utc(now)  # type: ignore[assignment]
    points = portfolio_points(now, weeks)

    rel: list[dict] = []
    for row in releases:
        rel.append({
            "id": str(_get(row, "id", 0)),
            "stage": release_stage(_get(row, "status", 1)),
            "created_at": _utc(_get(row, "created_at", 2)),
            "released_at": _utc(_get(row, "released_at", 3)),
        })

    # Per release: decisions ordered by time (a stable sort keeps the
    # statement's (created_at, id) order on a tie).
    by_release: dict[str, list[tuple[datetime, str, bool]]] = {}
    for row in decisions:
        at = _utc(_get(row, "created_at", 2))
        if at is None:
            continue
        by_release.setdefault(str(_get(row, "release_id", 0)), []).append(
            (at, str(_get(row, "verdict", 1) or ""), bool(_get(row, "has_reasons", 3)))
        )
    times: dict[str, list[datetime]] = {}
    for key, rows in by_release.items():
        rows.sort(key=lambda r: r[0])
        times[key] = [r[0] for r in rows]

    failed = {str(i) for i in failed_phase_release_ids}
    packed = {str(i) for i in packed_release_ids}

    def latest(release_id: str, at: datetime, after: Optional[datetime] = None) -> Optional[tuple[datetime, str, bool]]:
        """The release's last decision made at or before ``at`` (and after ``after``)."""
        stamps = times.get(release_id)
        if not stamps:
            return None
        index = bisect.bisect_right(stamps, at) - 1
        if index < 0:
            return None
        decision = by_release[release_id][index]
        if after is not None and decision[0] <= after:
            return None
        return decision

    created = [r["created_at"] for r in rel if r["created_at"] is not None]
    first_created = min(created) if created else None

    series: dict[str, list[Optional[int]]] = {name: [] for name in SERIES}
    evaluated_in_week: list[int] = []
    go_in_week: list[int] = []
    for point in points:
        week_start = point - _WEEK
        evaluated = go = 0
        for r in rel:
            decision = latest(r["id"], point, after=week_start)
            if decision is not None:
                evaluated += 1
                go += decision[1] == "GO"
        evaluated_in_week.append(evaluated)
        go_in_week.append(go)

        if first_created is None or point < first_created:
            for name in SERIES:
                series[name].append(None)
            continue
        in_progress = ready = blocked = released = 0
        for r in rel:
            stage, made, shipped = r["stage"], r["created_at"], r["released_at"]
            if stage == "released" and shipped is not None and point - _RELEASED_WINDOW < shipped <= point:
                released += 1
            if made is None or made > point:
                continue
            if not (stage == "in_progress" or (stage == "released" and shipped is not None and shipped > point)):
                continue
            in_progress += 1
            decision = latest(r["id"], point)
            if decision is not None and decision[1] == "GO":
                ready += 1
            red = decision is not None and (decision[1] == "NO_GO" or decision[2])
            if point == now and r["id"] in failed:
                red = True
            blocked += red
        series["in_progress"].append(in_progress)
        series["ready_to_ship"].append(ready)
        series["blocked"].append(blocked)
        series["released_30d"].append(released)

    window_start = now - _WEEK * weeks
    totals = {
        "evaluated": 0, "go": 0, "conditional_go": 0, "no_go": 0, "not_evaluated": 0,
        "released": 0, "released_with_pack": 0,
    }
    verdict_keys = {"GO": "go", "CONDITIONAL_GO": "conditional_go", "NO_GO": "no_go", "NOT_EVALUATED": "not_evaluated"}
    for r in rel:
        decision = latest(r["id"], now, after=window_start)
        if decision is not None:
            totals["evaluated"] += 1
            verdict_key = verdict_keys.get(decision[1])
            if verdict_key is not None:
                totals[verdict_key] += 1
        shipped = r["released_at"]
        if r["stage"] == "released" and shipped is not None and window_start < shipped <= now:
            totals["released"] += 1
            totals["released_with_pack"] += r["id"] in packed

    return {
        "weeks": weeks,
        "points_at": points,
        **series,
        "evaluated_in_week": evaluated_in_week,
        "go_in_week": go_in_week,
        "totals": totals,
        "approximated": list(PORTFOLIO_APPROXIMATED_SERIES),
        "generated_at": now,
    }


async def portfolio_trend(
    db: AsyncSession,
    *,
    project_ids: Optional[Collection[uuid.UUID]],
    weeks: int,
    now: datetime,
) -> dict:
    """The trend over the releases of ACTIVE projects in scope (``None`` = every project).

    Four statements: (1) the releases, (2) their release-level decisions,
    (3) the releases with a failed phase today, (4) those with a live pack.
    """
    now = _utc(now)  # type: ignore[assignment]
    if project_ids is not None and len(project_ids) == 0:
        return bucket_portfolio([], [], (), (), now=now, weeks=weeks)

    stmt = select(Release.id, Release.status, Release.created_at, Release.released_at).join(
        Project, and_(Project.id == Release.project_id, Project.is_active.is_(True))
    )
    if project_ids is not None:
        stmt = stmt.where(Release.project_id.in_(list(project_ids)))
    releases = (await db.execute(stmt)).all()
    if not releases:
        return bucket_portfolio([], [], (), (), now=now, weeks=weeks)
    ids = [row.id for row in releases]

    # ``blocking_reasons`` is JSON: a Python None is stored as the JSON
    # scalar null, on which json_array_length raises -- hence the json_typeof
    # guard inside the CASE (only evaluated for an array).
    has_reasons = (
        case(
            (
                func.json_typeof(ReleaseGateDecision.blocking_reasons) == "array",
                func.json_array_length(ReleaseGateDecision.blocking_reasons),
            ),
            else_=0,
        )
        > 0
    ).label("has_reasons")
    decisions = (
        await db.execute(
            select(
                ReleaseGateDecision.release_id,
                ReleaseGateDecision.verdict,
                ReleaseGateDecision.created_at,
                has_reasons,
            )
            .where(
                ReleaseGateDecision.phase_id.is_(None),
                ReleaseGateDecision.release_id.in_(ids),
                ReleaseGateDecision.created_at <= now,
            )
            .order_by(ReleaseGateDecision.release_id, ReleaseGateDecision.created_at, ReleaseGateDecision.id)
        )
    ).all()
    failed = (
        await db.execute(
            select(ReleasePhase.release_id)
            .where(
                ReleasePhase.release_id.in_(ids),
                func.lower(ReleasePhase.status).in_(PHASE_FAILED_STATUSES),
            )
            .distinct()
        )
    ).scalars().all()
    packed = (
        await db.execute(
            select(CompliancePack.release_id)
            .where(CompliancePack.release_id.in_(ids), CompliancePack.retention_expires_at > now)
            .distinct()
        )
    ).scalars().all()
    return bucket_portfolio(releases, decisions, failed, packed, now=now, weeks=weeks)


__all__ = ["SERIES", "bucket_portfolio", "portfolio_points", "portfolio_trend"]
