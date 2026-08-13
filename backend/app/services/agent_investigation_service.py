"""Investigator lifecycle + agent governance core (Agentic plan AI-1 / AI-3).

This module owns everything around the hypothesis-loop Investigator EXCEPT
the reasoning itself (which lives in ``app/agents/investigator/``):

* **Policies** — per-(project, agent) :class:`AgentPolicy` rows with a
  code-side default (enabled, shadow, budgets 10/30/60000/300) when no row
  exists. The PUT endpoint stages through :func:`upsert_policy`.
* **Trigger gate** — :func:`start_investigation` enforces the policy
  (enabled flag, ``max_runs_per_day``) and the one-active-per-run
  invariant, raising typed errors the router maps to 403/409/429. The DB
  partial unique index (migration 0108) backs the one-active check against
  races.
* **Auto-trigger** — :func:`maybe_auto_trigger` is the hook the
  transition-notification engine (``test.newly_failing``) and the release
  gate (NO_GO) call. Own session, own try/except — it must NEVER affect
  the host path.
* **Ledger (AI-3)** — :func:`record_agent_run` writes one
  :class:`AgentRun` row per investigation outcome and mirrors a durable
  ``agent_run_recorded`` event to the Mongo pipeline event log.
* **Wire serialization** — the InvestigationDetail / InvestigationSummary /
  AgentPolicy / AgentRunEntry JSON shapes are pinned API contract; the
  frontend is built against them verbatim. Do not rename keys.

Transaction discipline: every ``db``-taking function stages only
(``db.add`` / mutate / ``flush``) — the router or the Celery-owned workflow
runner owns the commit. :func:`maybe_auto_trigger` is the exception by
design (fire-and-forget hook with its own session).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    AgentInvestigation,
    AgentPolicy,
    AgentRun,
    Project,
    TestRun,
)

logger = structlog.get_logger("services.agent_investigation")


AGENT_ID_INVESTIGATOR = "investigator"
KNOWN_AGENT_IDS: tuple[str, ...] = (AGENT_ID_INVESTIGATOR,)

VALID_MODES: tuple[str, ...] = ("shadow", "suggest", "act")

# Default policy budgets when no AgentPolicy row exists (API contract).
DEFAULT_BUDGETS: dict[str, int | float] = {
    "max_runs_per_day": 10,
    "max_llm_calls_per_run": 30,
    "max_tokens_per_run": 60000,
    "max_cost_usd_per_run": 5.0,
    "max_seconds_per_run": 300,
    "max_cluster_children_per_run": 1,
    "max_cluster_members_per_child": 50,
    "max_cluster_child_llm_calls_per_parent": 6,
    "max_cluster_child_tokens_per_parent": 12000,
    "max_cluster_child_cost_usd_per_parent": 2.0,
    "max_cluster_child_seconds_per_parent": 180,
    "max_active_cluster_children_per_project": 2,
    "max_cluster_children_per_day": 20,
}

# The five hypotheses, in fixed presentation order. Seeded as "pending"
# entries on the investigation row at creation so the polling UI renders
# the full board immediately.
HYPOTHESIS_SPECS: tuple[tuple[str, str], ...] = (
    ("infra", "Infrastructure failure (runners, services, platform)"),
    ("commit", "Code change onset (commit vs baseline)"),
    ("environment", "Environment drift vs baseline"),
    ("known_flaky", "Known-flaky recurrence"),
    ("regression", "Genuine product regression"),
)
HYPOTHESIS_IDS: tuple[str, ...] = tuple(h[0] for h in HYPOTHESIS_SPECS)

INVESTIGATION_ACTIVE_STATUSES = AgentInvestigation.ACTIVE_STATUSES


# ── Typed trigger errors (router maps to HTTP statuses) ──────────────────────


class InvestigationPolicyDisabled(Exception):
    """The project's agent policy disables the investigator."""


class InvestigationAlreadyActive(Exception):
    """An investigation is already queued/running for this run."""

    def __init__(self, investigation_id: uuid.UUID):
        self.investigation_id = investigation_id
        super().__init__(f"investigation {investigation_id} already active")


class InvestigationDailyBudgetExceeded(Exception):
    """The policy's max_runs_per_day budget is exhausted for today."""

    def __init__(self, max_runs_per_day: int):
        self.max_runs_per_day = max_runs_per_day
        super().__init__(f"max_runs_per_day={max_runs_per_day} exhausted")


# ── Policies ─────────────────────────────────────────────────────────────────


def _effective_budgets(raw: Optional[dict]) -> dict[str, int | float]:
    """Merge a stored budgets JSONB over the defaults (missing keys resolve)."""
    budgets = dict(DEFAULT_BUDGETS)
    for key in DEFAULT_BUDGETS:
        value = (raw or {}).get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            budgets[key] = float(value) if isinstance(DEFAULT_BUDGETS[key], float) else int(value)
    return budgets


def serialize_policy(
    agent_id: str, row: Optional[AgentPolicy]
) -> dict[str, Any]:
    """AgentPolicy wire shape (pinned API contract)."""
    if row is None:
        return {
            "agent_id": agent_id,
            "enabled": True,
            "mode": "shadow",
            "budgets": dict(DEFAULT_BUDGETS),
            "promotion": {"shadow_runs_completed": 0, "note": None},
        }
    return {
        "agent_id": row.agent_id,
        "enabled": bool(row.enabled),
        "mode": row.mode,
        "budgets": _effective_budgets(row.budgets),
        "promotion": {
            "shadow_runs_completed": int(row.shadow_runs_completed or 0),
            "note": row.promotion_note,
        },
    }


async def get_policy_row(
    db: AsyncSession, project_id: uuid.UUID, agent_id: str
) -> Optional[AgentPolicy]:
    result = await db.execute(
        select(AgentPolicy).where(
            AgentPolicy.project_id == project_id,
            AgentPolicy.agent_id == agent_id,
        )
    )
    return result.scalar_one_or_none()


async def get_effective_policy(
    db: AsyncSession, project_id: uuid.UUID, agent_id: str = AGENT_ID_INVESTIGATOR
) -> dict[str, Any]:
    """Resolved policy dict — a missing row resolves to the default."""
    return serialize_policy(agent_id, await get_policy_row(db, project_id, agent_id))


async def upsert_policy(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_id: str,
    *,
    enabled: bool,
    mode: str,
    budgets: Optional[dict] = None,
    promotion_note: Optional[str] = None,
) -> AgentPolicy:
    """Create or update the policy row. Stage-only — the router handler owns
    ``db.commit()`` (transaction-boundary discipline).

    ``shadow_runs_completed`` is server-maintained (the workflow runner
    increments it) and deliberately not writable here.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode {mode!r} — expected one of {VALID_MODES}")
    row = await get_policy_row(db, project_id, agent_id)
    if row is None:
        row = AgentPolicy(project_id=project_id, agent_id=agent_id)
        db.add(row)
    row.enabled = bool(enabled)
    row.mode = mode
    row.budgets = _effective_budgets(budgets)
    row.promotion_note = promotion_note
    await db.flush()
    return row


def run_budget_from_policy(policy: dict[str, Any]) -> dict[str, int | float]:
    """Per-run budget (InvestigationDetail.budget shape) from a policy dict."""
    budgets = _effective_budgets(policy.get("budgets"))
    return {
        "max_llm_calls": budgets["max_llm_calls_per_run"],
        "max_tokens": budgets["max_tokens_per_run"],
        "max_cost_usd": budgets["max_cost_usd_per_run"],
        "max_seconds": budgets["max_seconds_per_run"],
    }


# ── Investigation lifecycle ──────────────────────────────────────────────────


def _pending_hypotheses() -> list[dict[str, Any]]:
    return [
        {
            "id": hyp_id,
            "title": title,
            "status": "pending",
            "confidence": 0,
            "confidence_basis": "heuristic_estimate",
            "summary": "",
            "evidence": [],
            "started_at": None,
            "completed_at": None,
        }
        for hyp_id, title in HYPOTHESIS_SPECS
    ]


async def get_active_investigation_for_run(
    db: AsyncSession, run_id: uuid.UUID
) -> Optional[AgentInvestigation]:
    result = await db.execute(
        select(AgentInvestigation)
        .where(
            AgentInvestigation.run_id == run_id,
            AgentInvestigation.scope_type == "run",
            AgentInvestigation.status.in_(INVESTIGATION_ACTIVE_STATUSES),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def count_investigations_today(
    db: AsyncSession, project_id: uuid.UUID
) -> int:
    """Investigations created for the project since UTC midnight — the
    denominator for the ``max_runs_per_day`` trigger-time budget."""
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await db.execute(
        select(func.count(AgentInvestigation.id)).where(
            AgentInvestigation.project_id == project_id,
            AgentInvestigation.scope_type == "run",
            AgentInvestigation.created_at >= midnight,
        )
    )
    return int(result.scalar() or 0)


async def start_investigation(
    db: AsyncSession,
    run: TestRun,
    *,
    triggered_by: str = "manual",
) -> AgentInvestigation:
    """Gate + create an investigation row for a run. Stage-only (flush);
    the caller owns the commit and enqueues the Celery task AFTER commit.

    Raises:
        InvestigationPolicyDisabled: the project policy disables the agent.
        InvestigationAlreadyActive: an active investigation exists for the run.
        InvestigationDailyBudgetExceeded: max_runs_per_day exhausted.
    """
    # Serialize the count+insert gate per project so concurrent runs cannot
    # both pass max_runs_per_day. The project row always exists, unlike an
    # optional AgentPolicy row.
    await db.execute(
        select(Project.id).where(Project.id == run.project_id).with_for_update()
    )
    policy = await get_effective_policy(db, run.project_id, AGENT_ID_INVESTIGATOR)
    if not policy["enabled"]:
        raise InvestigationPolicyDisabled()

    active = await get_active_investigation_for_run(db, run.id)
    if active is not None:
        raise InvestigationAlreadyActive(active.id)

    max_per_day = _effective_budgets(policy.get("budgets"))["max_runs_per_day"]
    if await count_investigations_today(db, run.project_id) >= max_per_day:
        raise InvestigationDailyBudgetExceeded(max_per_day)

    investigation = AgentInvestigation(
        project_id=run.project_id,
        run_id=run.id,
        status="queued",
        mode=policy["mode"],
        triggered_by=triggered_by,
        budget=run_budget_from_policy(policy),
        spend={
            "ledger_version": 2,
            "llm_calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "seconds": 0.0,
            "reservations": {},
            "completed_reservations": {},
        },
        hypotheses=_pending_hypotheses(),
    )
    db.add(investigation)
    await db.flush()
    return investigation


async def request_cancel(
    db: AsyncSession,
    investigation: AgentInvestigation,
    *,
    cancelled_by: Optional[str] = None,
) -> AgentInvestigation:
    """Set the cooperative cancel flag. Stage-only — router owns commit.

    The workflow checks the flag between nodes and finalizes the row to
    ``status="cancelled"``. Terminal rows are left untouched.
    """
    if investigation.status in INVESTIGATION_ACTIVE_STATUSES:
        investigation.cancel_requested = True
        investigation.cancelled_by = cancelled_by
        await db.flush()
    return investigation


def enqueue_investigation_task(investigation_id: uuid.UUID) -> bool:
    """Queue the Celery task (ai_analysis queue). Returns False when the
    broker is unreachable — callers log and surface the row as queued; the
    stale-queued row can be re-triggered manually."""
    try:
        from app.worker.tasks import run_agent_investigation

        run_agent_investigation.delay(str(investigation_id))
        return True
    except Exception as exc:  # noqa: BLE001 — broker faults must not 500 the API
        logger.warning(
            "investigation_enqueue_failed",
            investigation_id=str(investigation_id),
            error_type=type(exc).__name__,
        )
        return False


# ── Auto-trigger hook (transition engine / release gate) ────────────────────


async def maybe_auto_trigger(run_id: uuid.UUID, trigger: str) -> Optional[uuid.UUID]:
    """Best-effort auto-trigger from host paths (``auto:newly_failing`` /
    ``auto:gate_no_go``). Owns its session and commits itself (the callers
    are Celery-owned paths, not request handlers). NEVER raises — a broken
    investigator must not affect run finalization or the release gate.

    Returns the new investigation id, or None when gated/failed.
    """
    try:
        from app.db.postgres import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(TestRun).where(TestRun.id == run_id))
            ).scalar_one_or_none()
            if run is None:
                return None
            try:
                investigation = await start_investigation(
                    db, run, triggered_by=trigger
                )
            except (
                InvestigationPolicyDisabled,
                InvestigationAlreadyActive,
                InvestigationDailyBudgetExceeded,
            ) as gate:
                logger.info(
                    "investigation_auto_trigger_gated",
                    run_id=str(run_id),
                    trigger=trigger,
                    reason=type(gate).__name__,
                )
                return None
            await db.commit()
            investigation_id = investigation.id
        enqueue_investigation_task(investigation_id)
        logger.info(
            "investigation_auto_triggered",
            run_id=str(run_id),
            trigger=trigger,
            investigation_id=str(investigation_id),
        )
        return investigation_id
    except Exception as exc:  # noqa: BLE001 — host path must never break
        logger.warning(
            "investigation_auto_trigger_failed",
            run_id=str(run_id),
            trigger=trigger,
            error_type=type(exc).__name__,
        )
        return None


# ── Ledger (AI-3) ────────────────────────────────────────────────────────────


async def write_agent_run_row(
    db: AsyncSession,
    *,
    agent_id: str,
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    mode: str,
    trigger: str,
    status: str,
    summary: str,
    details_path: str,
    event_source_id: str,
    stage_name: str,
    actions_proposed: Optional[list[str]] = None,
    actions_taken: Optional[list[str]] = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    prompt_registry_digest: Optional[str] = None,
) -> AgentRun:
    """Generic AgentRun ledger writer shared by every governed agent (AI-3).

    Writes one ``agent_runs`` row and mirrors a durable
    ``agent_run_recorded`` event to the Mongo pipeline event log. Stage-only
    on the PG side (caller commits); the Mongo mirror is best-effort. The
    Investigator (:func:`record_agent_run`) and the Fixer both funnel through
    here so the ledger shape + audit mirror stay identical across agents.
    """
    entry = AgentRun(
        agent_id=agent_id,
        project_id=project_id,
        run_id=run_id,
        mode=mode,
        trigger=trigger,
        status=status,
        summary=(summary or "")[:2000],
        actions_proposed=list(actions_proposed or []),
        actions_taken=list(actions_taken or []),
        tokens=int(tokens or 0),
        cost_usd=float(cost_usd or 0.0),
        duration_ms=int(duration_ms or 0),
        prompt_registry_digest=prompt_registry_digest,
        details_path=details_path,
    )
    db.add(entry)
    await db.flush()

    # Durable Mongo mirror — same append-only stream the pipeline audit uses.
    try:
        from app.services.pipeline_event_log import emit_event

        await emit_event(
            event_source_id,
            "agent_run_recorded",
            stage_name=stage_name,
            detail={
                "agent_run_id": str(entry.id),
                "agent_id": entry.agent_id,
                "project_id": str(entry.project_id),
                "run_id": str(entry.run_id) if entry.run_id else None,
                "mode": entry.mode,
                "trigger": entry.trigger,
                "status": entry.status,
                "summary": entry.summary,
                "actions_proposed": entry.actions_proposed,
                "actions_taken": entry.actions_taken,
                "tokens": entry.tokens,
                "cost_usd": entry.cost_usd,
                "duration_ms": entry.duration_ms,
                "prompt_registry_digest": entry.prompt_registry_digest,
            },
        )
    except Exception as exc:  # noqa: BLE001 — mirror is best-effort
        logger.warning(
            "agent_run_mongo_mirror_failed",
            agent_id=agent_id,
            event_source_id=event_source_id,
            error_type=type(exc).__name__,
        )
    return entry


async def record_agent_run(
    db: AsyncSession,
    investigation: AgentInvestigation,
    *,
    status: str,
    summary: str,
    actions_proposed: Optional[list[str]] = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    prompt_registry_digest: Optional[str] = None,
) -> AgentRun:
    """Write the AgentRun ledger row for an investigation outcome (AI-3).

    ``actions_taken`` is ALWAYS ``[]`` this slice — shadow and suggest modes
    are observation-only, and ``act`` is reserved.
    """
    return await write_agent_run_row(
        db,
        agent_id=AGENT_ID_INVESTIGATOR,
        project_id=investigation.project_id,
        run_id=investigation.run_id,
        mode=investigation.mode,
        trigger=investigation.triggered_by,
        status=status,
        summary=summary,
        details_path=f"/investigations/{investigation.id}",
        event_source_id=str(investigation.id),
        stage_name="investigator",
        actions_proposed=actions_proposed,
        actions_taken=[],
        tokens=tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        prompt_registry_digest=prompt_registry_digest,
    )


# ── Wire serialization (pinned API contract) ─────────────────────────────────


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def serialize_investigation_detail(inv: AgentInvestigation) -> dict[str, Any]:
    """InvestigationDetail wire shape — pinned; the frontend consumes this
    verbatim. ``model_info`` (ORM) maps to the contract key ``model``."""
    spend = dict(inv.spend or {})
    return {
        "id": str(inv.id),
        "run_id": str(inv.run_id),
        "project_id": str(inv.project_id),
        "status": inv.status,
        "mode": inv.mode,
        "triggered_by": inv.triggered_by,
        "started_at": _iso(inv.started_at),
        "completed_at": _iso(inv.completed_at),
        "cancelled_by": inv.cancelled_by,
        "budget": {
            "max_llm_calls": int((inv.budget or {}).get("max_llm_calls", 0)),
            "max_tokens": int((inv.budget or {}).get("max_tokens", 0)),
            "max_seconds": int((inv.budget or {}).get("max_seconds", 0)),
        },
        "spend": {
            "llm_calls": int(spend.get("llm_calls", 0)),
            "tokens": int(spend.get("tokens", 0)),
            "cost_usd": float(spend.get("cost_usd", 0.0)),
            "seconds": float(spend.get("seconds", 0.0)),
        },
        "hypotheses": list(inv.hypotheses or []),
        "verdict": inv.verdict,
        "prompt_versions": dict(inv.prompt_versions or {}),
        "model": inv.model_info,
    }


def serialize_investigation_summary(
    inv: AgentInvestigation, build_number: Optional[str]
) -> dict[str, Any]:
    """InvestigationSummary wire shape (project list endpoint)."""
    verdict = inv.verdict or {}
    return {
        "id": str(inv.id),
        "run_id": str(inv.run_id),
        "run_build_number": build_number,
        "status": inv.status,
        "mode": inv.mode,
        "triggered_by": inv.triggered_by,
        "primary_cause": verdict.get("primary_cause"),
        "confidence": verdict.get("confidence"),
        "started_at": _iso(inv.started_at),
        "completed_at": _iso(inv.completed_at),
    }


def serialize_agent_run(row: AgentRun) -> dict[str, Any]:
    """AgentRunEntry wire shape (ledger endpoint + compliance packs)."""
    return {
        "id": str(row.id),
        "agent_id": row.agent_id,
        "project_id": str(row.project_id),
        "run_id": str(row.run_id) if row.run_id else None,
        "mode": row.mode,
        "trigger": row.trigger,
        "status": row.status,
        "summary": row.summary or "",
        "actions_proposed": list(row.actions_proposed or []),
        "actions_taken": list(row.actions_taken or []),
        "tokens": int(row.tokens or 0),
        "cost_usd": float(row.cost_usd or 0.0),
        "duration_ms": int(row.duration_ms or 0),
        "prompt_registry_digest": row.prompt_registry_digest,
        "created_at": _iso(row.created_at),
        "details_path": row.details_path,
    }
