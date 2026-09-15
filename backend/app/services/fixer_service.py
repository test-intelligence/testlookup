"""Fixer config + run-gate + serialization core (Agentic plan AI-2).

Mirrors ``agent_investigation_service`` for the Investigator; the Fixer's
governance lives in the canonical ``agent_configs`` row under ``agent_id='fixer'``:

* ``enabled`` + ``mode`` map to the row columns (``mode`` restricted to
  shadow|suggest — ``act`` is rejected at the policy layer, like the
  Investigator).
* the ``budgets`` JSONB carries the fixer-specific block — the runner config,
  test globs, per-run/per-test budgets, validation rerun count,
  concurrent-open-PR cap, and schedule — so no new config table is needed.

The ``fix_attempts`` rows are the per-attempt progress + audit surface;
``write_fixer_ledger`` writes the run's ``agent_runs`` ledger entry through
the shared ``agent_investigation_service.write_agent_run_row`` helper.

Transaction discipline: every ``db``-taking function stages only
(``db.add`` / mutate / ``flush``) — the router or the Celery-owned workflow
runner owns the commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.fixer.state import (
    DEFAULT_FIXER_BUDGETS,
    DEFAULT_TEST_GLOBS,
    FIXER_AGENT_ID,
    VALID_FIXER_MODES,
    VALID_RUNNER_TYPES,
    VALID_SCHEDULES,
    is_valid_runner_image,
)
from app.models.postgres import AgentConfig, FixAttempt
from app.services import agent_investigation_service as inv_svc

logger = structlog.get_logger("services.fixer")

# A fixer run whose attempts are still non-terminal AND younger than this is
# considered "in flight" for the 409 already-running gate.
_ACTIVE_RUN_WINDOW = timedelta(hours=1)
_NON_TERMINAL_STATUSES = ("selected", "diagnosing", "generating", "validating")


# ── Typed gate errors (router maps to HTTP statuses) ─────────────────────────


class FixerDisabled(Exception):
    """The project's fixer policy is disabled."""


class FixerAlreadyRunning(Exception):
    """A fixer run is already in flight for this project."""


class FixerRunnerRequiredForSuggest(Exception):
    """Suggest mode requires a configured (non-none) runner."""


# ── Config (agent_configs row for agent_id='fixer') ──────────────────────────


def _default_runner() -> dict[str, Any]:
    return {"type": "none", "runner_image": None, "command_template": None, "workflow_ref": None}


def _coerce_runner(raw: Optional[dict]) -> dict[str, Any]:
    raw = raw or {}
    rtype = str(raw.get("type") or "none")
    if rtype not in VALID_RUNNER_TYPES:
        rtype = "none"
    # Security: runner_image lands in ``docker run``'s argv — a value like
    # ``--privileged`` must never survive the config layer (the router's
    # field validator rejects it at write time; this coercion also cleans any
    # pre-existing stored value). runners.build_docker_run_argv re-asserts.
    runner_image = raw.get("runner_image")
    if runner_image is not None and not is_valid_runner_image(runner_image):
        logger.warning("fixer_runner_image_rejected", runner_image=str(runner_image)[:200])
        runner_image = None
    return {
        "type": rtype,
        "runner_image": runner_image,
        "command_template": raw.get("command_template"),
        "workflow_ref": raw.get("workflow_ref"),
    }


def _coerce_budgets(raw: Optional[dict]) -> dict[str, int]:
    budgets = dict(DEFAULT_FIXER_BUDGETS)
    for key in DEFAULT_FIXER_BUDGETS:
        v = (raw or {}).get(key)
        if isinstance(v, (int, float)) and v >= 0:
            budgets[key] = int(v)
    # validation_reruns must be >= 1 (M reruns; validated iff all pass).
    budgets["validation_reruns"] = max(1, budgets["validation_reruns"])
    return budgets


def _coerce_globs(raw: Any) -> list[str]:
    if isinstance(raw, list) and raw:
        globs = [str(g) for g in raw if str(g).strip()]
        if globs:
            return globs
    return list(DEFAULT_TEST_GLOBS)


def serialize_fixer_config(row: Optional[AgentConfig]) -> dict[str, Any]:
    """FixerConfig wire shape (pinned API contract). Missing row → defaults:
    disabled, shadow, runner type none, default globs, budgets 3/2/5/2, off."""
    from app.services import agent_config_service as config_svc

    config = config_svc.AgentConfigV1.model_validate(config_svc.serialize(FIXER_AGENT_ID, row)["config"])
    extension = config.extensions.fixer or config_svc.FixerConfigExtension()
    stored = extension.model_dump(mode="json")
    return {
        "enabled": config.enabled,
        "mode": config.mode if config.mode in VALID_FIXER_MODES else "shadow",
        "runner": _coerce_runner(stored.get("runner")),
        "test_globs": _coerce_globs(stored.get("test_globs")),
        "budgets": _coerce_budgets(stored.get("budgets")),
        "schedule": stored.get("schedule") if stored.get("schedule") in VALID_SCHEDULES else "off",
    }


async def get_fixer_policy_row(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[AgentConfig]:
    return await inv_svc.get_policy_row(db, project_id, FIXER_AGENT_ID)


async def get_effective_config(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    return serialize_fixer_config(await get_fixer_policy_row(db, project_id))


async def upsert_fixer_config(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    enabled: bool,
    mode: str,
    runner: dict[str, Any],
    test_globs: list[str],
    budgets: dict[str, int],
    schedule: str,
) -> AgentConfig:
    """Compatibility helper that writes the canonical AgentConfig row."""
    if mode not in VALID_FIXER_MODES:
        raise ValueError(f"invalid fixer mode {mode!r} — expected one of {VALID_FIXER_MODES}")
    runner = _coerce_runner(runner)
    if schedule not in VALID_SCHEDULES:
        schedule = "off"
    from app.services import agent_config_service as config_svc

    row = await get_fixer_policy_row(db, project_id)
    current = config_svc.AgentConfigV1.model_validate(config_svc.serialize(FIXER_AGENT_ID, row)["config"])
    extension = config_svc.FixerConfigExtension(
        runner=config_svc.FixerRunnerExtension.model_validate(runner),
        test_globs=_coerce_globs(test_globs),
        budgets=config_svc.FixerPolicyBudgets.model_validate(_coerce_budgets(budgets)),
        schedule=schedule,
    )
    document = current.model_dump(mode="json")
    document.update({"enabled": bool(enabled), "mode": mode})
    document["extensions"]["fixer"] = extension.model_dump(mode="json")
    config = config_svc.AgentConfigV1.model_validate(document)
    return await config_svc.put_config(db, project_id, config, updated_by=None)


# ── Run gate ─────────────────────────────────────────────────────────────────

# Cross-process dispatch lock: the DB active-run gate alone races when the
# beat dispatcher and a manual POST gate simultaneously (both see "no active
# attempt" before either run writes one). TTL comfortably outlives the task's
# 1h Celery time_limit; released in workflow._finalize.
_RUN_LOCK_TTL_S = 2 * 60 * 60


def _run_lock_key(project_id: uuid.UUID) -> str:
    return f"fixer:run:{project_id}"


async def acquire_fixer_run_lock(project_id: uuid.UUID) -> bool:
    """SETNX-style dispatch lock. Returns False when another dispatch already
    holds it. FAILS OPEN (True + warning) when Redis is unavailable —
    availability beats strictness here because the DB active-run gate still
    applies; the lock only closes the small dispatch race window."""
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        acquired = await redis.set(_run_lock_key(project_id), "1", ex=_RUN_LOCK_TTL_S, nx=True)
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.warning(
            "fixer_run_lock_unavailable", project_id=str(project_id), error=str(exc),
        )
        return True


async def release_fixer_run_lock(project_id: uuid.UUID) -> None:
    """Best-effort lock release (the TTL is the backstop). Never raises."""
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        await redis.delete(_run_lock_key(project_id))
    except Exception as exc:  # noqa: BLE001 — TTL will reap it
        logger.warning(
            "fixer_run_lock_release_failed", project_id=str(project_id), error=str(exc),
        )


async def has_active_fixer_run(db: AsyncSession, project_id: uuid.UUID) -> bool:
    """True when a fixer run is in flight (a non-terminal attempt younger than
    the active window) — backs the 409 already-running response."""
    cutoff = datetime.now(timezone.utc) - _ACTIVE_RUN_WINDOW
    count = int((
        await db.execute(
            select(func.count(FixAttempt.id)).where(
                FixAttempt.project_id == project_id,
                FixAttempt.status.in_(_NON_TERMINAL_STATUSES),
                FixAttempt.created_at >= cutoff,
            )
        )
    ).scalar() or 0)
    return count > 0


async def gate_fixer_run(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, Any]:
    """Validate that a run may start. Raises the typed gate errors the router
    maps to 403/409/422. Returns the effective config on success.

    Passing the gate also ACQUIRES the cross-process dispatch lock (the DB
    check alone races between the beat and a concurrent manual POST) — a
    caller whose enqueue subsequently fails must release it; the executed
    run releases it in ``workflow._finalize``."""
    config = await get_effective_config(db, project_id)
    if not config["enabled"]:
        raise FixerDisabled()
    if config["mode"] == "suggest" and config["runner"]["type"] == "none":
        raise FixerRunnerRequiredForSuggest()
    if await has_active_fixer_run(db, project_id):
        raise FixerAlreadyRunning()
    if not await acquire_fixer_run_lock(project_id):
        raise FixerAlreadyRunning()
    return config


def enqueue_fixer_run(project_id: uuid.UUID, fixer_run_id: uuid.UUID, triggered_by: str) -> bool:
    """Queue the Celery task. Returns False when the broker is unreachable."""
    try:
        from app.worker.tasks import run_fixer_run_task

        run_fixer_run_task.delay(str(project_id), str(fixer_run_id), triggered_by)
        return True
    except Exception as exc:  # noqa: BLE001 — broker faults must not 500 the API
        logger.warning(
            "fixer_enqueue_failed",
            project_id=str(project_id),
            fixer_run_id=str(fixer_run_id),
            error=str(exc),
        )
        return False


# ── Ledger ───────────────────────────────────────────────────────────────────


async def write_fixer_ledger(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    fixer_run_id: uuid.UUID,
    mode: str,
    trigger: str,
    status: str,
    summary: str,
    actions_proposed: Optional[list[str]] = None,
    actions_taken: Optional[list[str]] = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    prompt_registry_digest: Optional[str] = None,
):
    """Write the fixer run's ``agent_runs`` ledger entry (agent_id='fixer')
    through the shared writer so the ledger + Mongo audit mirror stay uniform.
    ``actions_taken`` carries opened PR urls in suggest mode; ``[]`` in shadow.
    """
    return await inv_svc.write_agent_run_row(
        db,
        agent_id=FIXER_AGENT_ID,
        project_id=project_id,
        run_id=None,
        mode=mode,
        trigger=trigger,
        status=status,
        summary=summary,
        details_path=f"/fixer/runs/{fixer_run_id}",
        event_source_id=str(fixer_run_id),
        stage_name="fixer",
        actions_proposed=actions_proposed,
        actions_taken=actions_taken,
        tokens=tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        prompt_registry_digest=prompt_registry_digest,
    )


# ── Wire serialization (pinned API contract) ─────────────────────────────────


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def serialize_attempt(row: FixAttempt, *, detail: bool = False) -> dict[str, Any]:
    """FixAttempt wire shape. ``detail=True`` adds patch + runner_log_digest +
    ledger_run_id (the single-attempt endpoint)."""
    validation: Optional[dict[str, int]] = None
    if row.validation_reruns is not None:
        validation = {
            "reruns": int(row.validation_reruns or 0),
            "passed": int(row.validation_passed or 0),
        }
    data: dict[str, Any] = {
        "id": str(row.id),
        "fixer_run_id": str(row.fixer_run_id),
        "test_fingerprint": row.test_fingerprint,
        "test_name": row.test_name,
        "status": row.status,
        "attempt_no": int(row.attempt_no or 1),
        "patch_summary": row.patch_summary,
        "validation": validation,
        "pr_url": row.pr_url,
        "reason": row.reason,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
    }
    if detail:
        data["patch"] = row.patch
        data["runner_log_digest"] = row.runner_log_digest
        data["ledger_run_id"] = str(row.ledger_run_id) if row.ledger_run_id else None
    return data
