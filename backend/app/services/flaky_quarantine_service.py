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

* :func:`expire_stale_proposals` / :func:`run_recheck_cycle` /
  :func:`mark_stale_quarantines` — maintenance tasks invoked by the celery
  beat schedule.

* Quarantine lifecycle (PMF US-5.4 / US-5.5): :func:`get_lifecycle_policy`
  resolves the per-project policy (missing row → defaults),
  :func:`update_quarantine_stability` advances the consecutive-pass counter
  at run finalization and auto-promotes (or flags ``ready_to_promote``) at
  the policy threshold, and the approve hook resolves an owner, snapshots
  the SLA, and optionally auto-creates an internal defect record.

Every public entry point short-circuits when the ``flaky_auto_quarantine``
feature flag is off so existing deployments see no behaviour change.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
    Project,
    QuarantineLifecyclePolicy,
    TestStatus,
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

# States in which a quarantine is CURRENTLY EFFECTIVE — the test is actively
# excluded from release-gate scoring and (US-5.1) suppressed by the CI
# manifest. Deliberately narrower than ``_LIVE_STATES``: DETECTED/PROPOSED
# are un-reviewed signals and APPROVED is a staged intent that hasn't
# activated — none of those may green a CI build.
_ACTIVE_QUARANTINE_STATES = (
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

# ── Lifecycle policy defaults (PMF US-5.4 / US-5.5 / US-5.6) ────────────────
# A project without a quarantine_lifecycle_policies row resolves to these.
_DEFAULT_SLA_DAYS = 14
_DEFAULT_PROMOTE_AFTER_PASSES = 20
# Fowler's rule of thumb, per-project configurable (migration 0134).
_DEFAULT_MAX_ACTIVE_QUARANTINED = 8
_DEFAULT_DETECTION_FLIP_RATE = 0.20
_DEFAULT_DETECTION_MIN_RUNS = 10


@dataclass(frozen=True)
class EffectiveLifecyclePolicy:
    """Resolved per-project quarantine lifecycle policy."""
    sla_days: int = _DEFAULT_SLA_DAYS
    auto_create_defect: bool = False
    auto_promote: bool = False
    promote_after_passes: int = _DEFAULT_PROMOTE_AFTER_PASSES
    detection_flip_rate_threshold: float = _DEFAULT_DETECTION_FLIP_RATE
    detection_min_runs: int = _DEFAULT_DETECTION_MIN_RUNS
    # Phase 5 (P5-B). How many tests may sit in quarantine before the UI warns.
    # 0 = unlimited. Warns, never blocks: refusing to quarantine a genuinely
    # broken test would push the noise back into the build.
    max_active_quarantined: int = _DEFAULT_MAX_ACTIVE_QUARANTINED


async def get_lifecycle_policy_row(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[QuarantineLifecyclePolicy]:
    """The explicit lifecycle policy row for a project, or ``None`` (=
    code defaults apply). Read-only."""
    result = await db.execute(
        select(QuarantineLifecyclePolicy).where(
            QuarantineLifecyclePolicy.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


async def get_lifecycle_policy(
    db: AsyncSession, project_id: uuid.UUID,
) -> EffectiveLifecyclePolicy:
    """Load the project's lifecycle policy, resolving a missing row to the
    defaults. Read-only."""
    row = await get_lifecycle_policy_row(db, project_id)
    if row is None:
        return EffectiveLifecyclePolicy()
    return EffectiveLifecyclePolicy(
        sla_days=int(row.sla_days or _DEFAULT_SLA_DAYS),
        auto_create_defect=bool(row.auto_create_defect),
        auto_promote=bool(row.auto_promote),
        promote_after_passes=int(
            row.promote_after_passes or _DEFAULT_PROMOTE_AFTER_PASSES
        ),
        max_active_quarantined=int(
            row.max_active_quarantined
            if getattr(row, "max_active_quarantined", None) is not None
            else _DEFAULT_MAX_ACTIVE_QUARANTINED
        ),
        detection_flip_rate_threshold=float(
            row.detection_flip_rate_threshold
            if row.detection_flip_rate_threshold is not None
            else _DEFAULT_DETECTION_FLIP_RATE
        ),
        detection_min_runs=int(row.detection_min_runs or _DEFAULT_DETECTION_MIN_RUNS),
    )


async def upsert_lifecycle_policy(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    sla_days: int,
    auto_create_defect: bool,
    auto_promote: bool,
    promote_after_passes: int,
    detection_flip_rate_threshold: float,
    detection_min_runs: int,
) -> QuarantineLifecyclePolicy:
    """Create or update the project's lifecycle policy row. Stage-only —
    the router handler owns ``db.commit()`` (transaction-boundary
    discipline)."""
    row = await get_lifecycle_policy_row(db, project_id)
    if row is None:
        row = QuarantineLifecyclePolicy(project_id=project_id)
        db.add(row)
    row.sla_days = sla_days
    row.auto_create_defect = auto_create_defect
    row.auto_promote = auto_promote
    row.promote_after_passes = promote_after_passes
    row.detection_flip_rate_threshold = detection_flip_rate_threshold
    row.detection_min_runs = detection_min_runs
    await db.flush()
    return row


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
    # ``getattr`` for the lifecycle fields (migration 0104) so test doubles
    # built before the columns existed keep working.
    owner_user_id = getattr(row, "owner_user_id", None)
    stale_at = getattr(row, "stale_at", None)
    return {
        "status": row.status,
        "flip_rate": row.flip_rate,
        "flip_window_size": row.flip_window_size,
        "quarantine_start": row.quarantine_start.isoformat() if row.quarantine_start else None,
        "quarantine_expires_at": row.quarantine_expires_at.isoformat() if row.quarantine_expires_at else None,
        "approved_by_user_id": str(row.approved_by_user_id) if row.approved_by_user_id else None,
        "rejected_by_user_id": str(row.rejected_by_user_id) if row.rejected_by_user_id else None,
        # Lifecycle (PMF US-5.4 / US-5.5)
        "owner_user_id": str(owner_user_id) if owner_user_id else None,
        "stale_at": stale_at.isoformat() if stale_at else None,
        "consecutive_passes": getattr(row, "consecutive_passes", 0) or 0,
        "ready_to_promote": bool(getattr(row, "ready_to_promote", False)),
    }


# ── Lifecycle: owner resolution + activation enrichment (US-5.4) ────────────


async def _resolve_owner_user_id(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: Optional[str],
    test_name: Optional[str],
) -> Optional[uuid.UUID]:
    """Map the test's suite through the project's ownership rules to a User.

    The ownership rules (``service_ownership_rules`` + the legacy
    ``component_owner_map``) resolve to a ``(team_name, team_contact)``
    pair; ``team_contact`` is free-form (email / username / slack handle)
    — we match it against ``User.email`` / ``User.username``. Returns
    ``None`` when no rule matches or the contact doesn't map to an active
    user; the caller falls back to the approving QA lead.
    """
    from sqlalchemy import or_ as _or

    from app.models.postgres import Project
    from app.services.ownership_resolver_service import (
        load_rules_for_project,
        resolve_test_ownership,
    )

    rules = await load_rules_for_project(db, project_id)
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    resolution = resolve_test_ownership(
        rules,
        {
            "suite_name": suite_name or "",
            "component": suite_name or "",
            "path": test_name or "",
        },
        project.component_owner_map if project else None,
    )
    contact = (resolution.team_contact or "").strip()
    if not contact:
        return None
    user = (
        (
            await db.execute(
                select(User)
                .where(
                    _or(User.email == contact, User.username == contact),
                    User.is_active == True,  # noqa: E712
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return user.id if user else None


async def _lifecycle_activation_context(
    project_id: uuid.UUID,
    suite_name: Optional[str],
    test_name: Optional[str],
    fallback_user_id: Optional[uuid.UUID],
) -> tuple[EffectiveLifecyclePolicy, Optional[uuid.UUID]]:
    """Policy + owner for a quarantine activation — never raises.

    Runs on a FRESH ``AsyncSessionLocal`` (like ``_audit``) so a lookup
    fault cannot poison the caller's transaction: activation must never
    fail on ownership-resolution errors. Owner falls back to the approving
    QA lead (``fallback_user_id``).
    """
    policy = EffectiveLifecyclePolicy()
    owner_id = fallback_user_id
    try:
        async with AsyncSessionLocal() as ldb:
            policy = await get_lifecycle_policy(ldb, project_id)
            resolved = await _resolve_owner_user_id(
                ldb, project_id, suite_name, test_name,
            )
            if resolved is not None:
                owner_id = resolved
    except Exception as exc:
        logger.warning(
            "quarantine_lifecycle_context_failed",
            project_id=str(project_id),
            error=str(exc),
        )
    return policy, owner_id


def _stage_internal_defect(
    db: AsyncSession, row: FlakyQuarantineRequest,
) -> Optional[uuid.UUID]:
    """Stage an INTERNAL defect record for an activating quarantine (US-5.4).

    Local record only — Jira posting is Epic 6. The id is generated
    client-side so no flush is needed; the INSERT rides the caller's
    commit (the unit of work executes inserts before the quarantine row's
    UPDATE, satisfying the ``defect_id`` FK). Never raises.
    """
    try:
        from app.core.config import settings
        from app.models.postgres import Defect

        name = row.test_name or row.test_fingerprint
        flip = f"{row.flip_rate:.0%}" if row.flip_rate is not None else "unknown"
        description = (
            "TestLookup quarantined this test as flaky.\n\n"
            f"Test: {name}\n"
            f"Suite: {row.suite_name or 'unknown'}\n"
            f"Fingerprint: {row.test_fingerprint}\n"
            f"Flake history: flip rate {flip} over "
            f"{row.flip_window_size if row.flip_window_size is not None else '?'} runs "
            f"(pass={row.pass_count if row.pass_count is not None else '?'}, "
            f"fail={row.fail_count if row.fail_count is not None else '?'})\n"
            f"Detection: {row.detection_method}\n\n"
            f"Quarantine review: {settings.public_base_url}/quarantine"
        )
        defect = Defect(
            id=uuid.uuid4(),
            project_id=row.project_id,
            title=f"Quarantined flaky test: {name}"[:255],
            description=description,
            severity="MEDIUM",
            component=(row.suite_name or None) and row.suite_name[:255],
            labels=["flaky", "quarantine", "auto-created"],
            resolution_status="OPEN",
            promotion_source="quarantine_lifecycle",
        )
        db.add(defect)
        return defect.id
    except Exception as exc:
        logger.warning(
            "quarantine_defect_autocreate_failed",
            request_id=str(row.id),
            project_id=str(row.project_id),
            error=str(exc),
        )
        return None


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
    # Same unconditional life-cycle exclusion as the suites list. Measured
    # live, **every** quarantine request returned (3 of 3) belonged to
    # soft-deleted projects, so the whole Quarantine queue was proposals for
    # tests in projects nobody can open.
    stmt = stmt.where(
        FlakyQuarantineRequest.project_id.in_(
            select(Project.id).where(Project.is_active.is_(True))
        )
    )
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


async def active_quarantine_entries(
    db: AsyncSession, project_id: uuid.UUID,
) -> list[tuple[FlakyQuarantineRequest, Optional[str]]]:
    """Return ``(request_row, class_name)`` pairs for every currently-effective
    quarantine in a project — the data behind the CI quarantine manifest
    (US-5.1).

    ``class_name`` is not stored on ``FlakyQuarantineRequest``; it comes from
    an outer join to ``CanonicalTestCase`` on ``(project_id,
    test_fingerprint)`` (index-backed via ``uq_canonical_test_cases_project_fp``)
    so CI-side matchers that key on ``class::name`` tuples can resolve it.
    ``None`` when the canonical row doesn't exist (e.g. manual proposals for
    tests that never ran).

    Mirrors :func:`active_quarantines_for_project` semantics: when the
    ``flaky_auto_quarantine`` feature flag is off, quarantine is not enforced
    anywhere (ingestion tagging, release gate), so the manifest is empty —
    CI then treats every failure as real, which fails toward strictness.
    """
    if not await _feature_enabled(db):
        return []
    from sqlalchemy import and_ as _and

    from app.models.postgres import CanonicalTestCase

    stmt = (
        select(FlakyQuarantineRequest, CanonicalTestCase.class_name)
        .outerjoin(
            CanonicalTestCase,
            _and(
                CanonicalTestCase.project_id == FlakyQuarantineRequest.project_id,
                CanonicalTestCase.test_fingerprint == FlakyQuarantineRequest.test_fingerprint,
            ),
        )
        .where(
            FlakyQuarantineRequest.project_id == project_id,
            FlakyQuarantineRequest.status.in_(_ACTIVE_QUARANTINE_STATES),
        )
        .order_by(FlakyQuarantineRequest.test_fingerprint)
    )
    result = await db.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


def manifest_etag(rows: list[FlakyQuarantineRequest]) -> str:
    """Stable content hash of a manifest's entry set for HTTP ``ETag``.

    sha256 over the sorted ``fingerprint|status|updated_at`` triples: identical
    quarantine state always yields the same tag (row order doesn't matter),
    and any transition — release, re-quarantine, refreshed window — changes
    it, so CI can poll with ``If-None-Match`` and get cheap 304s.
    """
    import hashlib

    parts = sorted(
        "{}|{}|{}".format(
            row.test_fingerprint,
            row.status,
            row.updated_at.isoformat() if row.updated_at else "",
        )
        for row in rows
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


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

    # ── Lifecycle enrichment (PMF US-5.4) — owner + SLA + optional internal
    # defect. Policy/owner lookups run on their own session and never raise:
    # activation must not fail on ownership-resolution errors.
    policy, owner_id = await _lifecycle_activation_context(
        row.project_id, row.suite_name, row.test_name, actor.id,
    )
    row.owner_user_id = owner_id
    row.sla_days = policy.sla_days
    row.stale_at = now + timedelta(days=policy.sla_days)
    row.stale_notified_at = None
    # Reset US-5.5 stability tracking for the fresh quarantine window.
    row.consecutive_passes = 0
    row.last_stability_run_id = None
    row.ready_to_promote = False
    row.ready_notified_at = None
    if policy.auto_create_defect and row.defect_id is None:
        row.defect_id = _stage_internal_defect(db, row)

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

    # PMF US-7.1 — test.quarantined transition notification. Event-driven
    # from this hook point (no polling); the dispatcher gates on the
    # per-project transition policy and never raises.
    from app.models.postgres import NotificationEventType
    from app.services.notification_transitions import dispatch_quarantine_transitions
    await dispatch_quarantine_transitions(
        row.project_id,
        NotificationEventType.TEST_QUARANTINED,
        [{
            "test_name": row.test_name,
            "test_fingerprint": row.test_fingerprint,
            "suite_name": row.suite_name,
            "detail": (
                f"flip rate {row.flip_rate:.0%}" if row.flip_rate is not None else None
            ),
        }],
    )

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

    # PMF US-7.1 — test.unquarantined transition notification (hook point;
    # never raises, gated on the per-project transition policy).
    from app.models.postgres import NotificationEventType
    from app.services.notification_transitions import dispatch_quarantine_transitions
    await dispatch_quarantine_transitions(
        row.project_id,
        NotificationEventType.TEST_UNQUARANTINED,
        [{
            "test_name": row.test_name,
            "test_fingerprint": row.test_fingerprint,
            "suite_name": row.suite_name,
            "detail": "released by QA lead",
        }],
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


#: Statuses this sweep can move into ``RECHECK_SCHEDULED``. Both are windowed
#: quarantines that carry a ``recheck_at``, so both must be re-evaluated when
#: that moment passes.
#:
#: ``RE_QUARANTINED`` was missing here, which made it a **dead end**: the
#: recheck cycle writes that status with a fresh window (and a fresh
#: ``recheck_at``), but no sweep selected it, so the row was never re-examined
#: again. It stayed in an active state past its own ``quarantine_expires_at``
#: for ever — permanently in the CI quarantine manifest, permanently tagged
#: ``quarantined`` at ingest, permanently excluded from release-gate scoring.
#: A genuine regression in a once-flaky test could never block a build again.
#: The documented workflow (``postgres.py``, "Flaky Auto-Quarantine") always
#: said ``[RE_QUARANTINED] -> QUARANTINED (new window)``; only the query
#: disagreed.
_RECHECKABLE_STATES = (
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)


async def schedule_pending_rechecks() -> int:
    """Move windowed quarantines whose ``recheck_at`` has passed into
    ``RECHECK_SCHEDULED`` so the next cycle evaluates their recovery.

    Covers both ``QUARANTINED`` (first window) and ``RE_QUARANTINED`` (every
    window after that) — see ``_RECHECKABLE_STATES``.
    """
    if not await _feature_enabled():
        return 0
    now = datetime.now(timezone.utc)
    moved = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.status.in_(_RECHECKABLE_STATES),
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
    released_rows: list[dict[str, Any]] = []

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
                released_rows.append({
                    "project_id": row.project_id,
                    "test_name": getattr(row, "test_name", None),
                    "test_fingerprint": row.test_fingerprint,
                    "suite_name": getattr(row, "suite_name", None),
                    "detail": (
                        f"auto-released after recheck "
                        f"(flip rate {flip_rate:.0%} over {total} runs)"
                    ),
                })
                released += 1
            else:
                row.status = FlakyQuarantineStatus.RE_QUARANTINED.value
                row.quarantine_start = now
                row.quarantine_expires_at = now + timedelta(days=row.quarantine_duration_days)
                row.recheck_at = row.quarantine_expires_at - timedelta(days=1)
                # PMF US-5.4/5.5 — a re-quarantine is a fresh window: restart
                # the SLA clock (when one was snapshotted at activation) and
                # the pass-streak tracking. getattr keeps pre-0104 test
                # doubles working.
                sla_days = getattr(row, "sla_days", None)
                if sla_days:
                    row.stale_at = now + timedelta(days=sla_days)
                    row.stale_notified_at = None
                row.consecutive_passes = 0
                row.ready_to_promote = False
                row.ready_notified_at = None
                re_quarantined += 1

        if rows:
            await db.commit()

    # PMF US-7.1 — test.unquarantined for auto-released rows, AFTER the
    # commit so the notification never claims a rolled-back release.
    # RE_QUARANTINED is deliberately silent: the test is still quarantined,
    # so nothing user-visible transitioned. Grouped per project (one batch
    # message each); the dispatcher never raises.
    if released_rows:
        from app.models.postgres import NotificationEventType
        from app.services.notification_transitions import (
            dispatch_quarantine_transitions,
        )
        by_project: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for entry in released_rows:
            by_project.setdefault(entry["project_id"], []).append(entry)
        for pid, entries in by_project.items():
            await dispatch_quarantine_transitions(
                pid, NotificationEventType.TEST_UNQUARANTINED, entries,
            )
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


# ── Lifecycle: staleness sweep (PMF US-5.4) ─────────────────────────────────


async def mark_stale_quarantines() -> int:
    """Flag active quarantines that have out-lived their SLA window.

    Runs from the nightly beat schedule alongside the other maintenance
    passes. The ``stale`` surfacing itself is DERIVED (``stale_at <= now``
    on the ORM property), so lists/manifests are current without this
    sweep — its job is the once-per-entry ``test.quarantine_stale``
    notification, anchored on ``stale_notified_at`` so a re-run (or the
    next night) never re-notifies the same entry. Returns the number of
    entries flagged this pass.
    """
    if not await _feature_enabled():
        return 0
    now = datetime.now(timezone.utc)
    flagged: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.status.in_(_ACTIVE_QUARANTINE_STATES),
                FlakyQuarantineRequest.stale_at != None,  # noqa: E711
                FlakyQuarantineRequest.stale_at <= now,
                FlakyQuarantineRequest.stale_notified_at == None,  # noqa: E711
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.stale_notified_at = now
            row.updated_at = now
            stale_at = row.stale_at
            if stale_at is not None and stale_at.tzinfo is None:
                stale_at = stale_at.replace(tzinfo=timezone.utc)
            days_over = max(0, (now - stale_at).days) if stale_at else 0
            flagged.append({
                "project_id": row.project_id,
                "test_name": row.test_name,
                "test_fingerprint": row.test_fingerprint,
                "suite_name": row.suite_name,
                "detail": (
                    f"{days_over} day{'s' if days_over != 1 else ''} past its "
                    f"{row.sla_days or _DEFAULT_SLA_DAYS}-day SLA"
                ),
            })
        if rows:
            await db.commit()

    # Notify AFTER the commit so the once-only anchor is durable before the
    # message exists. Grouped per project; the dispatcher never raises.
    if flagged:
        from app.models.postgres import NotificationEventType
        from app.services.notification_transitions import (
            dispatch_quarantine_transitions,
        )
        by_project: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for entry in flagged:
            by_project.setdefault(entry["project_id"], []).append(entry)
        for pid, entries in by_project.items():
            await dispatch_quarantine_transitions(
                pid, NotificationEventType.TEST_QUARANTINE_STALE, entries,
            )
    return len(flagged)


# ── Lifecycle: auto-promotion out of quarantine (PMF US-5.5) ────────────────


def advance_consecutive_passes(current: int, case_statuses: Sequence[str]) -> int:
    """Advance the consecutive-pass counter for ONE finalized run.

    * ANY FAILED/BROKEN result in the run → reset to 0. A single failure
      resets the whole streak (pinned by a regression test).
    * Otherwise, at least one PASSED result → +1. One run is one step,
      regardless of how many retries the run recorded.
    * SKIPPED/UNKNOWN-only runs are no-signal: the counter is unchanged.
    """
    statuses = {
        s if isinstance(s, str) else getattr(s, "value", str(s))
        for s in case_statuses
    }
    if statuses & {TestStatus.FAILED.value, TestStatus.BROKEN.value}:
        return 0
    if TestStatus.PASSED.value in statuses:
        return int(current or 0) + 1
    return int(current or 0)


async def update_quarantine_stability(run_id: uuid.UUID) -> dict[str, int]:
    """Advance pass-streak counters for quarantined tests after a run
    finalizes, and promote at the policy threshold.

    Hooked from the ``dispatch_transition_notifications`` Celery task (the
    same post-ingestion point the transition engine rides), wrapped in its
    own try/except there. Idempotent per (request, run) via
    ``last_stability_run_id`` — a Celery retry or re-finalized run
    advances nothing.

    At ``promote_after_passes`` consecutive fully-passed runs:

    * ``auto_promote`` ON  → the quarantine is RELEASED (audit-logged,
      emits the existing ``test.unquarantined``).
    * ``auto_promote`` OFF → the row is flagged ``ready_to_promote`` and
      the optional ``test.ready_to_unquarantine`` notification fires once
      per threshold crossing (anchor: ``ready_notified_at``, cleared when
      a failure resets the streak).
    """
    empty = {"tracked": 0, "auto_released": 0, "ready": 0}
    if not await _feature_enabled():
        return empty

    from app.models.postgres import TestCase, TestRun

    tracked = 0
    released_notifications: list[dict[str, Any]] = []
    ready_notifications: list[dict[str, Any]] = []
    pending_audits: list[tuple[str, uuid.UUID, uuid.UUID, dict[str, Any], dict[str, Any]]] = []

    async with AsyncSessionLocal() as db:
        run = (
            await db.execute(select(TestRun).where(TestRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            return empty
        project_id = run.project_id

        result = await db.execute(
            select(FlakyQuarantineRequest).where(
                FlakyQuarantineRequest.project_id == project_id,
                FlakyQuarantineRequest.status.in_(_ACTIVE_QUARANTINE_STATES),
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return empty

        fingerprints = {r.test_fingerprint for r in rows}
        case_result = await db.execute(
            select(TestCase.test_fingerprint, TestCase.status).where(
                TestCase.test_run_id == run_id,
                TestCase.test_fingerprint.in_(fingerprints),
            )
        )
        statuses_by_fp: dict[str, list[str]] = {}
        for fp, st in case_result.all():
            statuses_by_fp.setdefault(fp, []).append(
                st if isinstance(st, str) else getattr(st, "value", str(st))
            )
        if not statuses_by_fp:
            return empty

        policy = await get_lifecycle_policy(db, project_id)
        threshold = max(1, policy.promote_after_passes)
        now = datetime.now(timezone.utc)

        for row in rows:
            case_statuses = statuses_by_fp.get(row.test_fingerprint)
            if not case_statuses:
                continue  # quarantined test didn't run in this run
            if row.last_stability_run_id == run_id:
                continue  # idempotent per (request, run) — retry-safe
            row.last_stability_run_id = run_id
            row.consecutive_passes = advance_consecutive_passes(
                row.consecutive_passes or 0, case_statuses,
            )
            if row.consecutive_passes == 0:
                # Failure broke the streak: withdraw any pending promotion
                # and re-arm the once-per-crossing notification anchor.
                row.ready_to_promote = False
                row.ready_notified_at = None
            elif row.consecutive_passes >= threshold:
                if policy.auto_promote:
                    before = _snapshot(row)
                    row.status = FlakyQuarantineStatus.RELEASED.value
                    row.ready_to_promote = False
                    pending_audits.append((
                        "auto_promote_release",
                        row.id, row.project_id, before, _snapshot(row),
                    ))
                    released_notifications.append({
                        "project_id": row.project_id,
                        "test_name": row.test_name,
                        "test_fingerprint": row.test_fingerprint,
                        "suite_name": row.suite_name,
                        "detail": (
                            f"auto-promoted after {row.consecutive_passes} "
                            f"consecutive passing runs"
                        ),
                    })
                elif not row.ready_to_promote:
                    before = _snapshot(row)
                    row.ready_to_promote = True
                    pending_audits.append((
                        "ready_to_promote",
                        row.id, row.project_id, before, _snapshot(row),
                    ))
                    if row.ready_notified_at is None:
                        row.ready_notified_at = now
                        ready_notifications.append({
                            "project_id": row.project_id,
                            "test_name": row.test_name,
                            "test_fingerprint": row.test_fingerprint,
                            "suite_name": row.suite_name,
                            "detail": (
                                f"{row.consecutive_passes} consecutive passing "
                                f"runs — release when ready"
                            ),
                        })
            row.updated_at = now
            tracked += 1

        if tracked:
            await db.commit()

    # Audit + notify AFTER the commit (same ordering rationale as
    # ``expire_stale_proposals``): never log/announce a rolled-back change.
    for action, req_id, proj_id, before, after in pending_audits:
        await _audit(
            db, None,
            action=action,
            request_id=req_id,
            project_id=proj_id,
            before=before,
            after=after,
        )

    from app.models.postgres import NotificationEventType
    from app.services.notification_transitions import (
        dispatch_quarantine_transitions,
    )
    if released_notifications:
        by_project: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for entry in released_notifications:
            by_project.setdefault(entry["project_id"], []).append(entry)
        for pid, entries in by_project.items():
            await dispatch_quarantine_transitions(
                pid, NotificationEventType.TEST_UNQUARANTINED, entries,
            )
    if ready_notifications:
        by_project = {}
        for entry in ready_notifications:
            by_project.setdefault(entry["project_id"], []).append(entry)
        for pid, entries in by_project.items():
            await dispatch_quarantine_transitions(
                pid, NotificationEventType.TEST_READY_TO_UNQUARANTINE, entries,
            )

    if released_notifications:
        from app.core.metrics import quarantine_expired_total
        quarantine_expired_total.labels(terminal_state="released").inc(
            len(released_notifications)
        )
    return {
        "tracked": tracked,
        "auto_released": len(released_notifications),
        "ready": len(ready_notifications),
    }
