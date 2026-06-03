"""
Flaky-test auto-quarantine workflow — Tier 1 item 3.

State machine owning ``FlakyQuarantineRequest`` rows. The service is the
only module that should mutate ``status`` — routers, agents, and the
celery beat task all call into the helpers here so the state transitions
stay correct and audited.

Public surface:

* :func:`propose_quarantine` — idempotent upsert from detection (agent or
  manual). Creates a new row in ``PROPOSED`` or refreshes the existing
  live row if one exists for this ``(project, test_fingerprint)``.

* :func:`approve` / :func:`reject` / :func:`release` / :func:`schedule_recheck`
  — QA Lead-driven transitions. Each writes a ``SettingsAuditLog`` entry.

* :func:`active_quarantines_for_project` — read helper used by the
  ingestion pipeline to tag newly-ingested test cases.

* :func:`expire_stale_proposals` / :func:`run_recheck_cycle` — maintenance
  tasks invoked by the celery beat schedule.

Every public entry point short-circuits when the ``flaky_auto_quarantine``
feature flag is off so existing deployments see no behaviour change.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
    User,
)

logger = structlog.get_logger("services.flaky_quarantine")


_LIVE_STATES = (
    FlakyQuarantineStatus.DETECTED.value,
    FlakyQuarantineStatus.PROPOSED.value,
    FlakyQuarantineStatus.APPROVED.value,
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)

# How long a PROPOSED row can sit waiting for a QA Lead before it auto-expires.
_PROPOSAL_TTL_DAYS = 7

# Default quarantine window when the QA Lead doesn't override it on approve.
_DEFAULT_QUARANTINE_DAYS = 14

# Flip-rate floor below which a recheck_at row is moved back to RELEASED.
# A test running 10+ times at <10% flip rate is considered stable enough.
_RECHECK_RELEASE_THRESHOLD = 0.10


# ── Feature-flag gate ───────────────────────────────────────────────────────


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("flaky_auto_quarantine", db=db)
    except Exception as exc:
        logger.debug("flaky_auto_quarantine flag check failed", error=str(exc))
        return False


# ── Audit ───────────────────────────────────────────────────────────────────


async def _audit(
    db: AsyncSession,
    actor: Optional[User],
    *,
    action: str,
    request_id: uuid.UUID,
    project_id: uuid.UUID,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
) -> None:
    """Write a SettingsAuditLog row — never raises.

    Uses a fresh ``AsyncSessionLocal`` (NOT the caller's ``db``) so:

    * Transient DB faults can be retried without poisoning the caller's
      session (a rolled-back asyncpg session needs an explicit
      ``rollback()`` before reuse, which the caller doesn't know to do
      from inside this swallowed-exception path).
    * The audit row is durable across the primary mutation's transaction
      boundary. Every caller in this module already commits the primary
      mutation BEFORE calling ``_audit`` (see e.g. line 304-313), so
      this matches the de-facto behaviour while making the isolation
      explicit. P2-6 (attempt-vs-outcome split) covers the broader
      design question across all services.
    * Final failures emit a structured WARNING with the action,
      request_id, project_id, and exception type — operators querying
      the log for missing audit rows can pin them to the original event.

    ``db`` is still accepted in the signature for backwards-compat with
    callers (and to keep the unused parameter from being a footgun if
    callers expect the audit to share a session in the future).
    """
    # `db` parameter retained for API compatibility — see docstring above.
    del db
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import SettingsAuditLog
    from app.services.resilience import DB_RETRYABLE_EXCEPTIONS, async_retry

    if action in ("update", "approve", "reject") and before and after:
        changed = sorted(
            k for k in set(before) | set(after)
            if before.get(k) != after.get(k)
        )
    elif after is not None:
        changed = sorted(after.keys())
    elif before is not None:
        changed = sorted(before.keys())
    else:
        changed = []

    entry_kwargs = dict(
        setting_key=f"flaky_quarantine:{project_id}:{request_id}",
        action=action,
        actor_id=actor.id if actor else None,
        actor_name=(
            getattr(actor, "username", None) or getattr(actor, "email", None)
        ) if actor else None,
        changed_fields=changed,
    )

    async def _do_write() -> None:
        # Fresh session per attempt — a previous failure rolled back this
        # session implicitly when the context manager exited, so each
        # retry starts clean.
        async with AsyncSessionLocal() as audit_db:
            try:
                audit_db.add(SettingsAuditLog(**entry_kwargs))
                await audit_db.commit()
            except Exception:
                await audit_db.rollback()
                raise

    try:
        await async_retry(
            _do_write,
            max_retries=2,
            base_delay=0.1,
            max_delay=2.0,
            retryable_exceptions=DB_RETRYABLE_EXCEPTIONS,
            operation_name="flaky_quarantine_audit",
        )
        logger.info(
            "flaky_quarantine_change",
            request_id=str(request_id),
            project_id=str(project_id),
            action=action,
            actor_id=str(actor.id) if actor else None,
        )
    except Exception as exc:
        # Structured WARNING so operators can grep "audit_dropped action=..."
        # against the request id / project id when reconciling missing rows.
        logger.warning(
            "flaky_quarantine_audit_dropped",
            action=action,
            request_id=str(request_id),
            project_id=str(project_id),
            actor_id=str(actor.id) if actor else None,
            error_type=type(exc).__name__,
            error=str(exc),
        )


def _snapshot(row: FlakyQuarantineRequest) -> dict[str, Any]:
    return {
        "status": row.status,
        "flip_rate": row.flip_rate,
        "flip_window_size": row.flip_window_size,
        "quarantine_start": row.quarantine_start.isoformat() if row.quarantine_start else None,
        "quarantine_expires_at": row.quarantine_expires_at.isoformat() if row.quarantine_expires_at else None,
        "approved_by_user_id": str(row.approved_by_user_id) if row.approved_by_user_id else None,
        "rejected_by_user_id": str(row.rejected_by_user_id) if row.rejected_by_user_id else None,
    }


# ── Lookup helpers ──────────────────────────────────────────────────────────


async def _find_live(
    db: AsyncSession, project_id: uuid.UUID, test_fingerprint: str,
) -> Optional[FlakyQuarantineRequest]:
    """Return the single live row for this (project, fingerprint) if any.

    Backed by the ``ux_fqr_live_per_fingerprint`` partial unique index so a
    full table scan isn't required.
    """
    result = await db.execute(
        select(FlakyQuarantineRequest).where(
            FlakyQuarantineRequest.project_id == project_id,
            FlakyQuarantineRequest.test_fingerprint == test_fingerprint,
            FlakyQuarantineRequest.status.in_(_LIVE_STATES),
        )
    )
    return result.scalar_one_or_none()


async def get_request(
    db: AsyncSession, request_id: uuid.UUID,
) -> Optional[FlakyQuarantineRequest]:
    result = await db.execute(
        select(FlakyQuarantineRequest).where(FlakyQuarantineRequest.id == request_id)
    )
    return result.scalar_one_or_none()


async def list_requests(
    db: AsyncSession,
    *,
    project_ids: Optional[set[uuid.UUID]] = None,
    status_filter: Optional[str] = None,
    live_only: bool = False,
    limit: int = 200,
) -> list[FlakyQuarantineRequest]:
    stmt = select(FlakyQuarantineRequest)
    if project_ids is not None:
        if not project_ids:
            return []
        stmt = stmt.where(FlakyQuarantineRequest.project_id.in_(project_ids))
    if status_filter:
        stmt = stmt.where(FlakyQuarantineRequest.status == status_filter)
    if live_only:
        stmt = stmt.where(FlakyQuarantineRequest.status.in_(_LIVE_STATES))
    stmt = stmt.order_by(FlakyQuarantineRequest.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def stats_by_status(
    db: AsyncSession, project_ids: Optional[set[uuid.UUID]] = None,
) -> dict[str, int]:
    from sqlalchemy import func as _func
    stmt = select(
        FlakyQuarantineRequest.status,
        _func.count(FlakyQuarantineRequest.id).label("count"),
    )
    if project_ids is not None:
        if not project_ids:
            return {}
        stmt = stmt.where(FlakyQuarantineRequest.project_id.in_(project_ids))
    stmt = stmt.group_by(FlakyQuarantineRequest.status)
    result = await db.execute(stmt)
    return {row.status: int(row.count) for row in result.all()}


async def active_quarantines_for_project(
    db: AsyncSession, project_id: uuid.UUID,
) -> set[str]:
    """Return the set of test fingerprints currently under active quarantine.

    Used by the ingestion pipeline: on every ingest, any test whose
    fingerprint is in this set gets tagged ``quarantined`` so dashboards,
    release gate scoring, and defect promotion can all exclude it.

    The query is bounded and index-backed via ``ix_fqr_project_status`` +
    the partial live-state unique index.
    """
    if not await _feature_enabled(db):
        return set()
    result = await db.execute(
        select(FlakyQuarantineRequest.test_fingerprint).where(
            FlakyQuarantineRequest.project_id == project_id,
            FlakyQuarantineRequest.status.in_(
                (
                    FlakyQuarantineStatus.QUARANTINED.value,
                    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
                    FlakyQuarantineStatus.RE_QUARANTINED.value,
                )
            ),
        )
    )
    return {row[0] for row in result.all()}


# ── Transitions ─────────────────────────────────────────────────────────────


async def propose_quarantine(
    *,
    project_id: uuid.UUID,
    test_fingerprint: str,
    test_name: Optional[str],
    suite_name: Optional[str],
    detection_method: str,
    flip_rate: Optional[float],
    flip_window_size: Optional[int],
    pass_count: Optional[int] = None,
    fail_count: Optional[int] = None,
    rationale: Optional[dict[str, Any]] = None,
    quarantine_duration_days: int = _DEFAULT_QUARANTINE_DAYS,
    actor: Optional[User] = None,
) -> Optional[FlakyQuarantineRequest]:
    """Create or refresh a PROPOSED row for a flaky test.

    Idempotent: if a live row already exists for this (project, fingerprint)
    the detection signal is merged in (updates flip_rate, pushes the row
    to PROPOSED if it was only DETECTED) rather than creating a duplicate.

    No-op when the feature flag is off; returns ``None`` so callers can
    skip downstream work.
    """
    from app.core.metrics import quarantine_proposals_total

    if not await _feature_enabled():
        return None

    # "auto" source = detection method didn't originate from an explicit
    # QA-Lead Propose click; everything else is manual. The propose_quarantine
    # entry point is called from both detection workers (auto) and from the
    # manual-propose router handler (actor=user, detection_method="manual").
    source_label = "manual" if detection_method == "manual" else "auto"

    async with AsyncSessionLocal() as db:
        try:
            existing = await _find_live(db, project_id, test_fingerprint)
            now = datetime.now(timezone.utc)

            if existing is not None:
                before = _snapshot(existing)
                # Merge new signal. Keep the later detection timestamps and
                # let the new flip_rate win — detection runs on latest data.
                existing.flip_rate = flip_rate
                existing.flip_window_size = flip_window_size
                if pass_count is not None:
                    existing.pass_count = pass_count
                if fail_count is not None:
                    existing.fail_count = fail_count
                existing.last_failure_at = now
                if test_name:
                    existing.test_name = test_name
                if suite_name:
                    existing.suite_name = suite_name
                if rationale:
                    existing.rationale = rationale
                # Escalate DETECTED → PROPOSED on the first signal strong
                # enough to involve humans.
                if existing.status == FlakyQuarantineStatus.DETECTED.value:
                    existing.status = FlakyQuarantineStatus.PROPOSED.value
                    existing.proposed_at = now
                existing.updated_at = now
                await db.commit()
                await db.refresh(existing)
                await _audit(
                    db, actor,
                    action="refresh_proposal",
                    request_id=existing.id,
                    project_id=project_id,
                    before=before,
                    after=_snapshot(existing),
                )
                return existing

            row = FlakyQuarantineRequest(
                project_id=project_id,
                test_fingerprint=test_fingerprint,
                test_name=test_name,
                suite_name=suite_name,
                status=FlakyQuarantineStatus.PROPOSED.value,
                detection_method=detection_method,
                flip_rate=flip_rate,
                flip_window_size=flip_window_size,
                pass_count=pass_count,
                fail_count=fail_count,
                detected_at=now,
                last_failure_at=now,
                proposed_at=now,
                quarantine_duration_days=quarantine_duration_days,
                rationale=rationale,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            await _audit(
                db, actor,
                action="create",
                request_id=row.id,
                project_id=project_id,
                after=_snapshot(row),
            )
            quarantine_proposals_total.labels(source=source_label).inc()
            return row
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "propose_quarantine failed",
                project_id=str(project_id),
                test_fingerprint=test_fingerprint,
                error=str(exc),
            )
            return None


async def approve(
    db: AsyncSession,
    request_id: uuid.UUID,
    actor: User,
    *,
    notes: Optional[str] = None,
    quarantine_duration_days: Optional[int] = None,
) -> FlakyQuarantineRequest:
    from fastapi import HTTPException, status as _s
    row = await get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=_s.HTTP_404_NOT_FOUND, detail="Quarantine request not found")
    if row.status not in (
        FlakyQuarantineStatus.PROPOSED.value,
        FlakyQuarantineStatus.DETECTED.value,
    ):
        raise HTTPException(
            status_code=_s.HTTP_409_CONFLICT,
            detail=f"Cannot approve from status {row.status}",
        )

    before = _snapshot(row)
    now = datetime.now(timezone.utc)
    duration = quarantine_duration_days or row.quarantine_duration_days or _DEFAULT_QUARANTINE_DAYS

    # Two-step transition: APPROVED (intent) → QUARANTINED (active) happens
    # on the next ingestion. We set QUARANTINED directly here so the
    # ingestion pipeline's ``active_quarantines_for_project`` lookup picks
    # it up immediately; the APPROVED state is retained only for manual
    # workflows that want to stage an approval without activating it.
    row.status = FlakyQuarantineStatus.QUARANTINED.value
    row.approved_at = now
    row.approved_by_user_id = actor.id
    row.quarantine_start = now
    row.quarantine_expires_at = now + timedelta(days=duration)
    row.quarantine_duration_days = duration
    # Schedule a recheck one day before expiry so the beat task has a
    # window to evaluate and release before the window closes.
    row.recheck_at = row.quarantine_expires_at - timedelta(days=1)
    if notes:
        row.reviewer_notes = notes
    row.updated_at = now
    await db.commit()
    await db.refresh(row)
    await _audit(
        db, actor,
        action="approve",
        request_id=row.id,
        project_id=row.project_id,
        before=before,
        after=_snapshot(row),
    )
    from app.core.metrics import quarantine_approvals_total
    quarantine_approvals_total.labels(outcome="approved").inc()

    # Tier 2 item 6 — emit webhook event so external systems (PagerDuty,
    # Slack, Jira) can react to the quarantine. Non-blocking.
    try:
        from app.services.webhook_service import emit_event
        await emit_event(
            "flaky.quarantined",
            project_id=row.project_id,
            payload={
                "request_id": str(row.id),
                "project_id": str(row.project_id),
                "test_fingerprint": row.test_fingerprint,
                "test_name": row.test_name,
                "suite_name": row.suite_name,
                "flip_rate": row.flip_rate,
                "flip_window_size": row.flip_window_size,
                "quarantine_start": row.quarantine_start.isoformat() if row.quarantine_start else None,
                "quarantine_expires_at": row.quarantine_expires_at.isoformat() if row.quarantine_expires_at else None,
                "approved_by_user_id": str(actor.id),
                "rationale": row.rationale,
            },
        )
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("flaky.quarantined webhook emit failed", error=str(exc))

    return row


async def reject(
    db: AsyncSession,
    request_id: uuid.UUID,
    actor: User,
    *,
    notes: Optional[str] = None,
) -> FlakyQuarantineRequest:
    from fastapi import HTTPException, status as _s
    row = await get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=_s.HTTP_404_NOT_FOUND, detail="Quarantine request not found")
    if row.status not in (
        FlakyQuarantineStatus.PROPOSED.value,
        FlakyQuarantineStatus.DETECTED.value,
    ):
        raise HTTPException(
            status_code=_s.HTTP_409_CONFLICT,
            detail=f"Cannot reject from status {row.status}",
        )
    before = _snapshot(row)
    now = datetime.now(timezone.utc)
    row.status = FlakyQuarantineStatus.REJECTED.value
    row.rejected_at = now
    row.rejected_by_user_id = actor.id
    if notes:
        row.reviewer_notes = notes
    row.updated_at = now
    await db.commit()
    await db.refresh(row)
    await _audit(
        db, actor,
        action="reject",
        request_id=row.id,
        project_id=row.project_id,
        before=before,
        after=_snapshot(row),
    )
    from app.core.metrics import quarantine_approvals_total
    quarantine_approvals_total.labels(outcome="rejected").inc()
    return row


async def release(
    db: AsyncSession,
    request_id: uuid.UUID,
    actor: User,
    *,
    notes: Optional[str] = None,
) -> FlakyQuarantineRequest:
    """Manually end an active quarantine. Used when a QA lead decides the
    underlying bug is fixed before the auto-recheck window."""
    from fastapi import HTTPException, status as _s
    row = await get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=_s.HTTP_404_NOT_FOUND, detail="Quarantine request not found")
    if row.status not in (
        FlakyQuarantineStatus.QUARANTINED.value,
        FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
        FlakyQuarantineStatus.RE_QUARANTINED.value,
    ):
        raise HTTPException(
            status_code=_s.HTTP_409_CONFLICT,
            detail=f"Cannot release from status {row.status}",
        )
    before = _snapshot(row)
    now = datetime.now(timezone.utc)
    row.status = FlakyQuarantineStatus.RELEASED.value
    if notes:
        row.reviewer_notes = notes
    row.updated_at = now
    await db.commit()
    await db.refresh(row)
    await _audit(
        db, actor,
        action="release",
        request_id=row.id,
        project_id=row.project_id,
        before=before,
        after=_snapshot(row),
    )
    return row


# ── Maintenance (celery beat) ──────────────────────────────────────────────


async def expire_stale_proposals() -> int:
    """Flip PROPOSED rows older than ``_PROPOSAL_TTL_DAYS`` to EXPIRED.

    Runs from the nightly beat schedule. Returns the count of rows
    expired for telemetry.
    """
    if not await _feature_enabled():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=_PROPOSAL_TTL_DAYS)
    expired = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.status == FlakyQuarantineStatus.PROPOSED.value,
                FlakyQuarantineRequest.proposed_at <= cutoff,
            )
        )
        rows = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        pending_audits: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            before = _snapshot(row)
            row.status = FlakyQuarantineStatus.EXPIRED.value
            row.updated_at = now
            pending_audits.append((row.id, row.project_id, before, _snapshot(row)))
            expired += 1
        if rows:
            await db.commit()
            # Audit AFTER the commit (every other caller in this module does
            # too): ``_audit`` writes + commits on its OWN session, so auditing
            # mid-loop would make the "expire" rows durable even if this batch
            # commit failed — the log would then claim expirations that rolled
            # back.
            for req_id, proj_id, before, after in pending_audits:
                await _audit(
                    db, None,
                    action="expire",
                    request_id=req_id,
                    project_id=proj_id,
                    before=before,
                    after=after,
                )
    return expired


async def schedule_pending_rechecks() -> int:
    """Move QUARANTINED rows whose ``recheck_at`` has passed into
    ``RECHECK_SCHEDULED`` so the next cycle evaluates their recovery."""
    if not await _feature_enabled():
        return 0
    now = datetime.now(timezone.utc)
    moved = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.status == FlakyQuarantineStatus.QUARANTINED.value,
                FlakyQuarantineRequest.recheck_at != None,  # noqa: E711
                FlakyQuarantineRequest.recheck_at <= now,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = FlakyQuarantineStatus.RECHECK_SCHEDULED.value
            row.updated_at = now
            moved += 1
        if rows:
            await db.commit()
    return moved


async def run_recheck_cycle() -> dict[str, int]:
    """Evaluate every RECHECK_SCHEDULED row and either RELEASE or RE_QUARANTINE.

    Uses ``TestCase`` history since quarantine start to compute the new
    flip rate. A test that has run at least 10 times since quarantine and
    has a flip rate below ``_RECHECK_RELEASE_THRESHOLD`` is released;
    everything else is bumped back into a fresh quarantine window.

    Returns a dict with per-action counts for telemetry.
    """
    if not await _feature_enabled():
        return {"released": 0, "re_quarantined": 0, "insufficient_data": 0}
    released = re_quarantined = insufficient = 0

    async with AsyncSessionLocal() as db:
        from app.models.postgres import TestCase, TestRun, TestStatus
        from sqlalchemy import and_ as _and, func as _func, or_ as _or
        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.status == FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
            )
        )
        rows = list(result.scalars().all())
        now = datetime.now(timezone.utc)

        # Batch the per-row flip-rate counts into ONE grouped query (was one
        # COUNT query per RECHECK_SCHEDULED row). Each row has its own ``since``
        # cutoff, so OR per-(project, fingerprint, since) conditions and group by
        # (project, fingerprint, status). The partial unique index
        # ``ux_fqr_live_per_fingerprint`` guarantees at most one LIVE row per
        # (project_id, test_fingerprint) — RECHECK_SCHEDULED is a live state — so
        # each result pair maps back to exactly one row and that row's cutoff is
        # preserved. MUST keep the TestRun.project_id scope: ``test_fingerprint``
        # is ``sha256(class::name)`` with no project salt, so two projects with a
        # same-named test share a fingerprint and an unscoped count would blend
        # tenants. The ``test_fingerprint IN (...)`` predicate is redundant with
        # the OR (it only helps the planner use ix_test_cases_fingerprint).
        buckets_by_pair: dict[tuple[Any, str], dict[str, int]] = {}
        if rows:
            since_by_pair: dict[tuple[Any, str], datetime] = {}
            for row in rows:
                since = row.quarantine_start or (
                    now - timedelta(days=row.quarantine_duration_days)
                )
                since_by_pair[(row.project_id, row.test_fingerprint)] = since
            conds = [
                _and(
                    TestRun.project_id == pid,
                    TestCase.test_fingerprint == fp,
                    TestCase.created_at >= since,
                )
                for (pid, fp), since in since_by_pair.items()
            ]
            counts_stmt = (
                select(
                    TestRun.project_id.label("project_id"),
                    TestCase.test_fingerprint.label("fingerprint"),
                    TestCase.status.label("status"),
                    _func.count(TestCase.id).label("c"),
                )
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(
                    TestCase.test_fingerprint.in_([fp for _p, fp in since_by_pair]),
                    _or(*conds),
                )
                .group_by(
                    TestRun.project_id, TestCase.test_fingerprint, TestCase.status,
                )
            )
            counts_result = await db.execute(counts_stmt)
            for cr in counts_result.all():
                buckets_by_pair.setdefault(
                    (cr.project_id, cr.fingerprint), {}
                )[cr.status] = int(cr.c)

        for row in rows:
            buckets = buckets_by_pair.get((row.project_id, row.test_fingerprint), {})

            passes = buckets.get(TestStatus.PASSED.value, 0)
            fails = (
                buckets.get(TestStatus.FAILED.value, 0)
                + buckets.get(TestStatus.BROKEN.value, 0)
            )
            total = passes + fails

            if total < 10:
                # Not enough runs to make a call yet — extend the quarantine
                # by one week so the next cycle gets more data.
                row.quarantine_expires_at = now + timedelta(days=7)
                row.recheck_at = row.quarantine_expires_at - timedelta(days=1)
                row.status = FlakyQuarantineStatus.QUARANTINED.value
                row.updated_at = now
                insufficient += 1
                continue

            flip_rate = fails / total if total else 0.0
            row.flip_rate = flip_rate
            row.flip_window_size = total
            row.pass_count = passes
            row.fail_count = fails
            row.updated_at = now

            if flip_rate < _RECHECK_RELEASE_THRESHOLD:
                row.status = FlakyQuarantineStatus.RELEASED.value
                released += 1
            else:
                row.status = FlakyQuarantineStatus.RE_QUARANTINED.value
                row.quarantine_start = now
                row.quarantine_expires_at = now + timedelta(days=row.quarantine_duration_days)
                row.recheck_at = row.quarantine_expires_at - timedelta(days=1)
                re_quarantined += 1

        if rows:
            await db.commit()
    from app.core.metrics import quarantine_expired_total
    if released:
        quarantine_expired_total.labels(terminal_state="released").inc(released)
    if re_quarantined:
        quarantine_expired_total.labels(terminal_state="re_quarantined").inc(re_quarantined)
    return {
        "released": released,
        "re_quarantined": re_quarantined,
        "insufficient_data": insufficient,
    }
