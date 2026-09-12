"""The only writer of ``agent_pipeline_runs.status`` (architecture E7.1).

Why this module exists
----------------------
Before it, five modules assigned ``pipeline.status = "<literal>"`` directly and
the vocabulary drifted: ``_mark_pipeline_done`` wrote ``partial`` (neither a
success nor a failure), the Investigator wrote ``cancelled`` (which the model
comment never listed), and the /agents router rewrote ``running`` rows to
``failed`` at read time on a 30-minute guess. Requirement 9 of the agentic
architecture is that every run ends in exactly one of four public states and
that no row can sit in an undefined one. That is only provable when one place
decides which transitions are legal.

Two entry points
----------------
* :func:`apply_transition` mutates an already-loaded ORM row. Callers that
  hold the row (usually ``FOR UPDATE``) and set other columns in the same
  transaction use this; the session commit stays theirs. It validates the edge
  against :data:`TRANSITIONS`, maps the Investigator's ``cancelled`` onto
  ``failed`` with a ``cancelled:`` error prefix, and stamps ``completed_at``.
* :func:`guarded_transition` is one ``UPDATE ... WHERE status = :expected
  RETURNING`` for callers that must win a race (the reaper today; retry and
  cancel in E7.4). Zero rows means someone else moved the row first; the
  caller re-reads and never writes.

Internal vs public vocabulary
-----------------------------
Internal: ``pending running retry_wait completed passed failed`` (the DB CHECK
constraint from migration 0173 enforces exactly these). Public, on every API
response: ``in_progress completed failed passed`` via :func:`public_status`.
``partial`` is gone: a run that finished with failed or skipped stages is
``completed`` with ``execution_metadata.stage_quality == "degraded"``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.models.enums import PipelineRunStatus

__all__ = [
    "CANCELLED_ERROR_PREFIX",
    "DEGRADED",
    "IN_PROGRESS_STATUSES",
    "IllegalTransition",
    "PUBLIC_STATUS",
    "TRANSITIONS",
    "TransitionLost",
    "apply_transition",
    "guarded_transition",
    "is_resumable",
    "is_terminal",
    "mark_degraded",
    "normalize_status",
    "passes_without_review",
    "public_status",
    "REVIEW_NOT_APPLICABLE",
]

CANCELLED_ERROR_PREFIX = "cancelled: "
DEGRADED = "degraded"

_S = PipelineRunStatus

# from -> {to}. A same-state "transition" is always a no-op and never listed.
# ``passed`` is terminal. ``completed -> running`` and ``failed -> running`` are
# the same-id resume path (E7.4 adds the attempt ceiling on top of them);
# ``* -> pending`` is the Investigator's requeue-before-claim reset.
# ``failed -> retry_wait`` (E7.2): the pipeline marks itself failed inside the
# graph's own error handler before the Celery task sees the exception, so the
# task parks an already-failed row for its scheduled retry.
TRANSITIONS: Mapping[PipelineRunStatus, frozenset[PipelineRunStatus]] = {
    _S.PENDING: frozenset({_S.RUNNING, _S.FAILED}),
    _S.RUNNING: frozenset({_S.COMPLETED, _S.FAILED, _S.RETRY_WAIT, _S.PENDING}),
    _S.RETRY_WAIT: frozenset({_S.RUNNING, _S.FAILED, _S.PENDING}),
    _S.COMPLETED: frozenset({_S.PASSED, _S.FAILED, _S.RUNNING}),
    _S.FAILED: frozenset({_S.RUNNING, _S.PENDING, _S.RETRY_WAIT}),
    _S.PASSED: frozenset(),
}

PUBLIC_STATUS: Mapping[PipelineRunStatus, str] = {
    _S.PENDING: "in_progress",
    _S.RUNNING: "in_progress",
    _S.RETRY_WAIT: "in_progress",
    _S.COMPLETED: "completed",
    _S.PASSED: "passed",
    _S.FAILED: "failed",
}

# Internal states that project to public ``in_progress``. One definition, so a
# query that means "a run is still going" cannot drift back to ``== "running"``
# and silently miss ``pending`` and ``retry_wait`` (E7.5).
IN_PROGRESS_STATUSES: tuple[str, ...] = tuple(
    s.value for s, pub in PUBLIC_STATUS.items() if pub == "in_progress"
)

# ``review_policy`` for a run that produced no report (architecture section 7.1):
# there is nothing for a human to accept, so ``passed`` needs no ReviewRequest.
REVIEW_NOT_APPLICABLE = "not_applicable"

# Legacy values a row or a caller may still present. ``partial`` rows were
# backfilled by migration 0173; the mapping stays so an un-migrated read (or a
# cached payload) cannot surface an unknown status.
_LEGACY: Mapping[str, PipelineRunStatus] = {
    "partial": _S.COMPLETED,
    "cancelled": _S.FAILED,
    "canceled": _S.FAILED,
}


class IllegalTransition(ValueError):
    """``from -> to`` is not an edge of :data:`TRANSITIONS`."""


class TransitionLost(RuntimeError):
    """The guarded UPDATE matched zero rows: another writer moved the row first."""


def normalize_status(value: Any) -> PipelineRunStatus:
    """Coerce a stored/legacy/public string to the internal enum.

    Unknown values normalise to ``failed``: an unrecognised status is by
    definition an invalid state, and reporting it as in-progress would be the
    stuck-forever failure mode this module exists to remove.
    """
    if isinstance(value, PipelineRunStatus):
        return value
    text = str(value or "").strip().lower()
    if text in _LEGACY:
        return _LEGACY[text]
    try:
        return PipelineRunStatus(text)
    except ValueError:
        return _S.FAILED


def public_status(value: Any) -> str:
    """Internal (or legacy) status -> one of ``in_progress|completed|failed|passed``."""
    return PUBLIC_STATUS[normalize_status(value)]


def is_terminal(value: Any) -> bool:
    return normalize_status(value) in {_S.COMPLETED, _S.PASSED, _S.FAILED}


def is_resumable(status: Any, execution_metadata: Optional[Mapping[str, Any]] = None) -> bool:
    """Same-id resume eligibility: a failed run, or a completed run whose stages
    were degraded (the rows that used to be ``partial``)."""
    raw = str(status or "").strip().lower()
    if raw == "partial":
        # A legacy row (or fixture) that predates migration 0173: the value
        # itself says "finished with failed stages", metadata or not.
        return True
    normalized = normalize_status(status)
    if normalized in (_S.FAILED, _S.RETRY_WAIT):
        return True
    if normalized is _S.COMPLETED:
        metadata = execution_metadata or {}
        return metadata.get("stage_quality") == DEGRADED
    return False


def mark_degraded(execution_metadata: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a copy of ``execution_metadata`` with ``stage_quality = degraded``."""
    merged = dict(execution_metadata or {})
    merged["stage_quality"] = DEGRADED
    return merged


def _now() -> datetime:
    return datetime.now(timezone.utc)


def apply_transition(
    run: Any,
    to: PipelineRunStatus | str,
    *,
    error: Optional[str] = None,
    degraded: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> PipelineRunStatus:
    """Move a loaded ``AgentPipelineRun`` row to ``to``.

    * Validates the edge; raises :class:`IllegalTransition` otherwise.
    * ``to="cancelled"`` (Investigator vocabulary) becomes ``failed`` with the
      error prefixed ``cancelled:`` so the reason survives the mapping.
    * Terminal targets stamp ``completed_at``; ``running``/``pending`` clear it.
    * ``degraded=True`` on ``completed`` stamps ``execution_metadata.stage_quality``.
    * A same-state call is a no-op that still applies ``error``/``degraded``.

    The caller owns the session and the commit.
    """
    requested = str(to.value if isinstance(to, PipelineRunStatus) else to).strip().lower()
    target = normalize_status(requested)
    if requested in ("cancelled", "canceled"):
        error = f"{CANCELLED_ERROR_PREFIX}{error}" if error else f"{CANCELLED_ERROR_PREFIX}by request"
    current = normalize_status(getattr(run, "status", None))

    if target is not current and target not in TRANSITIONS[current]:
        raise IllegalTransition(f"agent_pipeline_runs.status {current.value} -> {target.value}")

    stamp = now or _now()
    run.status = target.value
    if target in (_S.COMPLETED, _S.PASSED, _S.FAILED):
        run.completed_at = getattr(run, "completed_at", None) or stamp
        if target is _S.FAILED and not error and not getattr(run, "error", None):
            error = "failed"
    elif target in (_S.RUNNING, _S.PENDING):
        run.completed_at = None
        if target is _S.RUNNING:
            run.error = None
    if error:
        run.error = error[:2000]
    if degraded and target is _S.COMPLETED:
        run.execution_metadata = mark_degraded(getattr(run, "execution_metadata", None))
    return target


async def guarded_transition(
    db: Any,
    run_id: uuid.UUID | str,
    *,
    expected: PipelineRunStatus | str,
    to: PipelineRunStatus | str,
    error: Optional[str] = None,
    fencing_token: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> PipelineRunStatus:
    """One ``UPDATE ... WHERE id = :id AND status = :expected [AND fencing_token
    = :token] RETURNING status``. Raises :class:`TransitionLost` on zero rows.

    Used where two writers can race (the reaper vs a still-live worker today;
    retry/cancel in E7.4). Terminal targets stamp ``completed_at`` in SQL so
    the row can never be terminal without a timestamp (the CHECK-adjacent
    invariant in the architecture's section 7.2).
    """
    from sqlalchemy import update as sa_update  # noqa: PLC0415

    from app.models.postgres import AgentPipelineRun  # noqa: PLC0415

    expected_s = normalize_status(expected)
    target = normalize_status(to)
    if str(to).strip().lower() in ("cancelled", "canceled"):
        error = f"{CANCELLED_ERROR_PREFIX}{error or 'by request'}"
    if target is not expected_s and target not in TRANSITIONS[expected_s]:
        raise IllegalTransition(f"agent_pipeline_runs.status {expected_s.value} -> {target.value}")

    values: dict[str, Any] = {"status": target.value}
    if target in (_S.COMPLETED, _S.PASSED, _S.FAILED):
        values["completed_at"] = _now()
    elif target in (_S.RUNNING, _S.PENDING):
        values["completed_at"] = None
    if error:
        values["error"] = error[:2000]
    if extra:
        values.update(dict(extra))

    stmt = (
        sa_update(AgentPipelineRun)
        .where(
            AgentPipelineRun.id == (run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))),
            AgentPipelineRun.status == expected_s.value,
        )
        .values(**values)
        .returning(AgentPipelineRun.status)
    )
    if fencing_token is not None:
        stmt = stmt.where(AgentPipelineRun.fencing_token == fencing_token)
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise TransitionLost(
            f"agent_pipeline_runs {run_id}: expected {expected_s.value}, another writer moved it"
        )
    return target


def passes_without_review(
    execution_metadata: Optional[Mapping[str, Any]],
    executed_stage_names: Any,
) -> bool:
    """Whether a just-``completed`` run settles straight to ``passed`` (E7.5).

    Section 7.1: a report-producing run rests at ``completed`` until a human
    accepts its review (E8); a run that produced no report has nothing to
    review and moves ``completed -> passed`` in the finalize transaction, so a
    client can always wait for ``passed | failed``.

    Three things keep a run at ``completed``:

    * it is degraded -- a stage failed, so the result is not settled positively
      whether or not it contains a report;
    * any stage that actually ran is report-producing (by its capability's
      output contract, see ``agent_capability_registry.is_report_producing``);
    * nothing ran at all. An empty run proves nothing, and reporting it as
      ``passed`` is the "absence is not health" mistake.
    """
    if (execution_metadata or {}).get("stage_quality") == DEGRADED:
        return False
    names = [str(n) for n in (executed_stage_names or ()) if n]
    if not names:
        return False
    from app.services.agent_capability_registry import is_report_producing  # noqa: PLC0415

    return not any(is_report_producing(name) for name in names)
