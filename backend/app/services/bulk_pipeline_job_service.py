"""Tracked bulk pipeline jobs (W14 AGT-12).

A bulk trigger answers with a job id; the job's REQUEST facts live in one
Redis hash (``BULK_JOB_KEY``, 24 h): who asked, what was requested / queued /
skipped / not found, the queued run ids, and its state (active / cancelled).
There is NO progress counter in Redis: progress is COUNTED from the pipeline
rows on every read (``execution_metadata->>'bulk_job_id'``), so the panel and
``/agents`` can never disagree, and a lost hash degrades to a Postgres count
(``counters_source: "postgres"``), never to zeros.

Who writes the hash: the bulk POST (create, then the final queued count) and
the cancel route. The worker only READS it (:func:`is_cancelled`); a GET never
writes it. Commit-free: nothing here adds, flushes, commits or rolls back.
Redis failures never raise: a create that fails answers ``False`` (the batch
still queues, untracked), a read that fails answers ``None``. Log lines carry
ids and counts only.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_ops_vocab import (
    BULK_CLIENT_KEY,
    BULK_CLIENT_TOKEN_TTL_SECONDS,
    BULK_FILTER_CAP,
    BULK_JOB_KEY,
    BULK_JOB_SETTLE_SECONDS,
    BULK_JOB_TTL_SECONDS,
    BULK_NOT_STARTED_NOTE,
)
from app.models.postgres import AgentPipelineRun, TestRun
from app.services.workflow_run_state import public_status

logger = structlog.get_logger("services.bulk_pipeline_job")

#: The run-list filter's rows: (run_id, project_id, build_number), newest first.
FilterRows = list[tuple[uuid.UUID, uuid.UUID, Optional[str]]]


def _redis():
    from app.db.redis_client import get_redis  # noqa: PLC0415 (patched in tests)

    return get_redis()


def job_key(job_id: str) -> str:
    return BULK_JOB_KEY.format(job_id=job_id)


def client_key(user_id: Any, token: str) -> str:
    return BULK_CLIENT_KEY.format(user_id=user_id, token=token)


# ── the filter ("re-trigger all matching") ─────────────────────────────────


async def resolve_run_filter(
    db: AsyncSession, current_user: Any, f: Any, *, cap: int = BULK_FILTER_CAP,
) -> tuple[FilterRows, int, bool]:
    """The runs ``GET /runs`` lists for the same filter: (rows, matched, truncated).

    Parsed and authorised exactly as the list route does (the same 400 / 403 /
    422 texts): ``project_id`` through ``resolve_project_scope`` FIRST, then the
    suites, the toolbar values and the releases; the predicates are the list's
    own (``runs_service.run_list_filters``) inside the caller's accessible
    projects. TWO statements: the COUNT (``matched``) and the newest ``cap``
    ids in the list's order. ``truncated`` when more matched than ``cap``.
    """
    from app.core.deps import get_accessible_project_ids, resolve_project_scope  # noqa: PLC0415
    from app.services import runs_service  # noqa: PLC0415
    from app.services.analytics_scope import (  # noqa: PLC0415
        parse_suite_filter,
        resolve_release_query_scope_list,
    )

    requested_project = str(f.project_id) if f.project_id else None
    await resolve_project_scope(db, current_user, requested_project)
    suites = parse_suite_filter(f.suite_name)
    toolbar = runs_service.parse_run_list_toolbar(f.q, f.branch, f.recommendation)
    releases = await resolve_release_query_scope_list(db, current_user, f.release_id)
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and not accessible:
        return [], 0, False
    effective_days = f.days if f.days and f.days > 0 else None
    filters = runs_service.run_list_filters(
        requested_project, accessible, f.status, releases, effective_days, suites, **toolbar,
    )
    matched = int((await db.execute(select(func.count(TestRun.id)).where(*filters))).scalar() or 0)
    if matched == 0:
        return [], 0, False
    rows = (await db.execute(
        select(TestRun.id, TestRun.project_id, TestRun.build_number)
        .where(*filters)
        .order_by(*runs_service.run_list_order_keys())
        .limit(int(cap))
    )).all()
    return [(row[0], row[1], row[2]) for row in rows], matched, matched > int(cap)


# ── the request record (Redis) ──────────────────────────────────────────────


def _iso(value: Optional[datetime]) -> str:
    return value.isoformat() if value is not None else ""


def _queued_fields(queued_run_ids: Sequence[Any], queued_per_project: Mapping[Any, int]) -> dict[str, str]:
    return {
        "queued": str(len(queued_run_ids)),
        "run_ids": json.dumps([str(run_id) for run_id in queued_run_ids]),
        "queued_per_project": json.dumps({str(pid): int(n) for pid, n in queued_per_project.items()}),
    }


async def create_job(
    *,
    job_id: str,
    requested_by: Any,
    workflow_type: str,
    source: str,
    requested: int,
    queued_rows: Sequence[Any],
    skipped: int,
    not_found: int,
    matched: Optional[int],
    truncated: bool,
    queued_per_project: Mapping[Any, int],
    now: Optional[datetime] = None,
) -> bool:
    """Write the job's request record (HSET + EXPIRE in one MULTI) BEFORE the
    fan-out, so a fast worker finds it. ``queued_rows`` are the runs about to be
    queued (their run ids are stored). False on any Redis error (untracked)."""
    queued_ids = [row[0] if isinstance(row, (tuple, list)) else row for row in queued_rows]
    mapping = {
        "requested_by": str(requested_by),
        "workflow_type": workflow_type,
        "source": source,
        "requested": str(int(requested)),
        "skipped_in_progress": str(int(skipped)),
        "not_found": str(int(not_found)),
        "matched": "" if matched is None else str(int(matched)),
        "truncated": "1" if truncated else "0",
        "created_at": _iso(now or datetime.now(timezone.utc)),
        "state": "active",
        "cancelled_at": "",
        "cancelled_by": "",
        **_queued_fields(queued_ids, queued_per_project),
    }
    try:
        async with _redis().pipeline(transaction=True) as pipe:
            pipe.hset(job_key(job_id), mapping=mapping)
            pipe.expire(job_key(job_id), BULK_JOB_TTL_SECONDS)
            await pipe.execute()
        return True
    except Exception as exc:  # noqa: BLE001 -- a cache outage is not a refusal
        logger.warning("bulk_job_create_failed", bulk_job_id=job_id, error_type=type(exc).__name__)
        return False


async def record_queued(
    job_id: str, queued_run_ids: Sequence[Any], queued_per_project: Mapping[Any, int],
) -> bool:
    """After the fan-out: the runs that actually queued (a broker error drops a
    run from the record instead of leaving it "not started" forever)."""
    try:
        await _redis().hset(job_key(job_id), mapping=_queued_fields(queued_run_ids, queued_per_project))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_job_queued_update_failed", bulk_job_id=job_id, error_type=type(exc).__name__)
        return False


async def claim_client_token(user_id: Any, token: str, job_id: str) -> Optional[str]:
    """``SET NX EX``: None when this request claimed the token (or Redis is
    down: no idempotency then, never a refusal); else the EXISTING job id."""
    try:
        redis = _redis()
        key = client_key(user_id, token)
        if await redis.set(key, job_id, nx=True, ex=BULK_CLIENT_TOKEN_TTL_SECONDS):
            return None
        existing = await redis.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_job_token_failed", error_type=type(exc).__name__)
        return None
    if isinstance(existing, bytes):
        existing = existing.decode("utf-8", errors="replace")
    return str(existing) if existing else None


def _text(raw: Mapping[Any, Any], name: str) -> str:
    value = raw.get(name, raw.get(name.encode()) if raw else None)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def _int(raw: Mapping[Any, Any], name: str) -> Optional[int]:
    value = _text(raw, name)
    try:
        return int(value) if value != "" else None
    except ValueError:
        return None


def _when(raw: Mapping[Any, Any], name: str) -> Optional[datetime]:
    value = _text(raw, name)
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _json(raw: Mapping[Any, Any], name: str, default: Any) -> Any:
    try:
        value = json.loads(_text(raw, name) or "null")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def decode_job(raw: Mapping[Any, Any]) -> dict[str, Any]:
    """The hash as typed values (counts as ints, instants as datetimes)."""
    return {
        "requested_by": _text(raw, "requested_by"),
        "workflow_type": _text(raw, "workflow_type") or "offline",
        "source": _text(raw, "source") or "selection",
        "requested": _int(raw, "requested"),
        "queued": _int(raw, "queued"),
        "skipped_in_progress": _int(raw, "skipped_in_progress"),
        "not_found": _int(raw, "not_found"),
        "matched": _int(raw, "matched"),
        "truncated": _text(raw, "truncated") == "1",
        "created_at": _when(raw, "created_at"),
        "state": _text(raw, "state") or "active",
        "cancelled_at": _when(raw, "cancelled_at"),
        "cancelled_by": _text(raw, "cancelled_by") or None,
        "run_ids": [str(r) for r in _json(raw, "run_ids", [])],
        "queued_per_project": {str(k): int(v) for k, v in _json(raw, "queued_per_project", {}).items()},
    }


async def read_job(job_id: str) -> Optional[dict[str, Any]]:
    """The job's request record, decoded; None when absent (expired / flushed) or on error."""
    try:
        raw = await _redis().hgetall(job_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_job_read_failed", bulk_job_id=job_id, error_type=type(exc).__name__)
        return None
    return decode_job(raw) if raw else None


async def mark_cancelled(job_id: str, *, by: Any, now: datetime) -> Optional[dict[str, Any]]:
    """Set the record's state to cancelled (the worker's skip reads it); the
    updated record, or None when it is gone or Redis failed."""
    try:
        redis = _redis()
        if not await redis.exists(job_key(job_id)):
            return None
        await redis.hset(job_key(job_id), mapping={
            "state": "cancelled", "cancelled_at": _iso(now), "cancelled_by": str(by),
        })
        raw = await redis.hgetall(job_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_job_cancel_failed", bulk_job_id=job_id, error_type=type(exc).__name__)
        return None
    return decode_job(raw) if raw else None


async def is_cancelled(job_id: Optional[str]) -> bool:
    """The worker's check before a task creates its pipeline; False on any error."""
    if not job_id:
        return False
    try:
        state = await _redis().hget(job_key(job_id), "state")
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_job_state_read_failed", bulk_job_id=job_id, error_type=type(exc).__name__)
        return False
    if isinstance(state, bytes):
        state = state.decode("utf-8", errors="replace")
    return bool(state == "cancelled")


# ── progress (counted from Postgres on every read) ──────────────────────────


def progress_statement(
    job_id: str, *, run_ids: Optional[Sequence[Any]], requested_by: Any, accessible: Optional[set],
):
    """ONE grouped statement over the job's pipeline rows (status, workflow type)."""
    stamp = AgentPipelineRun.execution_metadata["bulk_job_id"].as_string()
    stmt = (
        select(AgentPipelineRun.status, AgentPipelineRun.workflow_type, func.count(AgentPipelineRun.id))
        .join(TestRun, TestRun.id == AgentPipelineRun.test_run_id)
        .where(stamp == str(job_id), AgentPipelineRun.requested_by == _as_uuid(requested_by))
        .group_by(AgentPipelineRun.status, AgentPipelineRun.workflow_type)
    )
    if run_ids is not None:
        stmt = stmt.where(AgentPipelineRun.test_run_id.in_([_as_uuid(r) for r in run_ids]))
    if accessible is not None:
        stmt = stmt.where(TestRun.project_id.in_(list(accessible)))
    return stmt


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def job_progress(
    db: AsyncSession,
    job_id: str,
    *,
    run_ids: Optional[Sequence[Any]],
    requested_by: Any,
    accessible: Optional[set],
) -> dict[str, Any]:
    """``BulkJobProgress`` counts (+ ``workflow_type`` of the rows, None when there
    are none). Only rows the job's requester created, of the job's run ids when
    known, in projects the CALLER can still access (``accessible``; None = admin)."""
    if accessible is not None and not accessible:
        rows: list = []
    else:
        rows = list((await db.execute(progress_statement(
            job_id, run_ids=run_ids, requested_by=requested_by, accessible=accessible,
        ))).all())
    counts = {"in_progress": 0, "completed": 0, "passed": 0, "failed": 0}
    workflow_types: dict[str, int] = {}
    for status, workflow_type, count in rows:
        counts[public_status(status)] += int(count)
        workflow_types[str(workflow_type)] = workflow_types.get(str(workflow_type), 0) + int(count)
    started = sum(counts.values())
    return {
        "started": started,
        **counts,
        "workflow_type": max(sorted(workflow_types), key=workflow_types.__getitem__) if workflow_types else None,
    }


def job_state(
    job: Optional[Mapping[str, Any]], progress: Mapping[str, Any], *, now: datetime,
) -> tuple[str, Optional[str]]:
    """``(state, note)``. ``cancelled`` when the record says so; ``finished`` when
    nothing is in progress and every queued task started (or the job is older
    than ``BULK_JOB_SETTLE_SECONDS``: a task not started by then never starts),
    with ``BULK_NOT_STARTED_NOTE`` for the ones that never started (NOT failed);
    else ``active``. Without the record: finished iff nothing is in progress."""
    if job is None:
        return ("finished" if progress.get("in_progress", 0) == 0 else "active"), None
    if job.get("state") == "cancelled":
        return "cancelled", None
    if progress.get("in_progress", 0) > 0:
        return "active", None
    not_started = progress.get("not_started")
    if not not_started:
        return "finished", None
    created = job.get("created_at")
    if isinstance(created, datetime) and now - created >= timedelta(seconds=BULK_JOB_SETTLE_SECONDS):
        return "finished", BULK_NOT_STARTED_NOTE.format(n=not_started)
    return "active", None


def job_read(
    job_id: str, job: Optional[Mapping[str, Any]], progress: Mapping[str, Any], *, now: datetime,
) -> dict[str, Any]:
    """The ``BulkJobRead`` body. Without the record (``counters_source:
    "postgres"``) every request count is None -- unknown, never 0."""
    counts = {key: progress.get(key, 0) for key in ("started", "in_progress", "completed", "passed", "failed")}
    queued = job.get("queued") if job is not None else None
    counts["not_started"] = max(int(queued) - counts["started"], 0) if queued is not None else None
    state, note = job_state(job, counts, now=now)
    if job is None:
        return {
            "bulk_job_id": str(job_id), "workflow_type": progress.get("workflow_type") or "offline",
            "source": "selection", "state": state, "counters_source": "postgres",
            "created_at": None, "cancelled_at": None, "requested": None, "queued": None,
            "skipped_in_progress": None, "not_found": None, "matched": None, "truncated": False,
            "progress": counts, "note": note,
        }
    return {
        "bulk_job_id": str(job_id), "workflow_type": job.get("workflow_type") or "offline",
        "source": job.get("source") or "selection", "state": state, "counters_source": "redis",
        "created_at": job.get("created_at"), "cancelled_at": job.get("cancelled_at"),
        "requested": job.get("requested"), "queued": queued,
        "skipped_in_progress": job.get("skipped_in_progress"), "not_found": job.get("not_found"),
        "matched": job.get("matched"), "truncated": bool(job.get("truncated")),
        "progress": counts, "note": note,
    }


__all__ = [
    "claim_client_token", "create_job", "decode_job", "is_cancelled", "job_progress", "job_read",
    "job_state", "mark_cancelled", "progress_statement", "read_job", "record_queued", "resolve_run_filter",
]
