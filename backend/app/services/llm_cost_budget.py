"""
LLM cost budget — Tier 1 item 2.

Usage-based billing primitive. Everything runs gated behind the
``llm_cost_budget`` feature flag so the meter can be left off entirely
until operations are ready.

Two public entry points:

* :func:`record_usage` — called from ``BaseAgent.mark_stage_done`` after a
  stage persists its cost. Atomically upserts the per-period meter row so
  two workers writing concurrently don't clobber each other.

* :func:`check_and_apply_cap` — called at the top of ``analysis_agent.run``
  before the first classification. Reads the project's quota config and
  current usage, decides whether to soft-warn, downgrade the analysis
  mode, or hard-block LLM work, and returns the decision so the caller
  can both enforce it and log it into the decision trail.

Design notes
------------

* Current period = calendar-month UTC. Daily/quarterly budgets are a
  trivial extension (``period_type`` already exists on the quota row) but
  aren't shipped yet — guard your cap policies on ``MONTHLY``.

* Meter is atomic via ``INSERT ... ON CONFLICT DO UPDATE`` against the
  ``(project_id, period_start)`` unique key. No Python-side read-modify-
  write.

* All I/O is best-effort — a failure to record a single dollar doesn't
  block the pipeline. We log and move on.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    ProjectLlmQuota,
    ProjectLlmUsage,
    QuotaCapAction,
    User,
)

logger = structlog.get_logger("services.llm_cost_budget")


# ── Period bounds ───────────────────────────────────────────────────────────


def current_period_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return ``(period_start, period_end)`` for the current calendar month UTC.

    Both bounds are timezone-aware. ``period_start`` is the 1st of the
    current month at 00:00 UTC; ``period_end`` is the 1st of the next month
    at 00:00 UTC. Using half-open ``[start, end)`` intervals so membership
    tests are unambiguous.
    """
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


# ── Feature flag check ─────────────────────────────────────────────────────


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    """Short-circuit check: both entry points are no-ops unless the
    ``llm_cost_budget`` flag is enabled. Never raises."""
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("llm_cost_budget", db=db)
    except Exception as exc:
        logger.debug("llm_cost_budget flag check failed", error=str(exc))
        return False


# ── Metering ───────────────────────────────────────────────────────────────


async def record_usage(
    project_id: uuid.UUID | str | None,
    *,
    cost_usd: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    llm_calls: int = 0,
) -> None:
    """Increment the current-period meter for a project.

    Best-effort — any failure is logged at debug and swallowed. The caller
    (``BaseAgent.mark_stage_done``) runs this after the stage's primary
    work is done so we can never block test analysis on a metering failure.
    """
    if project_id is None:
        return
    if cost_usd <= 0 and llm_calls <= 0:
        return
    try:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return

    if not await _feature_enabled():
        return

    period_start, period_end = current_period_bounds()
    now = datetime.now(timezone.utc)

    try:
        async with AsyncSessionLocal() as db:
            stmt = pg_insert(ProjectLlmUsage).values(
                project_id=pid,
                period_start=period_start,
                period_end=period_end,
                total_cost_usd=float(cost_usd or 0.0),
                total_input_tokens=int(input_tokens or 0),
                total_output_tokens=int(output_tokens or 0),
                total_llm_calls=int(llm_calls or 0),
                cap_hits=0,
                last_updated_at=now,
            ).on_conflict_do_update(
                # Uses the ``uq_project_period`` unique index.
                index_elements=["project_id", "period_start"],
                set_={
                    "total_cost_usd": ProjectLlmUsage.total_cost_usd + float(cost_usd or 0.0),
                    "total_input_tokens": ProjectLlmUsage.total_input_tokens + int(input_tokens or 0),
                    "total_output_tokens": ProjectLlmUsage.total_output_tokens + int(output_tokens or 0),
                    "total_llm_calls": ProjectLlmUsage.total_llm_calls + int(llm_calls or 0),
                    "last_updated_at": now,
                },
            )
            await db.execute(stmt)
            await db.commit()
    except Exception as exc:
        logger.debug("record_usage failed", project_id=str(pid), error=str(exc))


async def _load_quota(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[ProjectLlmQuota]:
    result = await db.execute(
        select(ProjectLlmQuota).where(ProjectLlmQuota.project_id == project_id)
    )
    return result.scalar_one_or_none()


async def _load_usage_row(
    db: AsyncSession, project_id: uuid.UUID, period_start: datetime,
) -> Optional[ProjectLlmUsage]:
    result = await db.execute(
        select(ProjectLlmUsage).where(
            ProjectLlmUsage.project_id == project_id,
            ProjectLlmUsage.period_start == period_start,
        )
    )
    return result.scalar_one_or_none()


# ── Cap evaluation ─────────────────────────────────────────────────────────


class CapDecision:
    """Structured result returned from :func:`check_and_apply_cap`.

    Consumers use ``mode_override`` to force a specific analysis engine
    ("ml" or "rules") or ``block`` to raise a hard HTTP 402/503 at the
    caller's discretion. ``rationale`` is intended for
    ``BaseAgent.log_decision`` so the decision trail explains the
    degrade to end users.
    """

    __slots__ = ("action", "mode_override", "block", "rationale", "utilization_pct")

    def __init__(
        self,
        action: str,
        *,
        mode_override: Optional[str] = None,
        block: bool = False,
        rationale: str = "",
        utilization_pct: float = 0.0,
    ) -> None:
        self.action = action
        self.mode_override = mode_override
        self.block = block
        self.rationale = rationale
        self.utilization_pct = utilization_pct

    def is_capped(self) -> bool:
        return self.mode_override is not None or self.block

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "mode_override": self.mode_override,
            "block": self.block,
            "rationale": self.rationale,
            "utilization_pct": round(self.utilization_pct, 2),
        }


_NOOP_DECISION = CapDecision(action="UNLIMITED", rationale="")


async def check_and_apply_cap(
    project_id: uuid.UUID | str,
) -> CapDecision:
    """Evaluate the cap for a project and return the action to take.

    * Returns an ``UNLIMITED`` decision when the feature flag is off, when
      the project has no quota row, when the quota is disabled, when
      ``hard_cap_usd`` is zero (unbounded), or when usage is below the
      soft-warn threshold.
    * Returns the configured action when usage crosses
      ``soft_warn_threshold_pct * hard_cap_usd``.
    * Returns ``HARD_BLOCK`` when usage is at or above 100% of the hard
      cap, regardless of the configured action — the hard cap is a
      wall, not a knob.

    Never raises. On internal error, returns ``UNLIMITED`` (fail-open) so
    we don't accidentally block the pipeline when the meter itself is
    broken. We log loudly so ops catches the degrade.
    """
    try:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return _NOOP_DECISION

    if not await _feature_enabled():
        return _NOOP_DECISION

    try:
        async with AsyncSessionLocal() as db:
            quota = await _load_quota(db, pid)
            if quota is None or not quota.enabled or quota.hard_cap_usd <= 0:
                return _NOOP_DECISION

            period_start, _period_end = current_period_bounds()
            usage = await _load_usage_row(db, pid, period_start)
            current_cost = float(usage.total_cost_usd) if usage else 0.0

            utilization = (current_cost / quota.hard_cap_usd) * 100.0 if quota.hard_cap_usd else 0.0

            # Hard cap wall — always wins.
            if current_cost >= quota.hard_cap_usd:
                decision = CapDecision(
                    action=QuotaCapAction.HARD_BLOCK.value,
                    block=True,
                    rationale=(
                        f"LLM cost budget exceeded: ${current_cost:.4f} "
                        f">= hard cap ${quota.hard_cap_usd:.2f}"
                    ),
                    utilization_pct=utilization,
                )
                await _increment_cap_hit(db, pid, period_start)
                logger.warning(
                    "llm_cost_budget hard cap reached",
                    project_id=str(pid),
                    current_usd=current_cost,
                    cap_usd=quota.hard_cap_usd,
                )
                # Tier 2 item 6 — emit ``quota.exceeded`` so external
                # systems can page ops. Non-blocking.
                try:
                    from app.services.webhook_service import emit_event
                    await emit_event(
                        "quota.exceeded",
                        project_id=pid,
                        payload={
                            "project_id": str(pid),
                            "current_cost_usd": round(current_cost, 6),
                            "hard_cap_usd": float(quota.hard_cap_usd),
                            "utilization_pct": round(utilization, 2),
                            "period_start": period_start.isoformat(),
                            "action": QuotaCapAction.HARD_BLOCK.value,
                        },
                    )
                except Exception as _wh_exc:  # pragma: no cover — best-effort
                    logger.debug("quota.exceeded webhook emit failed", error=str(_wh_exc))
                return decision

            # Soft-warn threshold crossed?
            threshold_usd = quota.hard_cap_usd * (quota.soft_warn_threshold_pct / 100.0)
            if current_cost < threshold_usd:
                return _NOOP_DECISION

            # Apply configured action. For SOFT_WARN we don't override the
            # mode — it's a telemetry-only signal. For the two downgrade
            # actions we return a ``mode_override`` the caller will thread
            # into the analysis router.
            action = quota.at_cap_action
            if action == QuotaCapAction.SOFT_WARN.value:
                decision = CapDecision(
                    action=action,
                    rationale=(
                        f"LLM cost budget at {utilization:.0f}% of ${quota.hard_cap_usd:.2f} cap "
                        f"(soft warn threshold {quota.soft_warn_threshold_pct}%)"
                    ),
                    utilization_pct=utilization,
                )
            elif action == QuotaCapAction.AUTO_DOWNGRADE_TO_ML.value:
                decision = CapDecision(
                    action=action,
                    mode_override="ml",
                    rationale=(
                        f"LLM cost budget at {utilization:.0f}% of cap — "
                        f"auto-downgrade to ML classifier"
                    ),
                    utilization_pct=utilization,
                )
            elif action == QuotaCapAction.AUTO_DOWNGRADE_TO_RULES.value:
                decision = CapDecision(
                    action=action,
                    mode_override="rules",
                    rationale=(
                        f"LLM cost budget at {utilization:.0f}% of cap — "
                        f"auto-downgrade to rules engine"
                    ),
                    utilization_pct=utilization,
                )
            elif action == QuotaCapAction.HARD_BLOCK.value:
                decision = CapDecision(
                    action=action,
                    block=True,
                    rationale=(
                        f"LLM cost budget at {utilization:.0f}% of cap — hard block"
                    ),
                    utilization_pct=utilization,
                )
            else:
                logger.warning("unknown at_cap_action", action=action, project_id=str(pid))
                return _NOOP_DECISION

            await _increment_cap_hit(db, pid, period_start)
            logger.info(
                "llm_cost_budget cap triggered",
                project_id=str(pid),
                action=action,
                utilization_pct=round(utilization, 2),
                cap_usd=quota.hard_cap_usd,
            )
            return decision
    except Exception as exc:
        # Fail-open: never block the pipeline on meter errors.
        logger.warning("check_and_apply_cap failed", project_id=str(project_id), error=str(exc))
        return _NOOP_DECISION


async def _increment_cap_hit(
    db: AsyncSession, project_id: uuid.UUID, period_start: datetime,
) -> None:
    """Increment ``cap_hits`` on the current-period usage row."""
    usage = await _load_usage_row(db, project_id, period_start)
    if usage is None:
        # The cap was hit before any usage was recorded — seed a zero row
        # so the increment has something to land on.
        now = datetime.now(timezone.utc)
        _, period_end = current_period_bounds()
        db.add(
            ProjectLlmUsage(
                project_id=project_id,
                period_start=period_start,
                period_end=period_end,
                total_cost_usd=0.0,
                cap_hits=1,
                last_updated_at=now,
            )
        )
    else:
        usage.cap_hits = int(usage.cap_hits or 0) + 1
    await db.commit()


# ── Read helpers for the billing router ───────────────────────────────────


async def get_quota(db: AsyncSession, project_id: uuid.UUID) -> Optional[ProjectLlmQuota]:
    return await _load_quota(db, project_id)


async def upsert_quota(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: User,
    **fields: Any,
) -> ProjectLlmQuota:
    """Create or update a project's billing config. Writes an audit entry."""
    quota = await _load_quota(db, project_id)
    before: Optional[dict[str, Any]] = None
    if quota is None:
        quota = ProjectLlmQuota(project_id=project_id, updated_by_user_id=actor.id)
        db.add(quota)
    else:
        before = _serialize_quota(quota)
    for key, value in fields.items():
        if value is None:
            continue
        if hasattr(quota, key):
            setattr(quota, key, value)
    quota.updated_by_user_id = actor.id
    quota.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(quota)

    # Audit — settings_audit_log uses field names only, no secret values.
    try:
        from app.models.postgres import SettingsAuditLog
        changed = sorted([k for k in fields if fields[k] is not None])
        entry = SettingsAuditLog(
            setting_key=f"llm_cost_budget:{project_id}",
            action="update" if before else "create",
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=changed,
        )
        db.add(entry)
        await db.commit()
    except Exception as exc:
        logger.warning("quota audit write failed", error=str(exc))
    return quota


def _serialize_quota(quota: ProjectLlmQuota) -> dict[str, Any]:
    return {
        "enabled": quota.enabled,
        "period_type": quota.period_type,
        "included_usd": quota.included_usd,
        "overage_rate_usd": quota.overage_rate_usd,
        "hard_cap_usd": quota.hard_cap_usd,
        "soft_warn_threshold_pct": quota.soft_warn_threshold_pct,
        "at_cap_action": quota.at_cap_action,
    }


async def get_current_usage(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    """Return the current-period usage row merged with its quota config.

    Used by the billing router. The merged shape mirrors ``LlmUsageRead``
    so the response can be passed straight to the Pydantic model.
    """
    period_start, period_end = current_period_bounds()
    usage = await _load_usage_row(db, project_id, period_start)
    quota = await _load_quota(db, project_id)

    base = {
        "project_id": project_id,
        "period_start": period_start,
        "period_end": period_end,
        "total_cost_usd": float(usage.total_cost_usd) if usage else 0.0,
        "total_input_tokens": int(usage.total_input_tokens) if usage else 0,
        "total_output_tokens": int(usage.total_output_tokens) if usage else 0,
        "total_llm_calls": int(usage.total_llm_calls) if usage else 0,
        "cap_hits": int(usage.cap_hits) if usage else 0,
        "included_usd": None,
        "hard_cap_usd": None,
        "utilization_pct": None,
        "status": "UNLIMITED",
    }
    if quota is None or not quota.enabled:
        return base

    base["included_usd"] = float(quota.included_usd)
    base["hard_cap_usd"] = float(quota.hard_cap_usd)
    if quota.hard_cap_usd > 0:
        util = (base["total_cost_usd"] / quota.hard_cap_usd) * 100.0
        base["utilization_pct"] = round(util, 2)
        threshold = quota.soft_warn_threshold_pct
        if base["total_cost_usd"] >= quota.hard_cap_usd:
            base["status"] = "CAPPED"
        elif util >= threshold:
            base["status"] = "SOFT_WARN"
        else:
            base["status"] = "OK"
    else:
        base["status"] = "OK"
    return base


async def list_usage_history(
    db: AsyncSession, project_id: uuid.UUID, *, limit: int = 12,
) -> list[ProjectLlmUsage]:
    """Return the last ``limit`` periods for a project, newest first."""
    result = await db.execute(
        select(ProjectLlmUsage)
        .where(ProjectLlmUsage.project_id == project_id)
        .order_by(ProjectLlmUsage.period_start.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
