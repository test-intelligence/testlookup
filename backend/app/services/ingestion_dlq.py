"""Dead-letter queue for ingestion tasks (Phase 4.3).

When ``persist_live_session`` exhausts its retry budget (3 attempts
with exponential backoff, ~7 minutes total), the failure is recorded
here so operators have a single endpoint to inspect what blew up
without grepping container logs across 8 worker pods.

Storage choice: Redis List (LPUSH onto a capped LTRIM-bounded queue)
rather than a Postgres table.

Why Redis over a SQL table:

* The whole reason a task hit the DLQ is that ingestion is degraded
  — writing a DLQ row through the same PG pool that may be
  saturated defeats the purpose.
* The DLQ exists for HUMAN inspection ("what failed in the last
  hour?") not for cross-system queries. A capped Redis list with
  the last 1000 failures + 7-day TTL is the right shape.
* Migrating to a SQL ``ingestion_dlq`` table later is a small
  refactor if the operational need changes (e.g. multi-week
  retention, programmatic Jira ticket creation).

Read access via ``GET /api/v1/health/ingestion/dlq`` (Phase 4.3b —
deferred; the data is already queryable via ``LRANGE`` from any
admin shell).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Single global queue keyed by failure type. Each type's queue is
# LTRIM-bounded so a runaway failure mode can't fill Redis. The
# default cap of 1000 entries × ~1 KB each = ~1 MB; fine alongside
# everything else this module budgets for.
_DLQ_KEY = "testlookup:ingestion_dlq:{kind}"
_DLQ_MAX_ENTRIES = 1_000
_DLQ_TTL_SECONDS = 7 * 24 * 3600  # 7 days


async def record_persist_failure(
    *,
    run_id: str,
    project_id: str,
    task_id: str,
    retry_count: int,
    error: str,
) -> None:
    """Write a dead-letter record for a ``persist_live_session`` task
    that exhausted its retry budget. Best-effort: a Redis failure is
    logged but never propagates — the DLQ is observability, not the
    source of truth for the failure (that's Celery's own task state)."""
    if not settings.INGESTION_DLQ_ENABLED:
        return
    payload = {
        "kind": "persist_live_session",
        "run_id": run_id,
        "project_id": project_id,
        "task_id": task_id,
        "retry_count": retry_count,
        "error": (error or "")[:2000],  # truncate to keep Redis values bounded
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _DLQ_KEY.format(kind="persist_live_session")
        # LPUSH so newest entries appear first on LRANGE; LTRIM caps
        # the list at ``_DLQ_MAX_ENTRIES``. Both ops in a pipeline so
        # the cap is enforced atomically.
        pipe = redis.pipeline()
        pipe.lpush(key, json.dumps(payload))
        pipe.ltrim(key, 0, _DLQ_MAX_ENTRIES - 1)
        pipe.expire(key, _DLQ_TTL_SECONDS)
        await pipe.execute()
        logger.warning(
            "ingestion_dlq_record_written",
            run_id=run_id,
            project_id=project_id,
            retry_count=retry_count,
        )
    except Exception as exc:
        logger.error(
            "ingestion_dlq_write_failed",
            run_id=run_id,
            project_id=project_id,
            error=str(exc),
        )


async def list_recent_failures(kind: str = "persist_live_session", limit: int = 50) -> list[dict]:
    """Return the most-recent DLQ entries for ``kind``. Used by the
    future ``GET /api/v1/health/ingestion/dlq`` admin endpoint and by
    on-call humans poking at the queue from a shell."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _DLQ_KEY.format(kind=kind)
        raw_entries = await redis.lrange(key, 0, max(0, limit - 1))
    except Exception:
        return []

    out: list[dict] = []
    for raw in raw_entries:
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


async def get_dlq_count(kind: str = "persist_live_session") -> int:
    """Return the current DLQ depth. Used by ``/health/ingestion`` so
    the dashboard can surface "12 ingestion tasks have permanently
    failed in the last 7 days" without polling for the entries."""
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        return int(await redis.llen(_DLQ_KEY.format(kind=kind)))
    except Exception:
        return 0
