"""Dead-letter stores for ingestion and background work (Phase 4.3, re-audit M2).

Two Redis stores hold work that permanently failed:

* ``testlookup:ingestion_dlq:persist_live_session`` -- a capped LIST. When
  ``persist_live_session`` exhausts its retry budget (3 attempts with
  exponential backoff, ~7 minutes total), the failure is recorded here so
  operators have one place to inspect what blew up without grepping container
  logs across 8 worker pods.
* ``DLQ_STREAM`` (``testlookup:stream:dlq``) -- a capped STREAM. The live-event
  consumer moves an event here after ``MAX_DELIVERY_ATTEMPTS`` and then ACKs
  it, so the entry is the only surviving copy of that event. Celery tasks
  (``run_agent_pipeline``, the run-compare report) add themselves here when
  their retries run out.

Storage choice: Redis rather than a Postgres table.

* The whole reason a task hit the DLQ is that ingestion is degraded
  -- writing a DLQ row through the same PG pool that may be
  saturated defeats the purpose.
* The DLQ exists for HUMAN inspection ("what failed in the last
  hour?") not for cross-system queries. A capped Redis list with
  the last 1000 failures + 7-day TTL is the right shape.
* Migrating to a SQL ``ingestion_dlq`` table later is a small
  refactor if the operational need changes (e.g. multi-week
  retention, programmatic Jira ticket creation).

Read access (re-audit M2). Both stores were write-only: ``/health/ingestion``
counted the LIST, nothing counted the STREAM, and no API returned an entry of
either. ``GET /api/v1/admin/maintenance/dlq`` now returns both, to instance
admins only, because the entries are cross-tenant (run and project ids, error
text, sanitized event payloads). ``/health/ingestion`` reports both depths, as
counts only. A store that cannot be read raises ``DLQUnavailable`` -- never an
empty list, which would tell on-call that nothing has failed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

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

# Stream fields whose value is a JSON document (see the two writers:
# ``LiveEventStreamConsumer._move_to_dlq`` and ``worker.tasks._send_to_dlq``).
_JSON_STREAM_FIELDS = frozenset({"original_data", "kwargs"})
_STREAM_ID = re.compile(r"^[0-9]+-[0-9]+$")
_REPLAY_LOCK_TTL_SECONDS = 60

# Only task names written by ``worker.tasks._send_to_dlq`` may cross the
# operator replay boundary. A Redis writer cannot turn this endpoint into an
# arbitrary Celery dispatcher.
REPLAYABLE_CELERY_TASKS = frozenset(
    {
        "app.worker.tasks.ingest_test_run",
        "app.worker.tasks.run_agent_pipeline",
        "app.worker.tasks.generate_run_compare_report",
    }
)


class DLQUnavailable(Exception):
    """A dead-letter store could not be read.

    Deliberately an exception rather than an empty result: "we could not look"
    must never render as "nothing has permanently failed".
    """


class DLQEntryNotFound(Exception):
    """The requested stream entry does not exist."""


class DLQReplayRefused(Exception):
    """The entry is not a replayable, server-authored Celery task."""


class DLQReplayInProgress(Exception):
    """Another operator currently owns this entry's replay claim."""


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


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
    """Newest-first entries of the ``kind`` LIST, as written.

    Raises ``DLQUnavailable`` when Redis cannot be read. This used to return
    ``[]`` on any error -- the mistake ``get_dlq_count`` below was already
    fixed for -- so an outage read as "no failures". An entry that is not
    valid JSON is skipped.
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        key = _DLQ_KEY.format(kind=kind)
        raw_entries = await redis.lrange(key, 0, max(0, limit - 1))
    except Exception as exc:
        logger.warning("dlq_read_unavailable", store=kind, error=str(exc))
        raise DLQUnavailable(str(exc)) from exc

    out: list[dict] = []
    for raw in raw_entries:
        try:
            out.append(json.loads(_text(raw)))
        except Exception:
            continue
    return out


async def list_recent_stream_failures(limit: int = 50) -> list[dict]:
    """Newest-first entries of ``DLQ_STREAM``, with their JSON fields decoded.

    Two writers put different shapes here: the live-event consumer
    (``source_stream``, ``original_msg_id``, ``original_data``, ``error``,
    ``attempt_count``) and Celery tasks (``source``, ``task_name``,
    ``task_id``, ``kwargs``, ``error``). Both come back as written, plus the
    stream ``id`` and the ``failed_at`` time that id encodes. A JSON field that
    does not parse is returned as its raw text rather than dropping the entry:
    this may be the only copy of a failed event.

    Raises ``DLQUnavailable`` when Redis cannot be read.
    """
    try:
        from app.db.redis_client import get_redis
        from app.streams import DLQ_STREAM

        redis = get_redis()
        raw_entries = await redis.xrevrange(DLQ_STREAM, count=max(1, limit))
    except Exception as exc:
        logger.warning("dlq_read_unavailable", store="stream", error=str(exc))
        raise DLQUnavailable(str(exc)) from exc

    out: list[dict] = []
    for msg_id, fields in raw_entries:
        entry: dict = {"id": _text(msg_id)}
        millis = entry["id"].split("-", 1)[0]
        if millis.isdigit():
            entry["failed_at"] = datetime.fromtimestamp(
                int(millis) / 1000, tz=timezone.utc
            ).isoformat()
        for key, value in (fields or {}).items():
            name, text = _text(key), _text(value)
            if name in _JSON_STREAM_FIELDS:
                try:
                    entry[name] = json.loads(text)
                    continue
                except ValueError:
                    pass
            entry[name] = text
        out.append(entry)
    return out


async def get_dlq_count(kind: str = "persist_live_session") -> int | None:
    """Return the current DLQ depth, or ``None`` when it could not be read.

    Used by ``/health/ingestion`` so the dashboard can surface "12 ingestion
    tasks have permanently failed in the last 7 days" without polling for the
    entries.

    This returned ``0`` on any error, which on an on-call dashboard reads as
    *nothing has permanently failed* -- the most reassuring possible answer to
    give when the truth is *we could not look*. ``None`` forces the caller to
    render "unknown".
    """
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        return int(await redis.llen(_DLQ_KEY.format(kind=kind)))
    except Exception as exc:
        logger.warning("dlq_count_unavailable", kind=kind, error=str(exc))
        return None


async def get_stream_dlq_count() -> int | None:
    """Depth of ``DLQ_STREAM``, or ``None`` when it could not be read."""
    try:
        from app.db.redis_client import get_redis
        from app.streams import DLQ_STREAM

        return int(await get_redis().xlen(DLQ_STREAM))
    except Exception as exc:
        logger.warning("dlq_count_unavailable", kind="stream", error=str(exc))
        return None


async def replay_stream_failure(entry_id: str) -> dict[str, Any]:
    """Dispatch one allowlisted Celery dead letter, then remove its entry.

    The short Redis claim prevents two operator requests from dispatching the
    same task concurrently. The entry is deleted only after Celery accepts the
    replacement task; broker failures leave it available for another attempt.
    Live-stream events use a different recovery protocol and are refused.
    """
    if not _STREAM_ID.fullmatch(entry_id):
        raise DLQEntryNotFound(entry_id)

    from app.db.redis_client import get_redis
    from app.streams import DLQ_STREAM

    redis = get_redis()
    lock_key = f"testlookup:dlq:replay:{entry_id}"
    try:
        claimed = await redis.set(
            lock_key, "1", ex=_REPLAY_LOCK_TTL_SECONDS, nx=True
        )
    except Exception as exc:
        raise DLQUnavailable(str(exc)) from exc
    if not claimed:
        raise DLQReplayInProgress(entry_id)

    dispatched = False
    entry_deleted = False
    try:
        try:
            rows = await redis.xrange(
                DLQ_STREAM, min=entry_id, max=entry_id, count=1
            )
        except Exception as exc:
            raise DLQUnavailable(str(exc)) from exc
        if not rows:
            raise DLQEntryNotFound(entry_id)

        _msg_id, fields = rows[0]
        decoded = {_text(key): _text(value) for key, value in (fields or {}).items()}
        task_name = decoded.get("task_name", "")
        if decoded.get("source") != "celery" or task_name not in REPLAYABLE_CELERY_TASKS:
            raise DLQReplayRefused(entry_id)
        try:
            kwargs = json.loads(decoded.get("kwargs", ""))
        except (TypeError, ValueError) as exc:
            raise DLQReplayRefused(entry_id) from exc
        if not isinstance(kwargs, dict):
            raise DLQReplayRefused(entry_id)

        try:
            from app.worker.celery_app import celery_app

            replacement = celery_app.send_task(task_name, kwargs=kwargs)
        except Exception as exc:
            raise DLQUnavailable(str(exc)) from exc
        dispatched = True
        try:
            await redis.xdel(DLQ_STREAM, entry_id)
            entry_deleted = True
        except Exception as exc:
            # Dispatch has already been accepted. Keep the claim until its TTL
            # rather than permit an immediate duplicate while cleanup is down.
            raise DLQUnavailable(str(exc)) from exc
        return {
            "accepted": True,
            "entry_id": entry_id,
            "task_name": task_name,
            "task_id": str(replacement.id),
        }
    finally:
        if not dispatched or entry_deleted:
            try:
                await redis.delete(lock_key)
            except Exception:
                pass
        # When dispatch succeeded but XDEL failed, retain the claim until its
        # TTL. Immediate release would invite a duplicate dispatch.
