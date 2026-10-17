"""The four-signal flakiness score on Flaky tests › Detected (W09 FLK-03).

``GET /projects/{id}/flaky-coach`` lists the tests the failure-rate detector
considers unstable. The nightly sweep (``recompute_flaky_scores`` →
``flaky_score_service.score_project`` / ``store_scores``) separately scores
each test from four signals and stores one ``flaky_score`` row per
(project, fingerprint). This module joins the two for the page's rows, so
every Detected row says what the score knows about it:

* ``"scored"``: the project's row — score, confidence band, observations,
  window, when it was computed, and the four components with the ROW's
  stored weights (weights are stored per row so a later change does not
  reinterpret history; a weight missing from an old row reads the default);
* ``"insufficient"``: no row and fewer than ``MIN_OBSERVATIONS`` executions
  in the sweep's window. ``store_scores`` skips such a test rather than
  writing a 0.0, so there is no row to read: the count is computed here with
  the sweep's own definition (executions in the last 30 days);
* ``"pending"``: no row although the test has enough executions — the sweep
  has not scored it yet.

Two statements at most, both on the request's session and both indexed:
the score rows (``ux_flaky_score_project_fingerprint``) and, only for the
fingerprints without a row, the execution count. Fingerprints are unique per
project only (``hash(test_name + class_name)``), so neither statement ever
looks one up without the project.

A secondary read must never fail the page: the work runs in a SAVEPOINT and
any exception leaves every entry's ``flaky_score`` ``None`` (the UI prints
"—", "Score not available") with one warning. Read-only; never commits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

import structlog
from sqlalchemy import func, select

from app.models.schemas import FlakyCoachResponse, FlakyScoreBrief, FlakyScoreComponent
from app.services import flaky_score_service

logger = structlog.get_logger("services.flaky_score_brief")

STATUSES = ("scored", "insufficient", "pending")
STATUS_SCORED, STATUS_INSUFFICIENT, STATUS_PENDING = STATUSES

#: The order the details section lists the signals in (the weights' order).
COMPONENT_ORDER: tuple[str, ...] = tuple(flaky_score_service.DEFAULT_WEIGHTS)

#: The sweep's window (``recompute_flaky_scores`` scores the last 30 days and
#: stores ``window_days=30``); the unscored count uses the same one.
SWEEP_WINDOW_DAYS = 30


def _unit(value: Any) -> float:
    """A stored signal or weight as a float in [0, 1]; anything else is 0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def scored_brief(row: Any) -> FlakyScoreBrief:
    """A stored ``flaky_score`` row as the entry's brief (pure)."""
    components: Mapping[str, Any] = row.components if isinstance(row.components, Mapping) else {}
    weights: Mapping[str, Any] = row.weights if isinstance(row.weights, Mapping) else {}
    return FlakyScoreBrief(
        status=STATUS_SCORED,
        score=_unit(row.score),
        confidence=str(row.confidence or flaky_score_service.CONFIDENCE_NONE),
        observations=int(row.observation_count or 0),
        min_observations=flaky_score_service.MIN_OBSERVATIONS,
        window_days=int(row.window_days or SWEEP_WINDOW_DAYS),
        computed_at=_iso_utc(row.computed_at),
        components=[
            FlakyScoreComponent(
                name=name,
                value=_unit(components.get(name)),
                weight=_unit(weights[name] if name in weights else flaky_score_service.DEFAULT_WEIGHTS[name]),
            )
            for name in COMPONENT_ORDER
        ],
    )


def unscored_brief(observations: int) -> FlakyScoreBrief:
    """No stored row: "insufficient" below the evidence floor, else "pending" (pure)."""
    count = max(0, int(observations or 0))
    return FlakyScoreBrief(
        status=STATUS_INSUFFICIENT if count < flaky_score_service.MIN_OBSERVATIONS else STATUS_PENDING,
        score=None,
        confidence=flaky_score_service.CONFIDENCE_NONE,
        observations=count,
        min_observations=flaky_score_service.MIN_OBSERVATIONS,
        window_days=SWEEP_WINDOW_DAYS,
        computed_at=None,
        components=[],
    )


async def _load_briefs(db: Any, project_id: uuid.UUID, fingerprints: list[str]) -> dict[str, FlakyScoreBrief]:
    from app.models.postgres import FlakyScore, TestCase, TestRun

    rows = (
        await db.execute(
            select(FlakyScore).where(
                FlakyScore.project_id == project_id,
                FlakyScore.test_fingerprint.in_(fingerprints),
            )
        )
    ).scalars().all()
    briefs = {row.test_fingerprint: scored_brief(row) for row in rows}

    missing = [fp for fp in fingerprints if fp not in briefs]
    if missing:
        since = datetime.now(timezone.utc) - timedelta(days=SWEEP_WINDOW_DAYS)
        counts = {
            fingerprint: int(count or 0)
            for fingerprint, count in (
                await db.execute(
                    select(TestCase.test_fingerprint, func.count())
                    .join(TestRun, TestRun.id == TestCase.test_run_id)
                    .where(
                        TestRun.project_id == project_id,
                        TestRun.created_at >= since,
                        TestCase.test_fingerprint.in_(missing),
                    )
                    .group_by(TestCase.test_fingerprint)
                )
            ).all()
        }
        for fingerprint in missing:
            briefs[fingerprint] = unscored_brief(counts.get(fingerprint, 0))
    return briefs


async def attach_flaky_scores(db: Any, project_id: uuid.UUID, response: FlakyCoachResponse) -> FlakyCoachResponse:
    """Set each entry's ``flaky_score`` from the project's score rows; return the response.

    No entries → no statement. Any failure → every ``flaky_score`` stays
    ``None`` and one ``flaky_score_attach_failed`` warning is logged; the
    coach answer itself is returned unchanged (never a 500).
    """
    entries = list(response.entries or [])
    if not entries:
        return response
    fingerprints = list(dict.fromkeys(e.test_fingerprint for e in entries if e.test_fingerprint))
    if not fingerprints:
        return response
    try:
        async with db.begin_nested():
            briefs = await _load_briefs(db, project_id, fingerprints)
    except Exception as exc:  # noqa: BLE001 -- a secondary read never fails the page
        logger.warning(
            "flaky_score_attach_failed",
            project_id=str(project_id),
            error_type=type(exc).__name__,
        )
        return response
    for entry in entries:
        entry.flaky_score = briefs.get(entry.test_fingerprint)
    return response
