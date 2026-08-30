"""AI pipeline debouncer (Phase 3 of scalable-ingestion).

Before this module, every live-run completion fired one
``run_agent_pipeline`` Celery task. With 500 concurrent runs that's
500 LLM-backed pipelines queued in a 2-minute window — guaranteed to
saturate the LLM provider (Ollama / hosted Anthropic), guaranteed to
blow past the $10/project/day budget on hosted LLMs, and guaranteed
to make every per-run analysis tail-latency hours.

The debouncer replaces that 1:1 enqueue with a Redis SortedSet
buffer. ``enqueue_pipeline_for_run`` adds the run; a Celery beat
task (``flush_ai_pipeline_queue``, configured in
``worker/celery_app.py``) drains the buffer every 2 minutes,
groups by project, consults the cost budget, and fans out one
pipeline per run with the budget's ``mode_override`` baked in.

Why a per-project group, not per-run alone?
-------------------------------------------

* Cost-budget gating is per-project — the meter aggregates across
  every pipeline run for that project. Grouping at flush time lets
  us check the budget ONCE per project per flush, then fan out the
  same decision to every queued run.
* Future Phase 3b (coalesced ``finalize_run``) wants to process N
  runs of the same project in a single transaction. Grouping here
  is the foundation for that.

Why a SortedSet, not a List?
----------------------------

* **Idempotent enqueue.** ``ZADD`` on the same member updates its
  score; a duplicate enqueue (retry, re-finalize) doesn't create
  a duplicate pipeline run.
* **Time-windowed flush.** ``ZRANGEBYSCORE 0 (now - debounce)``
  hands us exactly the runs old enough to flush. Without scores
  we'd need a separate timestamp record per run.
* **Deterministic fan-out.** Members read back in score order, so
  pipelines fire in run-completion order within a project.

Failure modes
-------------

* Redis outage → ``enqueue_pipeline_for_run`` falls back to the
  legacy direct ``apply_async`` so we never lose a pipeline trigger.
* Budget-check failure → fail OPEN (let the pipeline run); the
  budget service already does this internally.
* Beat task crash → the SortedSet retains the runs; the next tick
  drains them. No data loss.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Single SortedSet for the whole installation. Member format:
# ``<project_uuid>|<test_run_uuid>|<build_number>|<workflow_type>``.
# Pipe-separator chosen because UUIDs / build numbers don't legally
# contain it, and a single delimiter parses faster than JSON.
_DEBOUNCE_KEY = "testlookup:ai_pipeline_debounce"
_DEBOUNCE_DELIMITER = "|"

# Per-project degraded flag. Set when the cost budget blocks the
# project's pipeline; ``/health/ingestion`` reads these keys to
# surface the degraded count. 24h TTL so the flag auto-clears on
# the next day's UTC boundary even if nothing else touches it.
_DEGRADED_KEY = "testlookup:ai_pipeline_degraded:{project_id}"
_DEGRADED_TTL_SECONDS = 86_400


@dataclass
class _QueuedRun:
    project_id: str
    test_run_id: str
    build_number: str
    workflow_type: str

    @classmethod
    def parse(cls, raw: str) -> Optional["_QueuedRun"]:
        # ``redis-py`` decode_responses=True is the project default, so
        # members come back as str. Defensive: handle bytes too in case
        # a future redis client config flips it.
        s = raw.decode() if isinstance(raw, bytes) else raw
        parts = s.split(_DEBOUNCE_DELIMITER)
        if len(parts) != 4:
            return None
        return cls(
            project_id=parts[0],
            test_run_id=parts[1],
            build_number=parts[2],
            workflow_type=parts[3] or "offline",
        )

    def serialize(self) -> str:
        return _DEBOUNCE_DELIMITER.join([
            self.project_id, self.test_run_id, self.build_number,
            self.workflow_type or "offline",
        ])


async def enqueue_pipeline_for_run(
    *,
    project_id: str,
    test_run_id: str,
    build_number: str,
    workflow_type: str = "offline",
) -> str:
    """Enqueue a run for AI-pipeline processing.

    Returns the status string the caller can log: ``"debounced"`` when
    the run landed in the SortedSet, ``"direct"`` when the debouncer
    is disabled (legacy path), or ``"fallback_direct"`` when Redis was
    unreachable and we routed directly to keep the trigger alive.
    """
    if not settings.AI_PIPELINE_DEBOUNCE_ENABLED:
        await _enqueue_direct(project_id, test_run_id, build_number, workflow_type)
        return "direct"

    queued = _QueuedRun(
        project_id=project_id,
        test_run_id=test_run_id,
        build_number=build_number,
        workflow_type=workflow_type,
    )
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        # ``ZADD key score member`` — score is enqueue time. Idempotent:
        # a duplicate enqueue overwrites the score (same member key).
        await redis.zadd(_DEBOUNCE_KEY, {queued.serialize(): time.time()})
    except Exception as exc:
        logger.warning(
            "ai_pipeline_debouncer_enqueue_failed_fallback_direct",
            project_id=project_id,
            test_run_id=test_run_id,
            error=str(exc),
        )
        # Never lose a pipeline trigger because of a Redis hiccup.
        await _enqueue_direct(project_id, test_run_id, build_number, workflow_type)
        return "fallback_direct"

    logger.info(
        "ai_pipeline_debounced",
        project_id=project_id,
        test_run_id=test_run_id,
    )
    return "debounced"


async def _enqueue_direct(
    project_id: str,
    test_run_id: str,
    build_number: str,
    workflow_type: str,
    mode_override: Optional[str] = None,
) -> None:
    """Direct (non-debounced) enqueue. Used as the fallback path and
    by the beat task to fire individual pipeline runs after grouping."""
    from app.worker.tasks import run_agent_pipeline

    # The Tier 0 analysis-mode override (rules / ml / llm) is threaded
    # through ``run_agent_pipeline`` via its existing kwargs. Until the
    # task signature catches up we pass via ``headers`` (Celery metadata
    # the task body can read) so behaviour stays backward-compatible
    # when ``mode_override`` is None.
    headers = {"ai_mode_override": mode_override} if mode_override else None
    run_agent_pipeline.apply_async(
        kwargs={
            "test_run_id": test_run_id,
            "project_id": project_id,
            "build_number": build_number,
            "workflow_type": workflow_type,
        },
        queue="ai_analysis",
        priority=6,
        countdown=0,
        headers=headers,
    )


async def flush_pending(
    *,
    now: Optional[float] = None,
    window_seconds: Optional[int] = None,
    max_runs_per_flush: int = 5000,
) -> dict:
    """Drain runs older than the debounce window, group by project,
    consult the cost budget, and fan out pipeline tasks.

    Returns a small dict for the beat-task log line so operators can
    see at a glance: total drained, per-project counts, budget-blocked
    count.

    Parameters
    ----------
    now:
        Override the current time for testing. Default is wall-clock.
    window_seconds:
        Minimum age (seconds) a run must reach before it's flushed.
        Default is ``settings.AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS``
        (60s). Smaller windows reduce per-run latency but increase
        the chance of a same-project burst hitting the LLM in
        parallel; the default matches the design doc.
    max_runs_per_flush:
        Safety cap. A pathological burst that overflows the SortedSet
        shouldn't process every queued run in one tick — flush the
        oldest N and leave the rest for the next tick.
    """
    if not settings.AI_PIPELINE_DEBOUNCE_ENABLED:
        return {"drained": 0, "skipped_disabled": True}

    from app.db.redis_client import get_redis
    redis = get_redis()

    current = now if now is not None else time.time()
    window = window_seconds if window_seconds is not None else settings.AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS
    eligible_until = current - window

    try:
        # ``ZRANGEBYSCORE 0 (eligible_until LIMIT 0 max_runs_per_flush``.
        # Returns members with score in [0, eligible_until], in score-ascending
        # order so we process older runs first.
        raw_members = await redis.zrangebyscore(
            _DEBOUNCE_KEY, 0, eligible_until,
            start=0, num=max_runs_per_flush,
        )
    except Exception as exc:
        logger.warning("ai_pipeline_debouncer_flush_read_failed", error=str(exc))
        return {"drained": 0, "error": str(exc)}

    if not raw_members:
        return {"drained": 0}

    # Parse + group.
    grouped: dict[str, list[_QueuedRun]] = {}
    for raw in raw_members:
        queued = _QueuedRun.parse(raw)
        if queued is None:
            continue  # malformed — drop silently
        grouped.setdefault(queued.project_id, []).append(queued)

    # Apply the cost-budget gate per project, then fan out.
    from app.services.llm_cost_budget import check_and_apply_cap

    project_summaries: dict[str, dict] = {}
    fired_total = 0
    blocked_total = 0
    fallback_total = 0
    for project_id, runs in grouped.items():
        decision = await check_and_apply_cap(project_id)
        mode_override = decision.mode_override
        blocked = decision.block

        # Surface the degraded state via Redis so /health/ingestion
        # can show it without re-querying the DB.
        try:
            if blocked or mode_override:
                await redis.set(
                    _DEGRADED_KEY.format(project_id=project_id),
                    decision.action,
                    ex=_DEGRADED_TTL_SECONDS,
                )
        except Exception:  # pragma: no cover - degraded marker is best-effort
            pass

        if blocked:
            # Hard cap reached — skip pipeline calls entirely. The runs
            # still have aggregates persisted (TestCase rows from the
            # earlier persist_live_session), so the dashboard works;
            # only the AI-analysis layer is dropped.
            blocked_total += len(runs)
            project_summaries[project_id] = {
                "fired": 0, "blocked": len(runs),
                "rationale": decision.rationale,
            }
        else:
            # Fire one pipeline per run. ``mode_override`` (when present)
            # downgrades LLM-mode runs to ml/rules to keep the project
            # within budget; the analysis-router honours it inside the
            # task body.
            for r in runs:
                try:
                    await _enqueue_direct(
                        r.project_id, r.test_run_id, r.build_number,
                        r.workflow_type, mode_override=mode_override,
                    )
                    fired_total += 1
                    if mode_override:
                        fallback_total += 1
                except Exception as exc:
                    logger.warning(
                        "ai_pipeline_debouncer_dispatch_failed",
                        project_id=project_id,
                        test_run_id=r.test_run_id,
                        error=str(exc),
                    )
            project_summaries[project_id] = {
                "fired": len(runs), "blocked": 0,
                "mode_override": mode_override,
            }

    # Remove every successfully read member from the SortedSet — even
    # the blocked ones. A blocked run shouldn't sit in the queue for
    # the next flush; the budget cap is a daily reset, and re-firing
    # the same pipeline 4 ticks in a row produces 4 logged blocks
    # without progress.
    try:
        await redis.zrem(_DEBOUNCE_KEY, *raw_members)
    except Exception as exc:  # pragma: no cover
        logger.warning("ai_pipeline_debouncer_zrem_failed", error=str(exc))

    logger.info(
        "ai_pipeline_flush_complete",
        drained=len(raw_members),
        projects=len(grouped),
        fired=fired_total,
        blocked=blocked_total,
        fallback=fallback_total,
    )
    return {
        "drained": len(raw_members),
        "projects": len(grouped),
        "fired": fired_total,
        "blocked": blocked_total,
        "fallback": fallback_total,
        "per_project": project_summaries,
    }


async def get_degraded_project_count() -> int | None:
    """Count projects currently in the degraded LLM-budget state.

    Returns ``None`` when the count could not be read. It used to return ``0``,
    which reads as *no project is degraded* -- indistinguishable from *the
    store that knows is unreachable*, and wrong in the direction that stops an
    operator looking further.
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        count = 0
        async for _ in redis.scan_iter(
            match=_DEGRADED_KEY.format(project_id="*"), count=200,
        ):
            count += 1
        return count
    except Exception as exc:
        logger.warning("degraded_project_count_unavailable", error=str(exc))
        return None
