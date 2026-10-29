"""The cross-run insights store (W23a FAIL-11): upsert, resolve, state, list, dismiss.

Stage-only (``STAGE_ONLY_SERVICES``): every function flushes or executes and
returns; the hourly beat (``tasks.detect_cross_run_insights``) commits one
session per project per detector, and ``routers/insights.py`` commits the
dismissal with its ledger row.

An insight is one EPISODE of one detector firing on one subject:

* :func:`apply_result` opens it (the first evaluation that fires), CONFIRMS it
  on every later one that still fires (``last_seen_at`` / ``severity`` /
  ``evidence`` refreshed, ``detection_count`` + 1 -- the upsert's SET never
  touches the ``dismissed_*`` fields, so a confirmation never un-dismisses),
  and resolves it ``cleared`` only when a MEASURED evaluation judged the
  subject and found it quiet. A result that could not measure writes only the
  detector's state row and clears NOTHING (absence is not health).
* :func:`resolve_stale` resolves an open row not confirmed for 72 h.
* A resolved row is never reopened: the next firing is a new row (the partial
  unique index ``uq_project_insights_open`` covers open rows only).

A GET never writes: :func:`list_insights` is one flag read and three selects.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from sqlalchemy import case, func, literal_column, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import InsightDetectorState, ProjectInsight
from app.services import feature_flags
from app.services.insight_detectors import DetectorResult
from app.services.insight_vocab import (
    INSIGHT_ERRORS,
    INSIGHT_FLAG,
    INSIGHT_KINDS,
    INSIGHT_LINKS,
    INSIGHT_RANGES,
    INSIGHT_SEVERITIES,
    NOT_MEASURED,
    STALE_AFTER_HOURS,
)

_slog = structlog.get_logger(__name__)

#: Column bounds of ``project_insights`` (contract §1.2).
_LABEL_MAX = 1000
_NAME_MAX = 255


class InsightError(Exception):
    """A refusal of the insight routes: ``detail = {"code", "message"}``."""

    status_code = 400

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = INSIGHT_ERRORS[code]
        super().__init__(self.message)

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class InsightNotFound(InsightError):
    """Unknown id, or another project's: the same 404 (no oracle)."""

    status_code = 404

    def __init__(self) -> None:
        super().__init__("insight_not_found")


class UnknownRange(InsightError):
    """``range`` outside ``INSIGHT_RANGES``."""

    status_code = 422

    def __init__(self) -> None:
        super().__init__("unknown_range")


# ── The detector state (one row per project and kind) ─────────────────────────


async def _upsert_state(
    db: AsyncSession, project_id: uuid.UUID, kind: str, *, now: datetime,
    measured: bool, reason_code: Optional[str], subjects_measured: int, firing: int,
) -> None:
    values = {
        "evaluated_at": now, "measured": measured, "reason_code": reason_code,
        "subjects_measured": subjects_measured, "firing": firing,
    }
    statement = pg_insert(InsightDetectorState).values(project_id=project_id, kind=kind, **values)
    await db.execute(
        statement.on_conflict_do_update(index_elements=["project_id", "kind"], set_=values)
    )


async def record_detector_failure(db: AsyncSession, project_id: uuid.UUID, kind: str, *, now: datetime) -> None:
    """The detector raised: its state says so (``detector_failed``); nothing
    is opened or cleared for it."""
    await _upsert_state(
        db, project_id, kind, now=now, measured=False, reason_code="detector_failed",
        subjects_measured=0, firing=0,
    )


# ── Apply one evaluation ──────────────────────────────────────────────────────


def _upsert_statement(project_id: uuid.UUID, result: DetectorResult, finding: Any, now: datetime) -> Any:
    """The open-row upsert of one finding (``uq_project_insights_open``)."""
    statement = pg_insert(ProjectInsight).values(
        id=uuid.uuid4(),
        project_id=project_id,
        kind=result.kind,
        subject_key=finding.subject_key,
        subject_label=str(finding.subject_label)[:_LABEL_MAX],
        window_key=result.window_key,
        severity=finding.severity,
        evidence=finding.evidence,
        first_seen_at=now,
        last_seen_at=now,
        detection_count=1,
    )
    return statement.on_conflict_do_update(
        index_elements=["project_id", "kind", "subject_key", "window_key"],
        index_where=ProjectInsight.resolved_at.is_(None),
        # Never the dismissed_* fields: a confirmation keeps a dismissal.
        set_={
            "last_seen_at": now,
            "severity": statement.excluded.severity,
            "evidence": statement.excluded.evidence,
            "subject_label": statement.excluded.subject_label,
            "detection_count": ProjectInsight.detection_count + 1,
        },
    ).returning(ProjectInsight.id, literal_column("(xmax = 0)").label("inserted"))


def _clear_statement(project_id: uuid.UUID, result: DetectorResult, quiet: list[str], now: datetime) -> Any:
    """Resolve ``cleared``: the open rows of the kind whose subject THIS
    evaluation judged quiet, and (``clear_earlier_windows``) every open row of
    an earlier window of the kind -- a new billing month is a new comparison."""
    reach: list[ColumnElement[bool]] = []
    if quiet:
        reach.append(ProjectInsight.subject_key.in_(quiet))
    if result.clear_earlier_windows:
        reach.append(ProjectInsight.window_key != result.window_key)
    return (
        update(ProjectInsight)
        .where(
            ProjectInsight.project_id == project_id,
            ProjectInsight.kind == result.kind,
            ProjectInsight.resolved_at.is_(None),
            or_(*reach),
        )
        .values(resolved_at=now, resolved_reason="cleared")
        .execution_options(synchronize_session=False)
    )


async def apply_result(
    db: AsyncSession, project_id: uuid.UUID, result: DetectorResult, *, now: datetime,
) -> dict[str, int]:
    """Store one detector evaluation (stage-only; the beat commits).

    1. Not measured: only the state row (``measured False`` + its reason);
       nothing opens and NOTHING clears.
    2. Each finding: the open-row upsert (insert, or confirm).
    3. Clear: only subjects this evaluation judged (``measured_subjects``)
       that did not fire; a subject it could not judge stays open for the
       stale rule.
    4. The state row.
    """
    counts = {"inserted": 0, "confirmed": 0, "cleared": 0}
    if not result.measured:
        await _upsert_state(
            db, project_id, result.kind, now=now, measured=False,
            reason_code=result.reason_code, subjects_measured=0, firing=0,
        )
        return counts
    for finding in result.findings:
        row = (await db.execute(_upsert_statement(project_id, result, finding, now))).one()
        counts["inserted" if row.inserted else "confirmed"] += 1
    quiet = sorted(set(result.measured_subjects) - {f.subject_key for f in result.findings})
    if quiet or result.clear_earlier_windows:
        cleared = await db.execute(_clear_statement(project_id, result, quiet, now))
        counts["cleared"] = int(getattr(cleared, "rowcount", 0) or 0)
    await _upsert_state(
        db, project_id, result.kind, now=now, measured=True, reason_code=result.reason_code,
        subjects_measured=len(result.measured_subjects), firing=result.firing,
    )
    return counts


async def resolve_stale(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> int:
    """Resolve ``stale`` every open row not confirmed for ``STALE_AFTER_HOURS``:
    the subject stopped being measurable, stopped running, or (per-test
    spikes, never judged quiet) stopped spiking."""
    cutoff = now - timedelta(hours=STALE_AFTER_HOURS)
    result = await db.execute(
        update(ProjectInsight)
        .where(
            ProjectInsight.project_id == project_id,
            ProjectInsight.resolved_at.is_(None),
            ProjectInsight.last_seen_at < cutoff,
        )
        .values(resolved_at=now, resolved_reason="stale")
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


# ── Titles and links (pure) ───────────────────────────────────────────────────


def _int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "0"
    return f"{amount:.0f}" if amount.is_integer() else f"{amount:.2f}"


def render_title(kind: str, subject_label: str, evidence: Mapping[str, Any]) -> str:
    """The insight's one sentence. Numbers are formatted here (ratio 1 dp,
    percentages whole); the label is inserted as stored -- the SPA renders
    the title as text, so markup in a suite name is shown, never run."""
    label = str(subject_label)
    if kind == "perf_drift":
        if evidence.get("test_fingerprint"):
            pct = evidence.get("pct")
            change = f"+{_int(pct)} %" if pct is not None else "slower"
            return (
                f"{label} is slower: {change} vs its baseline in "
                f"{_int(evidence.get('spike_runs'))} runs this week"
            )
        raw_ratio = evidence.get("ratio")
        try:
            ratio = f"{float(raw_ratio):.1f}" if raw_ratio is not None else "?"
        except (TypeError, ValueError):
            ratio = "?"
        return (
            f"Suite {label} is {ratio}× slower than its previous "
            f"{_int(evidence.get('baseline_runs'))} runs"
        )
    if kind == "flake_rising":
        return (
            f"Flaky tests rising in suite {label} "
            f"({_int(evidence.get('previous'))} → {_int(evidence.get('current'))})"
        )
    if kind == "coverage_drop":
        return (
            f"Tests exercised fell from {_int(evidence.get('previous'))} to "
            f"{_int(evidence.get('current'))} (−{_int(evidence.get('pct'))} %) in a week"
        )
    if kind == "cost_budget":
        cap = _money(evidence.get("hard_cap_usd"))
        if evidence.get("status") == "CAPPED":
            return f"AI spend reached the ${cap} monthly cap"
        return f"AI spend is at {_int(evidence.get('utilization_pct'))} % of the ${cap} monthly cap"
    return label


def insight_link(kind: str, evidence: Mapping[str, Any]) -> tuple[str, str, Optional[str]]:
    """``(path, label, run_path)``: the path is ALWAYS one of ``INSIGHT_LINKS``;
    perf drift adds EXEC-05's run link when the stored run id is a UUID."""
    path, label = INSIGHT_LINKS[kind]
    run_path: Optional[str] = None
    if kind == "perf_drift":
        try:
            run_path = f"/runs/{uuid.UUID(str(evidence.get('newest_run_id')))}?tab=changes"
        except (TypeError, ValueError, AttributeError):
            run_path = None
    return path, label, run_path


def insight_item(row: ProjectInsight) -> dict[str, Any]:
    """One row as the wire's ``InsightItem``."""
    evidence = dict(row.evidence or {})
    path, label, run_path = insight_link(row.kind, evidence)
    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "status": "open" if row.resolved_at is None else "resolved",
        "resolved_reason": row.resolved_reason,
        "title": render_title(row.kind, row.subject_label, evidence),
        "subject_label": row.subject_label,
        "window_key": row.window_key,
        "evidence": evidence,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "resolved_at": row.resolved_at,
        "detection_count": int(row.detection_count or 1),
        "dismissed_at": row.dismissed_at,
        "dismissed_by_name": row.dismissed_by_name,
        "link": {"path": path, "label": label},
        "run_path": run_path,
    }


# ── List (the route's only read) and dismiss ──────────────────────────────────


def _detector_status(kind: str, state: Optional[InsightDetectorState], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"kind": kind, "measured": False, "reason": NOT_MEASURED["flag_off"],
                "evaluated_at": None, "subjects_measured": 0, "firing": 0}
    if state is None:
        return {"kind": kind, "measured": False, "reason": NOT_MEASURED["not_evaluated"],
                "evaluated_at": None, "subjects_measured": 0, "firing": 0}
    reason = NOT_MEASURED.get(state.reason_code) if state.reason_code else None
    return {
        "kind": kind,
        "measured": bool(state.measured),
        # Perf's half-measured case keeps its sentence while measured.
        "reason": reason,
        "evaluated_at": state.evaluated_at,
        "subjects_measured": int(state.subjects_measured or 0),
        "firing": int(state.firing or 0),
    }


async def list_insights(
    db: AsyncSession, project_id: uuid.UUID, *, range_key: str, include_dismissed: bool,
    limit: int, now: datetime,
) -> dict[str, Any]:
    """The ``InsightListResponse`` body: the rows CONFIRMED in the range (open
    first, then high -> low, then newest), ``total`` / ``dismissed_total``
    beside them, and the four detector states in ``INSIGHT_KINDS`` order.

    One flag read and three selects; writes nothing. With the flag off the
    stored rows stay readable and every detector says so.
    """
    if range_key not in INSIGHT_RANGES:
        raise UnknownRange()
    since = now - timedelta(hours=INSIGHT_RANGES[range_key])
    enabled = await feature_flags.is_enabled(INSIGHT_FLAG, db=db, project_id=project_id)
    in_range = (ProjectInsight.project_id == project_id, ProjectInsight.last_seen_at >= since)
    visible = [*in_range] if include_dismissed else [*in_range, ProjectInsight.dismissed_at.is_(None)]
    severity_order = case(
        {name: rank for rank, name in enumerate(reversed(INSIGHT_SEVERITIES))},
        value=ProjectInsight.severity, else_=len(INSIGHT_SEVERITIES),
    )
    rows = (
        await db.execute(
            select(ProjectInsight)
            .where(*visible)
            .order_by(
                case((ProjectInsight.resolved_at.is_(None), 0), else_=1),
                severity_order,
                ProjectInsight.last_seen_at.desc(),
                ProjectInsight.id,
            )
            .limit(limit)
        )
    ).scalars().all()
    visible_filter = true() if include_dismissed else ProjectInsight.dismissed_at.is_(None)
    counts = (
        await db.execute(
            select(
                func.count().filter(visible_filter).label("total"),
                func.count().filter(ProjectInsight.dismissed_at.is_not(None)).label("dismissed_total"),
            ).where(*in_range)
        )
    ).one()
    states = {
        state.kind: state
        for state in (
            await db.execute(select(InsightDetectorState).where(InsightDetectorState.project_id == project_id))
        ).scalars().all()
    }
    return {
        "enabled": bool(enabled),
        "range": range_key,
        "items": [insight_item(row) for row in rows],
        "total": int(counts.total or 0),
        "dismissed_total": int(counts.dismissed_total or 0),
        "detectors": [_detector_status(kind, states.get(kind), enabled=bool(enabled)) for kind in INSIGHT_KINDS],
        "generated_at": now,
    }


def _display_name(user: Any) -> Optional[str]:
    """Full name, else the username -- never an e-mail."""
    name = (getattr(user, "full_name", None) or "").strip() or (getattr(user, "username", None) or "").strip()
    return name[:_NAME_MAX] or None


async def dismiss_insight(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID, *, user: Any, now: datetime,
) -> tuple[ProjectInsight, bool]:
    """Hide an insight for everyone in the project, for the rest of its
    episode. ``(row, changed)``: an already dismissed row is returned
    unchanged (``False``: nothing written, no second ledger row). Loaded
    ``WHERE id AND project_id FOR UPDATE``: another project's id is the
    unknown-id 404."""
    row = (
        await db.execute(
            select(ProjectInsight)
            .where(ProjectInsight.id == insight_id, ProjectInsight.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise InsightNotFound()
    if row.dismissed_at is not None:
        return row, False
    row.dismissed_at = now
    row.dismissed_by_id = getattr(user, "id", None)
    row.dismissed_by_name = _display_name(user)
    await db.flush()
    _slog.info("insight_dismissed", project_id=str(project_id), insight_id=str(row.id), kind=row.kind)
    return row, True
