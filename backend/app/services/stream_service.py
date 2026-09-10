from __future__ import annotations

import hashlib
import inspect
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import LaunchStatus, LiveSession, Project, TestRun
from app.models.schemas import (
    ActiveSessionsResponse,
    LiveEventBatchResponse,
    LiveSessionResponse,
    LiveSessionState,
    LiveStreamIngestRequest,
    LiveStreamIngestResponse,
)
from app.services.ingestion_sanitization import (
    sanitize_test_result_payload,
    validate_live_identifier,
)
from app.services.run_status import terminal_run_status
from app.services.run_tombstone_service import run_is_tombstoned

logger = logging.getLogger(__name__)


class _StubSkipped(Exception):
    """Internal: the companion TestRun stub was deliberately not written.

    Raised inside the stub's SAVEPOINT so the deliberate skip unwinds by
    the same path as a failed write, and caught separately so it is never
    reported as an error."""

# Throttle: warn at most once per process when the live buffer cap is disabled.
_buffer_cap_disabled_warned = False


def _warn_buffer_cap_disabled() -> None:
    global _buffer_cap_disabled_warned
    _buffer_cap_disabled_warned = True
    logger.warning(
        "LIVE_BUFFER_MAX_EVENTS_PER_RUN <= 0 — per-run event buffer is "
        "UNBOUNDED; Redis backpressure for live runs is disabled."
    )


SESSION_TTL = 86_400
SESSION_TOKEN_KEY = "live:session:{token}"

_LIVE_LEGACY_BUFFER_TTL_SECONDS = 90_000
_DEDUPE_CLOSED_FIELD = "__closed__"
_CLOSED_DEDUPE_RETENTION_SECONDS = 15 * 24 * 60 * 60
_CLOSE_ADMISSION_LUA = r"""
local kind = redis.call('TYPE', KEYS[1])['ok']
if kind ~= 'none' and kind ~= 'hash' then return {'wrongtype', kind} end
for i = 2, 8 do
  local set_kind = redis.call('TYPE', KEYS[i])['ok']
  if set_kind ~= 'none' and set_kind ~= 'set' then return {'wrongtype', KEYS[i]} end
end
if redis.call('HEXISTS', KEYS[1], '__admitting__') == 1 then
  return {'busy', 'admission in progress'}
end
redis.call('HSET', KEYS[1], '__gate__', 'closing', ARGV[1], ARGV[2])
local snapshot = cjson.encode({
  passed = redis.call('SCARD', KEYS[3]), failed = redis.call('SCARD', KEYS[4]),
  skipped = redis.call('SCARD', KEYS[5]), broken = redis.call('SCARD', KEYS[6]),
  unknown = redis.call('SCARD', KEYS[7]),
  events_received = redis.call('SCARD', KEYS[8]),
  total = redis.call('SCARD', KEYS[3]) + redis.call('SCARD', KEYS[4])
    + redis.call('SCARD', KEYS[5]) + redis.call('SCARD', KEYS[6])
    + redis.call('SCARD', KEYS[7])
})
return {'closing', snapshot, tostring(redis.call('SCARD', KEYS[2]))}
"""
_FINALIZE_CLOSE_LUA = r"""
local kind = redis.call('TYPE', KEYS[1])['ok']
if kind ~= 'none' and kind ~= 'hash' then return {'wrongtype', kind} end
for i = 2, 8 do
  local set_kind = redis.call('TYPE', KEYS[i])['ok']
  if set_kind ~= 'none' and set_kind ~= 'set' then return {'wrongtype', KEYS[i]} end
end
if tonumber(ARGV[1]) == nil or tonumber(ARGV[1]) <= 0 then return {'invalid', 'retention'} end
redis.call('HSET', KEYS[1], '__gate__', 'closed')
if redis.call('SCARD', KEYS[2]) == 0 then
  for i = 1, 8 do redis.call('EXPIRE', KEYS[i], ARGV[1]) end
end
return {'closed', tostring(redis.call('SCARD', KEYS[2]))}
"""


class LiveEvidenceCapacityError(HTTPException):
    """The complete batch cannot fit in the run's durable Redis evidence."""

    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": "1"},
        )


class LiveBatchConflictError(HTTPException):
    """A caller reused a batch identity for different event content."""

    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class LiveSessionClosedError(LiveBatchConflictError):
    """The batch names a session that has closed: nothing more is admitted."""

    def __init__(self, detail: str = "live session is closed"):
        super().__init__(detail)


def _canonical_event_json(value) -> str:
    """Stable JSON used by both legacy batch and event identity derivation."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _legacy_batch_id(session_id: str, run_id: str, events: list[dict]) -> str:
    """Derive retry-stable identity for clients that predate ``batch_id``."""
    preimage = f"{session_id}\0{run_id}\0{_canonical_event_json(events)}"
    return "legacy-" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _stable_event_id(session_id: str, batch_id: str, index: int) -> str:
    preimage = f"{session_id}\0{batch_id}\0{index}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _batch_digest(events: list[dict]) -> str:
    return hashlib.sha256(_canonical_event_json(events).encode("utf-8")).hexdigest()


def _dedupe_field(session_id: str, batch_id: str) -> str:
    preimage = f"{session_id}\0{batch_id}".encode("utf-8")
    return "batch:" + hashlib.sha256(preimage).hexdigest()


def _validated_batch_id(value: object) -> str:
    batch_id = str(value)
    if not batch_id or len(batch_id) > 255:
        raise ValueError("batch_id must contain 1-255 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in batch_id):
        raise ValueError("batch_id contains control characters")
    return batch_id


# One server-side transaction owns admission. A rejected batch changes no
# stream, list, counter, TTL or dedupe state; a retry of an accepted batch
# returns the original count without applying any side effect twice.
_ADMIT_LIVE_BATCH_LUA = r"""
local expected_types = {
  'stream', 'hash', 'hash', 'list', 'stream',
  'set', 'set', 'set', 'set', 'set', 'set', 'set'
}
for i = 1, 12 do
  local actual = redis.call('TYPE', KEYS[i])['ok']
  if actual ~= 'none' and actual ~= expected_types[i] then
    return {'wrongtype', KEYS[i]}
  end
end

local dedupe_field = ARGV[16]
local event_count = tonumber(ARGV[13])
local capacity = tonumber(ARGV[3])
local ok_events, events = pcall(cjson.decode, ARGV[19])
local ok_ids, event_ids = pcall(cjson.decode, ARGV[20])
local ok_wire, wire_events = pcall(cjson.decode, ARGV[21])
if not ok_events or not ok_ids or not ok_wire
   or type(events) ~= 'table' or type(event_ids) ~= 'table'
   or type(wire_events) ~= 'table' or #events ~= event_count
   or #event_ids ~= event_count or #wire_events ~= event_count then
  return {'invalid', 'batch encoding'}
end
for i = 1, 5 do
  if tonumber(ARGV[7 + i]) == nil then
    return {'invalid', 'counter'}
  end
end

local existing = redis.call('HGET', KEYS[3], dedupe_field)
local stream_id = nil
local recovering = false
if existing then
  local ok, receipt = pcall(cjson.decode, existing)
  if not ok then
    return {'corrupt', 'dedupe receipt'}
  end
  if receipt['digest'] ~= ARGV[15] or tonumber(receipt['count']) ~= tonumber(ARGV[13]) then
    return {'collision', tostring(receipt['count'] or '')}
  end
  if receipt['state'] == 'accepted' or receipt['state'] == 'projected' then
    if redis.call('HGET', KEYS[3], '__admitting__') == dedupe_field then
      redis.call('HDEL', KEYS[3], '__admitting__')
    end
    return {'duplicate', tostring(receipt['count']), tostring(receipt['stream_id'])}
  end
  if receipt['state'] ~= 'staged'
     or redis.call('HGET', KEYS[3], '__admitting__') ~= dedupe_field then
    return {'corrupt', 'admission receipt'}
  end
  stream_id = tostring(receipt['stream_id'])
  recovering = true
end

-- Duplicate lookup deliberately precedes the close fence: a retry of the
-- accepted closing batch must still converge after the session is closed.
local gate = redis.call('HGET', KEYS[3], '__gate__') or 'open'
-- ARGV[24] is PostgreSQL's answer: the session has completed. The fence
-- above expires with the closed ledger, 15 days after the run drains, and a
-- missing fence reads as 'open'. This answer does not expire, so a finished
-- session never admits a new batch (code review and QA of re-audit N14).
if ARGV[24] == '1' then
  gate = 'closed'
end
if not recovering and gate ~= 'open' then
  return {'closed', '0'}
end

local pending_total = redis.call('SCARD', KEYS[6])
if not recovering and capacity > 0 and pending_total + event_count > capacity then
  return {'capacity', tostring(pending_total)}
end

if not stream_id then
  if redis.call('HEXISTS', KEYS[3], '__admitting__') == 1 then
    return {'busy', 'another batch admission is recovering'}
  end
  local now = redis.call('TIME')
  local milliseconds = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
  local sequence = 0
  local tail = redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)
  if #tail == 1 then
    local last_ms, last_sequence = string.match(tail[1][1], '^(%d+)%-(%d+)$')
    last_ms = tonumber(last_ms)
    last_sequence = tonumber(last_sequence)
    if milliseconds < last_ms then milliseconds = last_ms end
    if milliseconds == last_ms then sequence = last_sequence + 1 end
  end
  stream_id = tostring(milliseconds) .. '-' .. tostring(sequence)
  local admitting = cjson.encode({
    digest = ARGV[15], count = event_count,
    state = 'staged', stream_id = stream_id,
    batch_id = ARGV[14], session_id = ARGV[1], run_id = ARGV[2],
    events_json = ARGV[19], event_ids_json = ARGV[20],
    passed = tonumber(ARGV[8]), failed = tonumber(ARGV[9]),
    skipped = tonumber(ARGV[10]), broken = tonumber(ARGV[11]),
    unknown = tonumber(ARGV[12]), countable = tonumber(ARGV[6]),
    last_event_at = ARGV[5], current_test = ARGV[7]
  })
  redis.call(
    'HSET', KEYS[3], dedupe_field, admitting,
    '__admitting__', dedupe_field, '__gate__', 'open'
  )
end
local exact = redis.call('XRANGE', KEYS[1], stream_id, stream_id, 'COUNT', 1)
if #exact == 0 then
  redis.call(
    'XADD', KEYS[1], stream_id,
    'batch_id', ARGV[14],
    'batch_digest', ARGV[15],
    'event_count', ARGV[13],
    'session_id', ARGV[1],
    'run_id', ARGV[2],
    'events_json', ARGV[19],
    'event_ids_json', ARGV[20],
    'trim_legacy', '0'
  )
end

for i = 1, #event_ids do
  local event_id = event_ids[i]
  local event = events[i]
  redis.call('SADD', KEYS[6], event_id)
  if event['event_type'] ~= 'live_heartbeat' then
    redis.call('SADD', KEYS[12], event_id)
  end
  if event['event_type'] == 'test_result' then
    local outcome = string.upper(tostring(event['status'] or 'UNKNOWN'))
    if outcome == 'PASSED' then redis.call('SADD', KEYS[7], event_id)
    elseif outcome == 'FAILED' then redis.call('SADD', KEYS[8], event_id)
    elseif outcome == 'SKIPPED' then redis.call('SADD', KEYS[9], event_id)
    elseif outcome == 'BROKEN' then redis.call('SADD', KEYS[10], event_id)
    else redis.call('SADD', KEYS[11], event_id) end
  end
end
redis.call(
  'HSET', KEYS[2],
  'passed', redis.call('SCARD', KEYS[7]),
  'failed', redis.call('SCARD', KEYS[8]),
  'skipped', redis.call('SCARD', KEYS[9]),
  'broken', redis.call('SCARD', KEYS[10]),
  'unknown', redis.call('SCARD', KEYS[11]),
  'events_received', redis.call('SCARD', KEYS[12]),
  'last_event_at', ARGV[5], 'current_test', ARGV[7]
)
redis.call('EXPIRE', KEYS[2], ARGV[23])
local receipt = cjson.encode({
  digest = ARGV[15], count = event_count, state = 'accepted', stream_id = stream_id
})
redis.call('HSET', KEYS[3], dedupe_field, receipt)
redis.call('HDEL', KEYS[3], '__admitting__')
return {'accepted', tostring(event_count), stream_id}
"""

_RECONCILE_STAGED_BATCH_LUA = r"""
local expected_types = {
  'stream', 'hash', 'hash', 'list', 'stream',
  'set', 'set', 'set', 'set', 'set', 'set', 'set'
}
for i = 1, 12 do
  local actual = redis.call('TYPE', KEYS[i])['ok']
  if actual ~= 'none' and actual ~= expected_types[i] then
    return {'wrongtype', KEYS[i]}
  end
end
local field = redis.call('HGET', KEYS[3], '__admitting__')
if not field then return {'idle', '0'} end
local encoded = redis.call('HGET', KEYS[3], field)
local ok, receipt = pcall(cjson.decode, encoded or '')
if not ok then return {'corrupt', '0'} end
if receipt['state'] == 'accepted' or receipt['state'] == 'projected' then
  redis.call('HDEL', KEYS[3], '__admitting__')
  return {'reconciled', tostring(receipt['count'])}
end
if receipt['state'] ~= 'staged' then return {'corrupt', '0'} end
local events_ok, events = pcall(cjson.decode, receipt['events_json'] or '')
local ids_ok, ids = pcall(cjson.decode, receipt['event_ids_json'] or '')
if not events_ok or not ids_ok or #events ~= tonumber(receipt['count'])
   or #ids ~= tonumber(receipt['count']) then return {'corrupt', '0'} end
local exact = redis.call('XRANGE', KEYS[1], receipt['stream_id'], receipt['stream_id'], 'COUNT', 1)
if #exact == 0 then
  redis.call(
    'XADD', KEYS[1], receipt['stream_id'],
    'batch_id', receipt['batch_id'], 'batch_digest', receipt['digest'],
    'event_count', tostring(receipt['count']), 'session_id', receipt['session_id'],
    'run_id', receipt['run_id'], 'events_json', receipt['events_json'],
    'event_ids_json', receipt['event_ids_json'], 'trim_legacy', '0'
  )
end
for i = 1, #ids do
  redis.call('SADD', KEYS[6], ids[i])
  if events[i]['event_type'] ~= 'live_heartbeat' then redis.call('SADD', KEYS[12], ids[i]) end
  if events[i]['event_type'] == 'test_result' then
    local outcome = string.upper(tostring(events[i]['status'] or 'UNKNOWN'))
    if outcome == 'PASSED' then redis.call('SADD', KEYS[7], ids[i])
    elseif outcome == 'FAILED' then redis.call('SADD', KEYS[8], ids[i])
    elseif outcome == 'SKIPPED' then redis.call('SADD', KEYS[9], ids[i])
    elseif outcome == 'BROKEN' then redis.call('SADD', KEYS[10], ids[i])
    else redis.call('SADD', KEYS[11], ids[i]) end
  end
end
redis.call(
  'HSET', KEYS[2], 'passed', redis.call('SCARD', KEYS[7]),
  'failed', redis.call('SCARD', KEYS[8]), 'skipped', redis.call('SCARD', KEYS[9]),
  'broken', redis.call('SCARD', KEYS[10]), 'unknown', redis.call('SCARD', KEYS[11]),
  'events_received', redis.call('SCARD', KEYS[12]),
  'last_event_at', receipt['last_event_at'], 'current_test', receipt['current_test']
)
redis.call('EXPIRE', KEYS[2], 86400)
redis.call('HSET', KEYS[3], field, cjson.encode({
  digest = receipt['digest'], count = receipt['count'], state = 'accepted',
  stream_id = receipt['stream_id']
}))
redis.call('HDEL', KEYS[3], '__admitting__')
return {'reconciled', tostring(receipt['count'])}
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def canonical_test_run_uuid(run_id: str) -> uuid.UUID:
    """Map a live-session ``run_id`` (which may be a UUID *or* an arbitrary
    user-supplied slug like ``local-abc12345``) to the canonical ``TestRun.id``
    UUID we persist under.

    SDKs frequently default to slug-style ids (the Python SDK's
    ``f"local-{uuid.uuid4().hex[:8]}"`` is the canonical example). Without
    this helper, callers had three choices — and the three call sites that
    needed the mapping (``upsert_test_run``, ``persist_live_session``, and
    the LiveSessionState builders) drifted: the first two derived a UUID5
    via ``uuid.uuid5(NAMESPACE_DNS, run_id)`` while the live state response
    returned the raw slug, so the frontend's ``/runs/<run_id>`` link 422'd
    for any non-UUID slug. Centralising here keeps them in lockstep.
    """
    try:
        return uuid.UUID(run_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_DNS, run_id)


def get_redis():
    from app.db.redis_client import get_redis as _get_redis

    return _get_redis()


async def publish_event_batch(session_id: str, run_id: str, events):
    from app.streams.producer import publish_event_batch as _publish_event_batch

    return await _publish_event_batch(session_id=session_id, run_id=run_id, events=events)


async def resolve_project(db: AsyncSession, identifier: str) -> Project:
    """Resolve a project identifier (UUID *or* name) to a Project row.

    Used by the streaming-session endpoint so SDK users can configure either
    ``testlookup.project=<uuid>`` or ``testlookup.project=<name>`` without
    knowing which one the server expects. UUID is tried first because it's
    the unambiguous case; the name fallback is a case-insensitive exact
    match (no fuzzy search — surprising matches would be worse than 404).
    """
    try:
        as_uuid = uuid.UUID(str(identifier))
    except (ValueError, AttributeError, TypeError):
        as_uuid = None

    if as_uuid is not None:
        project = await db.get(Project, as_uuid)
        if project:
            return project

    # Fall through to case-insensitive name lookup.
    result = await db.execute(
        select(Project).where(func.lower(Project.name) == str(identifier).lower())
    )
    project = result.scalar_one_or_none()
    if project:
        return project

    raise HTTPException(status_code=404, detail="Project not found")


_CI_CONTEXT_FIELDS = ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url")


def _with_ci_context(metadata: dict, payload) -> dict:
    """Fold the payload's CI-context fields (US-4.3) into the session's
    extra_metadata under ``ci_context`` — LiveSession has no dedicated
    columns; ``upsert_test_run`` reads this back at persist time and stamps
    the TestRun. Explicit ``metadata['ci_context']`` keys from the caller
    win over the typed fields."""
    ci = {
        f: getattr(payload, f, None)
        for f in _CI_CONTEXT_FIELDS
        if getattr(payload, f, None) is not None
    }
    # US-8.1 — stash a caller-supplied commit range (air-gapped attribution)
    # alongside the CI context. LiveSession has no column for it; upsert_test_run
    # reads it back at persist time and persists the range for the run.
    supplied_range = getattr(payload, "commit_range", None)
    if not ci and not supplied_range:
        return metadata
    merged = dict(metadata)
    if ci:
        merged["ci_context"] = {**ci, **(merged.get("ci_context") or {})}
    if supplied_range and not merged.get("commit_range"):
        # Two accepted wire shapes: a bare commit list, or the
        # boundary-carrying ``{base, head, commits}`` object. Stash whichever
        # arrived as plain JSON — the object form is what lets ``base_commit``
        # survive to the persisted row.
        if isinstance(supplied_range, dict):
            merged["commit_range"] = supplied_range
        elif hasattr(supplied_range, "model_dump"):
            merged["commit_range"] = supplied_range.model_dump()
        else:
            merged["commit_range"] = [
                c.model_dump() if hasattr(c, "model_dump") else c for c in supplied_range
            ]
    return merged


async def create_session(
    db: AsyncSession,
    payload,
    bound_project_id: Optional[uuid.UUID] = None,
) -> LiveSessionResponse:
    """Stage a new LiveSession and return the response shape. Handler commits.

    Redis session-token registration and in-memory run state happen *after*
    the handler's commit so an aborted transaction never leaves a dangling
    session token that authenticates a run which doesn't exist in Postgres.

    ``bound_project_id`` is the UUID a project-scoped API key restricts this
    call to (or ``None`` for JWT / user-scoped keys). It's checked against the
    *resolved* project, not the raw identifier, so passing a project name with
    a UUID-bound key still validates correctly.
    """
    project = await resolve_project(db, payload.project_id)
    if bound_project_id is not None and project.id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

    session_id = str(uuid.uuid4())
    run_id = session_id
    session_token = secrets.token_urlsafe(32)

    # Use the *resolved* project's real UUID for every downstream write. The
    # original payload.project_id may have been a name; project.id is always a
    # UUID so the LiveSession FK and the Redis state stay consistent.
    project_uuid = project.id

    session = LiveSession(
        id=uuid.UUID(session_id),
        project_id=project_uuid,
        run_id=run_id,
        client_name=payload.client_name,
        machine_id=payload.machine_id,
        build_number=payload.build_number,
        framework=payload.framework,
        branch=payload.branch,
        commit_hash=payload.commit_hash,
        session_token_hash=hash_token(session_token),
        total_tests=payload.total_tests or 0,
        status="active",
        release_name=payload.release_name or None,
        launch_name=getattr(payload, "launch_name", None) or None,
        suite_name=getattr(payload, "suite_name", None) or None,
        started_at=datetime.now(timezone.utc),
        extra_metadata=_with_ci_context(payload.metadata or {}, payload),
    )
    db.add(session)
    await db.flush()

    # Companion TestRun stub so /runs (and the Pipeline-signal KPI on
    # it) include this run from the moment it starts — not 30s later
    # when the Phase 4.5 drainer fires its first non-empty drain, and
    # not at session-complete when persist_live_session runs. Without
    # this, a short live session that finishes inside the drainer
    # window never lands in test_runs at all, and the Pipeline-signal
    # count stays frozen on historical builds. (Bug 2026-05-19.)
    #
    # ``upsert_test_run`` and the drainer both use ``id``-keyed
    # upsert semantics, so this stub is a forward-compatible write —
    # the persist path on session-complete updates these aggregates
    # to the final values and flips ``status`` to its terminal value.
    started_at = session.started_at
    # SAVEPOINT-wrap the stub write so a duplicate-key race with a
    # concurrent ingest/replay can't poison the outer transaction —
    # ``db.begin_nested()`` issues SAVEPOINT and SQLAlchemy auto-rolls
    # back to it on exception. The outer LiveSession write stays
    # committed regardless. Services don't own ``db.rollback()`` on
    # injected sessions; the SAVEPOINT is the right primitive here.
    #
    # A tombstoned id means an operator deleted this run: writing the stub
    # would put the row back with none of its data behind it. Only the STUB is
    # skipped — create_session still returns normally and the LiveSession is
    # still written, because refusing the session outright would drop live
    # results on the floor.
    stub_would_resurrect = await run_is_tombstoned(db, session_id)
    if stub_would_resurrect:
        logger.info("live_stub_skipped_tombstoned_run run_id=%s", session_id)
    try:
        async with db.begin_nested():
            if stub_would_resurrect:
                raise _StubSkipped()
            stub = TestRun(
                id=uuid.UUID(session_id),
                project_id=project_uuid,
                build_number=payload.build_number or run_id[:8],
                trigger_source="live_stream",
                ingestion_source="live",
                status=LaunchStatus.IN_PROGRESS,
                total_tests=payload.total_tests or 0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                broken_tests=0,
                primary_suite_name=getattr(payload, "suite_name", None) or None,
                suite_names=[getattr(payload, "suite_name", None)] if getattr(payload, "suite_name", None) else None,
                branch=payload.branch,
                commit_hash=payload.commit_hash,
                ci_provider=getattr(payload, "ci_provider", None) or None,
                ci_repo=getattr(payload, "ci_repo", None) or None,
                pr_number=getattr(payload, "pr_number", None),
                ci_actor=getattr(payload, "ci_actor", None) or None,
                ci_run_url=getattr(payload, "ci_run_url", None) or None,
                start_time=started_at,
                end_time=started_at,
            )
            db.add(stub)
            await db.flush()
    except _StubSkipped:
        # Deliberate: the id is tombstoned. Not a failure, and it must not be
        # logged as one — the SAVEPOINT rollback is exactly the behaviour we
        # want, so this reuses the existing unwind rather than adding a second
        # code path around the write.
        pass
    except Exception as exc:
        # stdlib ``logging`` doesn't take arbitrary kwargs the way
        # structlog does (the rest of this module is on stdlib logging
        # via ``logger = logging.getLogger(__name__)``). Format the
        # fields into the message instead.
        logger.warning(
            "live_session_test_run_stub_skipped run_id=%s error=%s",
            run_id, exc,
        )

    redis = get_redis()
    await redis.setex(SESSION_TOKEN_KEY.format(token=session_token), SESSION_TTL, session_id)

    from app.streams.live_run_state import RedisLiveRunState

    await RedisLiveRunState.start(
        run_id=run_id,
        project_id=str(project_uuid),
        build_number=payload.build_number or session_id,
        total_tests=payload.total_tests or 0,
        launch_name=getattr(payload, "launch_name", None) or None,
        suite_name=getattr(payload, "suite_name", None) or None,
    )

    logger.info(
        "Live session created: session=%s run=%s project=%s framework=%s",
        session_id,
        run_id,
        project_uuid,
        payload.framework,
    )
    return LiveSessionResponse(
        session_id=session_id,
        session_token=session_token,
        run_id=run_id,
        project_id=str(project_uuid),
        expires_in=SESSION_TTL,
        created_at=session.started_at,
    )


async def get_session(
    db: AsyncSession,
    session_id: str,
    bound_project_id: Optional[uuid.UUID] = None,
) -> dict:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if bound_project_id is not None and session.project_id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

    from app.streams.live_run_state import RedisLiveRunState

    live_stats = await RedisLiveRunState.get(str(session.id)) or {}
    return {
        "session_id": str(session.id),
        "run_id": session.run_id,
        "project_id": str(session.project_id),
        "client_name": session.client_name,
        "machine_id": session.machine_id,
        "build_number": session.build_number,
        "framework": session.framework,
        "branch": session.branch,
        "status": session.status,
        "total_tests": session.total_tests,
        # Prefer the live counter while the run is in flight — the column is
        # only written when the session closes, so reading it alone reports 0
        # for the entire duration of the run it is describing.
        "events_received": int(
            live_stats.get("events_received") or session.events_received or 0
        ),
        "started_at": session.started_at.isoformat(),
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "live_stats": live_stats,
    }


async def finalize_closed_session_redis(session_id: str) -> None:
    """Mark a committed close durable and enable bounded Redis retention.

    Callers must invoke this only after their PostgreSQL commit succeeds. The
    Lua transition is idempotent, so a retry after a lost response is safe.
    """
    internal_run_id = str(uuid.UUID(session_id))
    from app.streams import LIVE_BATCH_DEDUP_KEY

    redis = get_redis()
    ledger_key = LIVE_BATCH_DEDUP_KEY.format(run_id=internal_run_id)
    ledger_sets = [
        f"{ledger_key}:{suffix}"
        for suffix in (
            "pending", "passed", "failed", "skipped",
            "broken", "unknown", "received",
        )
    ]
    result = await redis.eval(
        _FINALIZE_CLOSE_LUA,
        8,
        ledger_key,
        *ledger_sets,
        _CLOSED_DEDUPE_RETENTION_SECONDS,
    )
    outcome = result[0].decode() if isinstance(result[0], bytes) else result[0]
    if outcome != "closed":
        raise RuntimeError(f"live close finalization failed: {outcome}")


async def close_session(
    db: AsyncSession,
    session_id: str,
    bound_project_id: Optional[uuid.UUID] = None,
) -> None:
    try:
        uid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc

    session = await db.get(LiveSession, uid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Project-scoped API key: the session must belong to the bound project.
    # JWT and user-scoped API keys pass ``bound_project_id=None`` and skip
    # this check (membership at the route level was previously enforced by
    # ``require_live_session_access``; the route now relies on this service
    # check so X-API-Key callers don't get a spurious 401).
    if bound_project_id is not None and session.project_id != bound_project_id:
        raise HTTPException(
            status_code=403,
            detail="This API key is restricted to a different project",
        )

    # Idempotency guard: a previous close_session for this session has already
    # cleared Redis state and queued the persistence task. Returning early here
    # closes the race where two concurrent close requests would each clear state
    # and enqueue persist_live_session, producing duplicate log lines and —
    # depending on Celery worker timing — duplicate DB upserts.
    if session.status == "completed":
        logger.info(f"close_session: already completed, skipping session_id={session_id}")
        return

    from app.streams.live_run_state import RedisLiveRunState

    internal_run_id = str(session.id)
    redis = get_redis()
    from app.streams import (
        LIVE_BATCH_DEDUP_KEY,
        LIVE_EVENTS_STREAM,
        LIVE_EVIDENCE_STREAM_KEY,
        LIVE_STATE_KEY,
        LIVE_TESTCASES_KEY,
    )

    ledger_key = LIVE_BATCH_DEDUP_KEY.format(run_id=internal_run_id)
    ledger_sets = [
        f"{ledger_key}:{suffix}"
        for suffix in ("pending", "passed", "failed", "skipped", "broken", "unknown", "received")
    ]

    # This Hash is TTL-less durable admission state. HSET is atomic relative
    # to the batch-admission Lua script: a new batch loses to the close fence,
    # while a byte-identical retry is resolved from its existing receipt first.
    close_result = await redis.eval(
        _CLOSE_ADMISSION_LUA,
        8,
        ledger_key,
        *ledger_sets,
        _DEDUPE_CLOSED_FIELD,
        datetime.now(timezone.utc).isoformat(),
    )
    close_status = close_result[0]
    if isinstance(close_status, bytes):
        close_status = close_status.decode()
    if close_status == "busy":
        reconcile_keys = [
            LIVE_EVIDENCE_STREAM_KEY.format(run_id=internal_run_id),
            LIVE_STATE_KEY.format(run_id=internal_run_id), ledger_key,
            LIVE_TESTCASES_KEY.format(run_id=internal_run_id), LIVE_EVENTS_STREAM,
            *ledger_sets,
        ]
        reconciled = await redis.eval(
            _RECONCILE_STAGED_BATCH_LUA, 12, *reconcile_keys
        )
        reconcile_status = reconciled[0].decode() if isinstance(reconciled[0], bytes) else reconciled[0]
        if reconcile_status != "reconciled":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="live batch admission is still in progress; retry close",
                headers={"Retry-After": "1"},
            )
        close_result = await redis.eval(
            _CLOSE_ADMISSION_LUA, 8, ledger_key, *ledger_sets,
            _DEDUPE_CLOSED_FIELD, datetime.now(timezone.utc).isoformat(),
        )
        close_status = close_result[0].decode() if isinstance(close_result[0], bytes) else close_result[0]
    if close_status != "closing":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live admission close fence is unavailable",
            headers={"Retry-After": "1"},
        )
    state = await RedisLiveRunState.complete(internal_run_id)
    ledger_snapshot = json.loads(
        close_result[1].decode()
        if isinstance(close_result[1], bytes)
        else close_result[1]
    )
    state = {**(state or {}), **ledger_snapshot}
    now = datetime.now(timezone.utc)
    session.status = "completed"
    session.completed_at = now
    if state:
        session.extra_metadata = {**(session.extra_metadata or {}), "final_state": state}
        # Persist the live counter before the Redis hash expires, so the column
        # still means something once the run is only readable from Postgres.
        session.events_received = int(state.get("events_received") or 0)

    await upsert_test_run(db, session, state or {})

    # ── 15-day durable event archive ───────────────────────────────────────
    # The Redis buffer that holds raw SDK events for a live run has a
    # 25-hour TTL. ``persist_live_session`` normally drains it minutes after
    # close_session fires, but a worker crash / transient error can leave
    # ``test_cases`` rows missing once the TTL lapses — and the user reports
    # "Recover from buffer" failing on day 2+ of a run they want to revisit.
    # Copy the events to ``TestRun.event_archive`` *before* queuing persist
    # so the Run Intelligence page stays recoverable for 15 days from this
    # moment regardless of the Redis TTL. Best-effort: a failure here must
    # not block session close.
    try:
        from app.streams import LIVE_TESTCASES_KEY

        from app.models.postgres import LiveEventReceipt
        from app.services.live_session_drainer import _decode_stream_entries
        from app.streams import LIVE_EVIDENCE_STREAM_KEY

        list_key = LIVE_TESTCASES_KEY.format(run_id=internal_run_id)
        stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=internal_run_id)
        decoded: list = []
        seen_event_ids: set[str] = set()

        # Entries already projected and XDEL'd remain recoverable from their
        # PostgreSQL receipts. Pending batch manifests are added below.
        try:
            receipt_result = await db.execute(
                    select(LiveEventReceipt.event_id, LiveEventReceipt.payload)
                    .where(LiveEventReceipt.run_id == session.id)
                    .order_by(LiveEventReceipt.projected_at, LiveEventReceipt.event_index)
                )
            receipt_rows = receipt_result.all()
            if inspect.isawaitable(receipt_rows):
                receipt_rows = await receipt_rows
            for event_id, payload in receipt_rows:
                seen_event_ids.add(str(event_id))
                decoded.append(sanitize_test_result_payload(payload))
        except Exception as receipt_error:
            logger.warning(
                "live_event_archive_receipt_read_failed session_id=%s error=%s",
                session_id,
                receipt_error,
            )

        if hasattr(redis, "xrange"):
            batch_rows = await redis.xrange(stream_key, min="-", max="+")
            for item in _decode_stream_entries(
                [(str(stream_id), fields) for stream_id, fields in batch_rows]
            ):
                if item["event_type"] != "test_result" or item["event_id"] in seen_event_ids:
                    continue
                seen_event_ids.add(item["event_id"])
                decoded.append(item["payload"])

        raw_events = await redis.lrange(list_key, 0, -1)
        for raw in raw_events:
            try:
                payload = sanitize_test_result_payload(json.loads(raw))
                event_id = str(payload.pop("_event_id", ""))
                if event_id and event_id in seen_event_ids:
                    continue
                if event_id:
                    seen_event_ids.add(event_id)
                decoded.append(payload)
            except Exception:
                continue
        if decoded:
            run_uuid = session.id
            tr = (
                await db.execute(select(TestRun).where(TestRun.id == run_uuid))
            ).scalar_one_or_none()
            if tr is not None:
                tr.event_archive = decoded
                tr.event_archive_at = now
                # NOTE: this module uses stdlib ``logging`` (see line 26).
                # Stdlib's Logger doesn't accept structlog-style ``key=value``
                # kwargs — passing them raises ``TypeError: Logger._log()
                # got an unexpected keyword argument 'session_id'`` which
                # then propagates as a 500 from the wrapping handler.
                # Use stdlib-format f-strings (the prevailing style in this
                # module) so the call works regardless of whether the
                # exception path or the success path fires.
                logger.info(
                    f"live_event_archive_written run_id={session.run_id} "
                    f"event_count={len(decoded)}"
                )
    except Exception as arc_err:
        # Archive failures are non-fatal — persist_live_session reads from
        # Redis first, so as long as the buffer is fresh recovery still
        # works. The user only loses the long-tail (>25h) recovery path.
        logger.warning(
            f"live_event_archive_failed session_id={session_id}: {arc_err}"
        )

    # Release linking — explicit session.release_name wins; otherwise fall
    # back to the project's default release (migration 0077). Wrapped in a
    # broad try/except so a release-linking error never blocks session close.
    try:
        from app.services.release_linker import link_run_or_default

        await link_run_or_default(
            db=db,
            project_id=session.project_id,
            release_name=session.release_name,
            # run_id is a slug for live sessions (e.g. "local-abc123"); the
            # TestRun row is persisted under canonical_test_run_uuid(run_id),
            # so the release link must target the SAME uuid. Using
            # uuid.UUID(session.run_id) raised ValueError on every slug run,
            # silently skipping release linking via the broad except below.
            test_run_id=session.id,
            # Live sessions are the one path that carries a real execution
            # time: started_at is stamped when the session opened, not at
            # ingest. So the as-of lookup is genuinely accurate here — a long
            # session that spans a rotation is attributed to the release that
            # was underway when it started, not the one that came after.
            #
            # getattr, not attribute access: this whole block sits inside a
            # broad ``except`` that logs and continues, so an AttributeError
            # here would not fail loudly — it would silently skip release
            # linking for every live run, which is exactly the regression
            # ``test_release_link_canonical_uuid`` exists to catch. Degrading
            # to None (= "the currently active release") is the honest
            # fallback; dropping the link is not.
            executed_at=getattr(session, "started_at", None),
        )
    except Exception as rel_err:
        logger.warning(
            f"live_session_release_link_failed session_id={session_id}: {rel_err}"
        )

    # Stage-only: the handler commits the LiveSession close, TestRun, release
    # link, and persistence intent atomically. The scheduled relay owns broker
    # publication, so a broker outage cannot turn a successful close into lost
    # work. ``persist_live_session`` invokes finalize_run, which stages the AI
    # and notification work after the test rows are durable.
    from app.services.run_downstream_outbox import stage_live_persist_operation

    canonical_run_uuid = session.id
    await stage_live_persist_operation(
        db,
        canonical_run_id=canonical_run_uuid,
        session=session,
        final_state=state or {},
    )
    logger.info(
        f"staged_persist_live_session session_id={session_id} "
        f"canonical_run_id={canonical_run_uuid}"
    )


async def _resolve_project_id_for_run(run_id: str) -> Optional[str]:
    """Helper used by ``_persist_event_batch`` to attribute the batch's
    test-event count to the right project for the high-volume detector.
    Best-effort: returns None on lookup failure and the detector simply
    skips this batch (the rolling counter is observability, not auth)."""
    try:
        from app.db.postgres import AsyncSessionLocal
        run_uuid = canonical_test_run_uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(TestRun, run_uuid)
            return str(run.project_id) if run else None
    except Exception:
        return None


async def _persist_event_batch(
    session_id: str,
    run_id: str,
    events,
    *,
    batch_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_closed: bool = False,
) -> int:
    """Atomically admit a complete live batch into run-isolated evidence.

    The Lua transaction makes capacity, deduplication, durable append, the
    rolling legacy LIST write and aggregate updates one decision. Writers do
    not trim either evidence buffer: only the drainer may remove committed
    evidence. ``batch_id`` is supplied by current SDKs; older SDK payloads get
    a deterministic content identity so a byte-equivalent retry converges.

    ``session_closed`` says PostgreSQL has completed the session. The batch is
    then admitted only as a retry of one the session already accepted, whatever
    the Redis close fence says: that fence expires.
    """
    from app.streams import (
        LIVE_BATCH_DEDUP_KEY,
        LIVE_EVENTS_STREAM,
        LIVE_EVIDENCE_STREAM_KEY,
        LIVE_STATE_KEY,
        LIVE_STREAM_MAXLEN,
        LIVE_TESTCASES_KEY,
    )

    session_id = validate_live_identifier("session_id", session_id)
    run_id = validate_live_identifier("run_id", run_id)

    normalized_events: list[dict] = []
    for event in events:
        event_dict = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        normalized_events.append(sanitize_test_result_payload(event_dict))

    redis = get_redis()
    resolved_batch_id = _validated_batch_id(
        batch_id or _legacy_batch_id(session_id, run_id, normalized_events)
    )
    evidence_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
    state_key = LIVE_STATE_KEY.format(run_id=run_id)
    dedup_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
    counter_map = {
        "PASSED": "passed",
        "FAILED": "failed",
        "SKIPPED": "skipped",
        "BROKEN": "broken",
    }
    now = datetime.now(timezone.utc).isoformat()
    last_test_name = ""
    test_event_count = 0
    countable_events = 0
    counters = {name: 0 for name in (*counter_map.values(), "unknown")}
    event_ids: list[str] = []
    wire_events: list[dict[str, str]] = []
    for index, event_dict in enumerate(normalized_events):
        event_id = _stable_event_id(session_id, resolved_batch_id, index)
        event_ids.append(event_id)
        # ``live_heartbeat`` is a keepalive that only exists to bump
        # last_event_at for the idle reaper. Counting it would inflate
        # events_received with idle noise, and a heartbeat-only batch must
        # touch no counter at all — see test_live_heartbeat_ingest.
        if event_dict.get("event_type") != "live_heartbeat":
            countable_events += 1
        if event_dict.get("event_type") == "test_result":
            test_event_count += 1
            legacy_entry = _canonical_event_json({
                "test_name":     event_dict.get("test_name", ""),
                "status":        event_dict.get("status", "UNKNOWN"),
                "duration_ms":   event_dict.get("duration_ms", 0),
                "suite_name":    event_dict.get("suite_name"),
                "class_name":    event_dict.get("class_name"),
                "error_message": event_dict.get("error_message"),
                "stack_trace":   event_dict.get("stack_trace"),
                "tags":          event_dict.get("tags"),
                "timestamp_ms":  event_dict.get("timestamp_ms"),
                "_event_id":     event_id,
                "_batch_id":     resolved_batch_id,
                "_event_index":  index,
            })
            status_upper = (event_dict.get("status") or "UNKNOWN").upper()
            counter_field = counter_map.get(status_upper, "unknown")
            counters[counter_field] += 1
            last_test_name = event_dict.get("test_name") or last_test_name
        else:
            legacy_entry = ""
        event_type = event_dict.get("event_type", "test_result")
        payload = _canonical_event_json({**event_dict, "run_id": run_id})
        wire_events.append({
            "event_type": str(event_type),
            "payload": payload,
            "legacy_entry": legacy_entry,
        })

    buffer_cap = settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN
    if buffer_cap <= 0 and not _buffer_cap_disabled_warned:
        _warn_buffer_cap_disabled()

    args = [
        session_id,
        run_id,
        str(buffer_cap),
        str(LIVE_STREAM_MAXLEN),
        now,
        str(countable_events),
        last_test_name,
        str(counters["passed"]),
        str(counters["failed"]),
        str(counters["skipped"]),
        str(counters["broken"]),
        str(counters["unknown"]),
        str(len(normalized_events)),
        resolved_batch_id,
        _batch_digest(normalized_events),
        _dedupe_field(session_id, resolved_batch_id),
        _DEDUPE_CLOSED_FIELD,
        "",
        _canonical_event_json(
            [{**event, "run_id": run_id} for event in normalized_events]
        ),
        _canonical_event_json(event_ids),
        _canonical_event_json(wire_events),
        str(_LIVE_LEGACY_BUFFER_TTL_SECONDS),
        str(86_400),
        # ARGV[24]: the session is no longer active in PostgreSQL.
        "1" if session_closed else "0",
    ]
    redis_keys = [
        evidence_key,
        state_key,
        dedup_key,
        list_key,
        LIVE_EVENTS_STREAM,
        f"{dedup_key}:pending",
        f"{dedup_key}:passed",
        f"{dedup_key}:failed",
        f"{dedup_key}:skipped",
        f"{dedup_key}:broken",
        f"{dedup_key}:unknown",
        f"{dedup_key}:received",
    ]
    response = await redis.eval(_ADMIT_LIVE_BATCH_LUA, 12, *redis_keys, *args)
    initial_outcome = (
        response[0].decode() if isinstance(response[0], bytes) else response[0]
    )
    if initial_outcome == "busy":
        reconciled = await redis.eval(
            _RECONCILE_STAGED_BATCH_LUA, 12, *redis_keys
        )
        reconcile_status = (
            reconciled[0].decode()
            if isinstance(reconciled[0], bytes)
            else reconciled[0]
        )
        if reconcile_status not in {"reconciled", "idle"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="staged live admission could not be reconciled",
                headers={"Retry-After": "1"},
            )
        response = await redis.eval(_ADMIT_LIVE_BATCH_LUA, 12, *redis_keys, *args)
    outcome = response[0]
    if isinstance(outcome, bytes):
        outcome = outcome.decode()
    value = response[1]
    if isinstance(value, bytes):
        value = value.decode()
    if outcome == "capacity":
        raise LiveEvidenceCapacityError(
            f"run evidence at {value}/{buffer_cap}; retry after persistence drains"
        )
    if outcome == "collision":
        raise LiveBatchConflictError(
            f"batch_id {resolved_batch_id!r} was already used with different content"
        )
    if outcome == "closed":
        raise LiveSessionClosedError()
    if outcome != "accepted" and outcome != "duplicate":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="live evidence storage is unavailable",
            headers={"Retry-After": "1"},
        )
    accepted = int(value)

    # Live counters and websocket fan-out are rebuildable views. Keep them
    # outside the durable admission state machine so a command-boundary error
    # can never create a second evidence record or inflate capacity. A lost
    # view update may make the in-flight UI briefly stale; PostgreSQL projection
    # and close-time receipt recovery remain authoritative.
    if outcome == "accepted" and hasattr(redis, "pipeline"):
        try:
            pipe = redis.pipeline(transaction=False)
            for wire_event in wire_events:
                pipe.xadd(
                    LIVE_EVENTS_STREAM,
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "event_type": wire_event["event_type"],
                        "payload": wire_event["payload"],
                    },
                    maxlen=LIVE_STREAM_MAXLEN,
                    approximate=True,
                )
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "live_derived_view_update_failed run_id=%s error=%s", run_id, exc,
            )

    # Phase 4.1 — feed the high-volume detector with the test_result event
    # count from this batch (counted in the loop above — no second model_dump
    # pass). Project_id is looked up lazily (one cached DB read per run) and
    # never blocks the ingest path — ``record_test_events`` swallows its errors.
    if outcome == "accepted" and test_event_count > 0:
        try:
            from app.services.high_volume_detector import record_test_events
            # A caller that knows the project passes it (re-audit N14). The
            # lookup is a database read per batch -- and /ws/events sends one
            # event per batch -- and it finds nothing for an API-key session,
            # whose TestRun only exists once the session closes, so those runs
            # never reached the detector at all.
            detector_project = project_id or await _resolve_project_id_for_run(run_id)
            if detector_project:
                await record_test_events(detector_project, test_event_count)
        except Exception as exc:
            logger.warning(
                "high_volume_record_failed run_id=%s error=%s", run_id, exc,
            )

    return accepted


async def resolve_project_id_for_session(
    session_id: str, x_session_token: str,
) -> Optional[uuid.UUID]:
    """Look up the project_id for a live session, given its session-token.

    Used by the ``/stream/events/batch`` admission gate so the per-project
    rate-limit charge lands on the right bucket. Returns ``None`` when
    the session token isn't recognised — the caller treats that as
    "skip the rate-limit charge", and ``ingest_event_batch`` will then
    raise its own 401 a few lines later. We don't want to surface the
    token failure twice or burn a rate-limit token on an invalid auth.
    """
    redis = get_redis()
    try:
        stored = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    except Exception:
        return None
    if not stored or not secrets.compare_digest(stored, session_id):
        return None
    # Look up the project_id on the LiveSession row. The cheaper path
    # would be to cache (token → project_id) in Redis at session-create
    # time, but that's a tier-2 optimisation; the per-request DB cost
    # here is one indexed PK lookup.
    from app.db.postgres import AsyncSessionLocal

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    async with AsyncSessionLocal() as db:
        session = await db.get(LiveSession, sid)
        return session.project_id if session else None


async def ingest_event_batch(batch, x_session_token: str) -> LiveEventBatchResponse:
    try:
        validate_live_identifier("session_id", batch.session_id)
        validate_live_identifier("run_id", batch.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    redis = get_redis()
    stored_session_id = await redis.get(SESSION_TOKEN_KEY.format(token=x_session_token))
    stored_session_id = (
        stored_session_id.decode()
        if isinstance(stored_session_id, bytes)
        else stored_session_id
    )
    if not stored_session_id or not secrets.compare_digest(stored_session_id, batch.session_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        )

    from app.db.postgres import AsyncSessionLocal

    try:
        session_uuid = uuid.UUID(batch.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id format") from exc
    async with AsyncSessionLocal() as db:
        session = await db.get(LiveSession, session_uuid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.run_id != batch.run_id:
        raise LiveBatchConflictError("run_id does not belong to this live session")

    accepted = await _persist_event_batch(
        session_id=batch.session_id,
        run_id=str(session.id),
        events=batch.events,
        batch_id=getattr(batch, "batch_id", None),
    )

    await redis.expire(SESSION_TOKEN_KEY.format(token=x_session_token), SESSION_TTL)
    return LiveEventBatchResponse(accepted=accepted, run_id=batch.run_id, session_id=batch.session_id)


def _starts_a_run(events) -> bool:
    """Whether a batch opens a run: it carries a ``run_start`` event."""
    return any(
        (event.model_dump() if hasattr(event, "model_dump") else dict(event)).get("event_type")
        == "run_start"
        for event in events
    )


async def ingest_via_api_key(
    db: AsyncSession,
    project_id: uuid.UUID,
    api_key_name: str,
    request: LiveStreamIngestRequest,
) -> LiveStreamIngestResponse:
    """Ingest a batch of live events authenticated by an API key.

    On the first call for a given (project_id, run_id) pair, auto-creates a
    LiveSession populated from ``request.meta`` (with sensible fallbacks).
    Subsequent calls reuse it while it is active; a session that has completed
    is handled below. The release record is
    auto-created when ``meta.release_name`` is set so live runs participate
    in release tracking the same way the legacy /sessions flow does.

    The handler is responsible for committing the DB transaction after this
    call so the session row, Redis token, and run-state hash all come into
    being atomically.
    """
    try:
        validate_live_identifier("run_id", request.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # The most recent session for (project_id, run_id), whatever its status:
    #   1. none       -> create one (the happy path);
    #   2. active     -> reuse it (the run's later batches);
    #   3. completed  -> that run is over. A batch that starts a run -- one
    #      carrying a ``run_start`` event, which /ws/events producers send and
    #      the bundled SDKs never do -- begins a NEW run under the same id, in
    #      a new session. Any other batch is refused with 409, unless it is a
    #      retry of a batch this session already accepted, which converges on
    #      that batch's receipt.
    #
    # Decided here, from PostgreSQL. Case 3 used to go to the admission script
    # like case 2, whose only refusal was the Redis close fence -- and that
    # fence expires with the run's closed ledger, 15 days after the run
    # drains. From then on a late batch, or a nightly job reusing its run id,
    # was admitted into the finished session with 202 and never persisted
    # (code review and QA of re-audit N14).
    existing = (
        await db.execute(
            select(LiveSession)
            .where(
                LiveSession.project_id == project_id,
                LiveSession.run_id == request.run_id,
            )
            .order_by(LiveSession.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None and existing.status != "active":
        if not _starts_a_run(request.events):
            try:
                accepted = await _persist_event_batch(
                    session_id=str(existing.id),
                    run_id=str(existing.id),
                    events=request.events,
                    batch_id=getattr(request, "batch_id", None),
                    project_id=str(project_id),
                    session_closed=True,
                )
            except LiveSessionClosedError as exc:
                raise LiveSessionClosedError(
                    f"live session is closed: run '{request.run_id}' has already "
                    "completed. Use a new run_id, or begin a new run under this "
                    "one with a run_start event."
                ) from exc
            return LiveStreamIngestResponse(
                accepted=accepted,
                run_id=request.run_id,
                session_id=str(existing.id),
                created_session=False,
            )
        existing = None

    meta = request.meta
    created_session = False
    if existing is None:
        session_uuid = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)
        session = LiveSession(
            id=session_uuid,
            project_id=project_id,
            run_id=request.run_id,
            client_name=api_key_name,
            machine_id=(meta.machine_id if meta else None),
            build_number=(meta.build_number if meta else None) or request.run_id,
            framework=(meta.framework if meta else None),
            branch=(meta.branch if meta else None),
            commit_hash=(meta.commit_hash if meta else None),
            session_token_hash=hash_token(session_token),
            total_tests=(meta.total_tests if meta else None) or 0,
            status="active",
            release_name=(meta.release_name.strip() if meta and meta.release_name else None),
            launch_name=(meta.launch_name.strip() if meta and meta.launch_name else None),
            started_at=datetime.now(timezone.utc),
            extra_metadata=(meta.metadata if meta else None) or {},
        )
        # SAVEPOINT-wrap the insert: a concurrent first-batch for the same
        # (project_id, run_id) hits the partial unique index
        # ``ix_live_sessions_active_run`` (active sessions only). Without this
        # guard the loser's flush raises IntegrityError, which 500s the SDK's
        # first batch instead of degrading to "reuse the winning session".
        # ``begin_nested`` unwinds only the failed insert; the outer
        # transaction stays usable for the re-select + event persist below.
        raced = False
        try:
            async with db.begin_nested():
                db.add(session)
                await db.flush()
        except IntegrityError:
            raced = True
            session = (
                await db.execute(
                    select(LiveSession)
                    .where(
                        LiveSession.project_id == project_id,
                        LiveSession.run_id == request.run_id,
                    )
                    .order_by(LiveSession.started_at.desc())
                    .limit(1)
                )
            ).scalar_one()
            logger.info(
                "live_session_create_race_reusing_winner run_id=%s project=%s",
                request.run_id, project_id,
            )

        if not raced:
            # Only the runner that actually created the row registers the
            # Redis token / run-state and auto-creates the release — the
            # race winner already did all of this for its own create.
            redis = get_redis()
            await redis.setex(SESSION_TOKEN_KEY.format(token=session_token), SESSION_TTL, str(session_uuid))

            from app.streams.live_run_state import RedisLiveRunState

            await RedisLiveRunState.start(
                run_id=str(session_uuid),
                project_id=str(project_id),
                build_number=(meta.build_number if meta else None) or request.run_id,
                total_tests=(meta.total_tests if meta else None) or 0,
                launch_name=(meta.launch_name.strip() if meta and meta.launch_name else None),
            )
            from app.streams import LIVE_STATE_KEY

            await redis.hset(
                LIVE_STATE_KEY.format(run_id=session_uuid),
                "display_run_id",
                request.run_id,
            )

            # Auto-create the release record so it shows up in release tracking
            # immediately. The actual run→release link is wired up at session close.
            if session.release_name:
                try:
                    from app.services.release_linker import resolve_or_create_release

                    await resolve_or_create_release(db, project_id, session.release_name)
                except Exception as exc:  # pragma: no cover - non-fatal, log only
                    logger.warning(
                        "live_session_release_autocreate_failed run_id=%s release=%s: %s",
                        request.run_id, session.release_name, exc,
                    )

            created_session = True
            logger.info(
                "Live session auto-created via API key: session=%s run=%s project=%s",
                session_uuid, request.run_id, project_id,
            )
    else:
        session = existing

    accepted = await _persist_event_batch(
        session_id=str(session.id),
        run_id=str(session.id),
        events=request.events,
        batch_id=getattr(request, "batch_id", None),
        project_id=str(project_id),
    )

    # Detect a run_complete event and finalize the session in the same handler.
    # This is what causes the TestRun row to be created (via upsert_test_run
    # inside close_session). Without it the session stays "active" forever and
    # nothing shows up in the Runs / Overview / Coverage / Failures / Trends
    # pages — those all read from TestRun, not the live Redis state.
    has_run_complete = any(
        (
            event.model_dump() if hasattr(event, "model_dump") else dict(event)
        ).get("event_type") == "run_complete"
        for event in request.events
    )
    if has_run_complete and session.status == "active":
        # Pass the API key's bound project so close_session re-asserts scope
        # (defense-in-depth parity with the JWT path), not just the row lookup.
        await close_session(db, str(session.id), bound_project_id=project_id)

    return LiveStreamIngestResponse(
        accepted=accepted,
        run_id=request.run_id,
        session_id=str(session.id),
        created_session=created_session,
    )


def build_live_session_state(payload: dict) -> LiveSessionState:
    raw_run_id = payload.get("run_id", "")
    display_run_id = payload.get("display_run_id") or raw_run_id
    return LiveSessionState(
        run_id=display_run_id,
        test_run_id=str(canonical_test_run_uuid(raw_run_id)) if raw_run_id else None,
        project_id=payload.get("project_id", ""),
        build_number=payload.get("build_number", ""),
        status=payload.get("status", "running"),
        total=int(payload.get("total", 0)),
        passed=int(payload.get("passed", 0)),
        failed=int(payload.get("failed", 0)),
        skipped=int(payload.get("skipped", 0)),
        broken=int(payload.get("broken", 0)),
        pass_rate=float(payload.get("pass_rate", 0.0)),
        current_test=payload.get("current_test") or None,
        started_at=payload.get("started_at"),
        last_event_at=payload.get("last_event_at"),
        client_name=payload.get("client_name"),
        completed_at=payload.get("completed_at"),
        release_name=payload.get("release_name"),
        launch_name=payload.get("launch_name"),
        suite_name=payload.get("suite_name"),
    )


def build_completed_session_state(session) -> LiveSessionState:
    final_state = (session.extra_metadata or {}).get("final_state", {})
    return LiveSessionState(
        run_id=session.run_id,
        test_run_id=str(session.id),
        project_id=str(session.project_id),
        build_number=session.build_number or "",
        status="completed",
        total=int(final_state.get("total", session.total_tests or 0)),
        passed=int(final_state.get("passed", 0)),
        failed=int(final_state.get("failed", 0)),
        skipped=int(final_state.get("skipped", 0)),
        broken=int(final_state.get("broken", 0)),
        pass_rate=float(final_state.get("pass_rate", 0.0)),
        current_test=None,
        started_at=session.started_at.isoformat() if session.started_at else None,
        last_event_at=session.completed_at.isoformat() if session.completed_at else None,
        client_name=session.client_name,
        completed_at=session.completed_at.isoformat() if session.completed_at else None,
        release_name=session.release_name or None,
        launch_name=session.launch_name or None,
        suite_name=getattr(session, "suite_name", None) or None,
    )


def build_test_run_fallback_state(run) -> LiveSessionState:
    return LiveSessionState(
        run_id=str(run.id),
        test_run_id=str(run.id),
        project_id=str(run.project_id),
        build_number=run.build_number or "",
        status="completed",
        total=run.total_tests or 0,
        passed=run.passed_tests or 0,
        failed=run.failed_tests or 0,
        skipped=run.skipped_tests or 0,
        broken=run.broken_tests or 0,
        pass_rate=float(run.pass_rate or 0.0),
        current_test=None,
        started_at=run.start_time.isoformat() if run.start_time else None,
        last_event_at=run.end_time.isoformat() if run.end_time else None,
        client_name=None,
        completed_at=run.end_time.isoformat() if run.end_time else None,
        suite_name=getattr(run, "primary_suite_name", None) or None,
    )


async def list_active_sessions(
    db: AsyncSession,
    project_id: Optional[str] = None,
    allowed_project_ids: Optional[set[uuid.UUID]] = None,
    suite_name: Optional[str] = None,
    days: int = 7,
) -> ActiveSessionsResponse:
    """
    List active + recent live sessions, enforcing tenant isolation.

    - ``project_id`` (when set) narrows the result to that single project.
    - ``allowed_project_ids`` (when set) constrains the result to the caller's
      accessible project set — used for non-admin callers without a pinned
      project. A value of ``None`` means no constraint (ADMIN or a project_id
      that has already been verified).
    - ``days`` controls the cutoff for *completed* sessions/runs joined onto
      the always-current active set. 1 = last 24 hours; 0 = no cutoff.
    """
    from app.streams.live_run_state import RedisLiveRunState

    suite_key = (suite_name or "").strip().lower()

    # Soft-deleted projects are excluded for every caller. The two filters
    # below are conditional by design (a pinned project, or a non-admin's
    # membership set), so an ADMIN with no project pinned matched neither and
    # saw sessions from projects that no longer exist. Measured live: of 9
    # entries, 4 belonged to two deleted probe projects.
    #
    # Eighth surface in this family (#535 runs, #538 dashboard, #539 analytics,
    # #541 ROI, #547 trends, #549 defect KPI, #550 releases). Applied to the
    # Redis set and both DB queries below, because this endpoint unions three
    # sources and filtering only one would leave the other two leaking.
    live_project_ids = {
        str(pid)
        for pid in (
            await db.execute(select(Project.id).where(Project.is_active.is_(True)))
        ).scalars().all()
    }

    all_active = await RedisLiveRunState.get_all_active()
    all_active = [s for s in all_active if s.get("project_id") in live_project_ids]
    if project_id:
        all_active = [session for session in all_active if session.get("project_id") == project_id]
    elif allowed_project_ids is not None:
        allowed_str = {str(pid) for pid in allowed_project_ids}
        all_active = [s for s in all_active if s.get("project_id") in allowed_str]
    if suite_key:
        all_active = [
            session
            for session in all_active
            if (session.get("suite_name") or "").strip().lower() == suite_key
        ]

    # Dedup on the CANONICAL run UUID, not the raw id.
    #
    # Redis keys a live run by whatever the SDK supplied — frequently a slug
    # like ``local-abc12345`` — while the DB rows carry the UUID persisted for
    # it. Comparing the two forms directly never matches, so one logical run
    # was listed twice. Measured live after the scoping fix, the Live page
    # showed exactly two rows and both were the same run:
    #
    #     sdk-probe-1 -> sdk-probe-1                            (Redis, slug)
    #     sdk-probe-1 -> bd337e00-38ae-50ee-b2e5-0f21077a711b   (DB, UUID)
    #
    # ``canonical_test_run_uuid`` is the module's existing mapping for exactly
    # this; its docstring already records three earlier call sites that drifted
    # apart the same way. This is the *opposite* failure to the older
    # build_number dedup bug: two runs may legitimately share a build number,
    # but one run must never carry two identities.
    active_run_ids = {
        canonical_test_run_uuid(str(session.get("run_id")))
        for session in all_active
        if session.get("run_id")
    }
    active_sessions = [build_live_session_state(session) for session in all_active]

    # ``days=0`` disables the cutoff so the caller sees every completed session
    # the limit allows (still capped to 50 below).
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    )
    stmt = (
        select(LiveSession)
        .where(
            LiveSession.status == "completed",
            # Same life-cycle exclusion as the Redis set above.
            LiveSession.project_id.in_(
                select(Project.id).where(Project.is_active.is_(True))
            ),
        )
        .order_by(LiveSession.completed_at.desc())
        .limit(50)
    )
    if cutoff is not None:
        stmt = stmt.where(LiveSession.completed_at >= cutoff)
    if project_id:
        try:
            stmt = stmt.where(LiveSession.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        if not allowed_project_ids:
            return ActiveSessionsResponse(sessions=[], count=0)
        stmt = stmt.where(LiveSession.project_id.in_(allowed_project_ids))
    if suite_key:
        stmt = stmt.where(func.lower(func.trim(LiveSession.suite_name)) == suite_key)

    db_sessions = (await db.execute(stmt)).scalars().all()
    seen_run_ids = set(active_run_ids)
    completed_sessions = []
    for session in db_sessions:
        canonical = session.id
        if canonical in seen_run_ids:
            continue
        seen_run_ids.add(canonical)
        completed_sessions.append(build_completed_session_state(session))

    tr_stmt = (
        select(TestRun)
        .where(
            TestRun.trigger_source == "live_stream",
            # Third source of this union — the same exclusion applies.
            TestRun.project_id.in_(
                select(Project.id).where(Project.is_active.is_(True))
            ),
        )
        .order_by(TestRun.start_time.desc())
        .limit(50)
    )
    if cutoff is not None:
        tr_stmt = tr_stmt.where(TestRun.start_time >= cutoff)
    if project_id:
        try:
            tr_stmt = tr_stmt.where(TestRun.project_id == uuid.UUID(project_id))
        except ValueError:
            pass
    elif allowed_project_ids is not None:
        tr_stmt = tr_stmt.where(TestRun.project_id.in_(allowed_project_ids))
    if suite_key:
        tr_stmt = tr_stmt.where(func.lower(func.trim(TestRun.primary_suite_name)) == suite_key)

    tr_runs = (await db.execute(tr_stmt)).scalars().all()
    for run in tr_runs:
        if run.id in seen_run_ids:
            continue
        seen_run_ids.add(run.id)
        completed_sessions.append(build_test_run_fallback_state(run))

    sessions = active_sessions + completed_sessions

    # Stamp the per-(project, suite) run sequence on every session so the
    # /live UI can render "Run #N" instead of the opaque SDK-supplied
    # build_number. Resolves via the canonical ``test_run_id`` — both
    # active sessions (Phase 4.5 drain pre-creates the TestRun row in
    # IN_PROGRESS state) and completed ones (TestRun already exists)
    # are covered. Bulk-fetched once for the full page payload.
    from app.services.runs_service import fetch_run_seq_map
    seq_ids: list[uuid.UUID] = []
    for s in sessions:
        if s.test_run_id:
            try:
                seq_ids.append(uuid.UUID(s.test_run_id))
            except ValueError:
                # ``run_id`` slugs that don't round-trip through UUID
                # are pre-Phase-4.5 legacy rows; their run_seq stays None.
                continue
    if seq_ids:
        seq_map = await fetch_run_seq_map(db, seq_ids)
        for s in sessions:
            if s.test_run_id and s.test_run_id in seq_map:
                s.run_seq = seq_map[s.test_run_id]

    return ActiveSessionsResponse(sessions=sessions, count=len(sessions))


async def upsert_test_run(db: AsyncSession, session: LiveSession, state: dict) -> None:
    run_uuid = session.id

    passed = int(state.get("passed", 0))
    failed = int(state.get("failed", 0))
    skipped = int(state.get("skipped", 0))
    broken = int(state.get("broken", 0))
    # Results whose status the server could not interpret. Included in the
    # total fallback so they cannot be erased from the run's own arithmetic.
    unknown = int(state.get("unknown", 0))
    total = int(state.get("total", session.total_tests or 0))
    # Fallback: if total wasn't tracked, derive it from component counts
    total = total or (passed + failed + skipped + broken + unknown)
    # Pass rate EXCLUDES skipped from the denominator (passed / passed+failed+broken),
    # consistent with live_consumer and ingestion. Skips are neither pass nor fail.
    executed = passed + failed + broken
    pass_rate = round(passed / executed * 100, 2) if executed > 0 else None
    # A closed run that executed nothing (empty session / all-skipped) grades as
    # STOPPED, not PASSED — mirrors ingestion._update_run_aggregates via the
    # shared helper. Runs at close_session only; the drainer creates the
    # in-progress row as IN_PROGRESS, so this never mislabels a live run.
    run_status = terminal_run_status(executed, failed, broken, unknown)
    now = datetime.now(timezone.utc)

    # Run-level suite identifier supplied by the SDK on session create. We
    # stamp it on the TestRun immediately so every UI page that joins the run
    # shows a single suite label without waiting for per-event aggregation
    # in _update_run_aggregates.
    suite_label = getattr(session, "suite_name", None) or None

    # CI context (US-4.3): stashed in extra_metadata["ci_context"] at session
    # create — stamp onto the TestRun (fill-if-null; never overwrite).
    ci_context = (getattr(session, "extra_metadata", None) or {}).get("ci_context") or {}

    run = (await db.execute(select(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if run is None and await run_is_tombstoned(db, run_uuid):
        # Deliberately deleted. Re-creating it here would return a run whose
        # events, objects and archive are already gone — an operator was told
        # this run no longer exists.
        logger.info("live_persist_skipped_tombstoned_run run_id=%s", run_uuid)
        return None
    if run is None:
        run = TestRun(
            id=run_uuid,
            project_id=session.project_id,
            build_number=session.build_number or str(session.id)[:8],
            trigger_source="live_stream",
            ingestion_source="live",
            branch=session.branch or None,
            commit_hash=session.commit_hash or None,
            ci_provider=ci_context.get("ci_provider"),
            ci_repo=ci_context.get("ci_repo"),
            pr_number=ci_context.get("pr_number"),
            ci_actor=ci_context.get("ci_actor"),
            ci_run_url=ci_context.get("ci_run_url"),
            status=run_status,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            broken_tests=broken,
            unknown_tests=unknown,
            pass_rate=pass_rate,
            primary_suite_name=suite_label,
            suite_names=[suite_label] if suite_label else None,
            start_time=session.started_at or now,
            end_time=now,
        )
        db.add(run)
        logger.info(
            f"test_run_created_for_live_session run_id={run_uuid} session_id={session.id}"
        )
    else:
        run.status = run_status
        run.total_tests = total
        run.passed_tests = passed
        run.failed_tests = failed
        run.skipped_tests = skipped
        run.broken_tests = broken
        run.unknown_tests = unknown
        run.pass_rate = pass_rate
        run.end_time = now
        # Only overwrite suite when SDK provided one — preserve any value
        # already populated by per-event aggregation.
        if suite_label and not run.primary_suite_name:
            run.primary_suite_name = suite_label
            run.suite_names = [suite_label]
        # CI context: fill-if-null only (the create_session stub usually
        # already carries it; this covers drainer-created rows).
        for field in ("ci_provider", "ci_repo", "pr_number", "ci_actor", "ci_run_url"):
            value = ci_context.get(field)
            if value is not None and getattr(run, field) is None:
                setattr(run, field, value)
        logger.info(
            f"test_run_updated_for_live_session run_id={run_uuid} session_id={session.id}"
        )

    # US-8.1 — persist a caller-supplied commit range stashed at session
    # create. Staged under this session (handler commits); best-effort so a
    # malformed list never fails live-run persistence.
    supplied_range = (getattr(session, "extra_metadata", None) or {}).get("commit_range")
    if supplied_range:
        try:
            await db.flush()
            from app.services.commit_attribution_service import store_supplied_range
            await store_supplied_range(db, run, supplied_range)
        except Exception as exc:
            logger.warning(
                f"live_supplied_commit_range_store_failed run_id={run_uuid} error={exc}"
            )
