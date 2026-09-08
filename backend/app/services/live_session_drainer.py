"""Project run-isolated live evidence into PostgreSQL without event loss."""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from redis.exceptions import ResponseError
from sqlalchemy import func, select as _sel
from sqlalchemy.dialects.postgresql import insert as _pg_insert

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.db.redis_client import get_redis
from app.models.postgres import (
    LaunchStatus,
    LiveEventReceipt,
    LiveIngestionAttempt,
    LiveProjectionCheckpoint,
    TestCase,
    TestRun,
    TestStatus,
)
from app.services.ingestion_sanitization import sanitize_test_result_payload
from app.services.run_tombstone_service import run_is_tombstoned
from app.streams import (
    LIVE_BATCH_DEDUP_KEY,
    LIVE_EVIDENCE_GROUP,
    LIVE_EVIDENCE_STREAM_KEY,
    LIVE_TESTCASES_KEY,
)
from app.streams.live_run_state import RedisLiveRunState

logger = structlog.get_logger(__name__)
_DRAIN_LOCK_KEY = "testlookup:live:drain_lock:{run_id}"
_DRAIN_LOCK_TTL_SECONDS = 90
_LOCK_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_LOCK_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_ACK_BATCH_LUA = r"""
local expected_types = {
  'stream', 'string', 'hash', 'list', 'set', 'set', 'set', 'set', 'set', 'set', 'set'
}
for i = 1, 11 do
  local actual = redis.call('TYPE', KEYS[i])['ok']
  if actual ~= 'none' and actual ~= expected_types[i] then
    return {'wrongtype', KEYS[i]}
  end
end
if redis.call('GET', KEYS[2]) ~= ARGV[4] then
  return {'lock_lost', '0'}
end
local pending = redis.call(
  'XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1, ARGV[2]
)
if #pending ~= 1 or pending[1][1] ~= ARGV[3] then
  return {'pending_mismatch', '0'}
end
local encoded = redis.call('HGET', KEYS[3], ARGV[5])
if not encoded then
  return {'dedupe_missing', '0'}
end
local ok, receipt = pcall(cjson.decode, encoded)
if not ok or (receipt['state'] ~= 'accepted' and receipt['state'] ~= 'projected')
   or receipt['stream_id'] ~= ARGV[3]
   or tonumber(receipt['count']) ~= tonumber(ARGV[6]) then
  return {'dedupe_mismatch', '0'}
end
local ids_ok, event_ids = pcall(cjson.decode, ARGV[7])
if not ids_ok or type(event_ids) ~= 'table' or #event_ids ~= tonumber(ARGV[6]) then
  return {'identity_mismatch', '0'}
end
if receipt['state'] == 'accepted' then
  receipt['state'] = 'projected'
  redis.call('HSET', KEYS[3], ARGV[5], cjson.encode(receipt))
end
if #event_ids > 0 then redis.call('SREM', KEYS[5], unpack(event_ids)) end
if redis.call('XACK', KEYS[1], ARGV[1], ARGV[3]) ~= 1 then
  return {'ack_mismatch', '0'}
end
redis.call('XDEL', KEYS[1], ARGV[3])
if redis.call('SCARD', KEYS[5]) == 0
   and redis.call('HGET', KEYS[3], '__gate__') == 'closed' then
  for i = 3, 11 do redis.call('EXPIRE', KEYS[i], ARGV[8]) end
end
return {'acknowledged', ARGV[6]}
"""
_ACK_LEGACY_RECORD_LUA = r"""
if redis.call('GET', KEYS[2]) ~= ARGV[4] then return {'lock_lost', '0'} end
local pending = redis.call(
  'XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1, ARGV[2]
)
if #pending ~= 1 or pending[1][1] ~= ARGV[3] then
  return {'pending_mismatch', '0'}
end
local ids = cjson.decode(ARGV[5])
if #ids > 0 then
  if redis.call('LLEN', KEYS[3]) < #ids then return {'legacy_mismatch', '0'} end
  for i = 1, #ids do
    local ok, payload = pcall(cjson.decode, redis.call('LINDEX', KEYS[3], i - 1))
    if not ok or payload['_event_id'] ~= ids[i] then
      return {'legacy_mismatch', tostring(i)}
    end
  end
end
if redis.call('XACK', KEYS[1], ARGV[1], ARGV[3]) ~= 1 then
  return {'ack_mismatch', '0'}
end
redis.call('XDEL', KEYS[1], ARGV[3])
if #ids > 0 then redis.call('LTRIM', KEYS[3], #ids, -1) end
return {'acknowledged', '1'}
"""


def _resolved_started_at(state: dict, now: datetime) -> datetime:
    raw = state.get("started_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            pass
    return now


def _resolved_suite(event: dict, default_suite: Optional[str]) -> Optional[str]:
    resolved = ((event.get("suite_name") or "").strip() or default_suite or "")[:500]
    return resolved or None


def _fingerprint(event: dict) -> str:
    value = f"{event.get('test_name') or ''}:{event.get('class_name') or ''}"
    return hashlib.md5(value.encode()).hexdigest()


def _event_to_row(
    event: dict, *, run_uuid: _uuid_mod.UUID, default_suite: Optional[str]
) -> dict:
    safe = sanitize_test_result_payload(event)
    test_name = safe.get("test_name") or ""
    class_name = safe.get("class_name") or ""
    try:
        status = TestStatus((safe.get("status") or "UNKNOWN").upper())
    except ValueError:
        status = TestStatus.UNKNOWN
    fingerprint = _fingerprint(safe)
    return {
        "id": _uuid_mod.uuid5(run_uuid, fingerprint),
        "test_run_id": run_uuid,
        "test_fingerprint": fingerprint,
        "test_name": test_name[:1000],
        "suite_name": _resolved_suite(safe, default_suite),
        "class_name": class_name[:500] or None,
        "status": status.value,
        "duration_ms": safe.get("duration_ms"),
        "error_message": safe.get("error_message"),
        "stack_trace": safe.get("stack_trace"),
        "tags": safe.get("tags"),
    }


async def _acquire_lock(redis, run_id: str) -> Optional[str]:
    token = secrets.token_urlsafe(32)
    acquired = await redis.set(
        _DRAIN_LOCK_KEY.format(run_id=run_id),
        token,
        nx=True,
        ex=_DRAIN_LOCK_TTL_SECONDS,
    )
    return token if acquired else None


async def _renew_lock(redis, run_id: str, token: str) -> bool:
    return bool(await redis.eval(
        _LOCK_RENEW_LUA,
        1,
        _DRAIN_LOCK_KEY.format(run_id=run_id),
        token,
        _DRAIN_LOCK_TTL_SECONDS,
    ))


async def _release_lock(redis, run_id: str, token: str) -> None:
    try:
        key = _DRAIN_LOCK_KEY.format(run_id=run_id)
        if hasattr(redis, "eval"):
            await redis.eval(_LOCK_RELEASE_LUA, 1, key, token)
        else:  # compatibility for old test doubles only
            await redis.delete(key)
    except Exception:  # pragma: no cover - lock expires naturally
        pass


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _field(fields: dict, name: str, default: Any = "") -> Any:
    return fields.get(name, fields.get(name.encode(), default))


async def _ensure_group(redis, stream_key: str) -> None:
    try:
        await redis.xgroup_create(stream_key, LIVE_EVIDENCE_GROUP, id="0-0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _read_stream_entries(
    redis, stream_key: str, consumer: str, count: int
) -> list[tuple[str, dict]]:
    await _ensure_group(redis, stream_key)
    entries: list = []
    try:
        claimed = await redis.xautoclaim(
            stream_key,
            LIVE_EVIDENCE_GROUP,
            consumer,
            # The per-run ownership lock serializes drainers. Claim every
            # pending entry immediately so a prior DB failure cannot leave an
            # older, young PEL entry behind while a newer entry is projected
            # and trimmed past it.
            min_idle_time=0,
            start_id="0-0",
            count=count,
        )
        entries = list(claimed[1] or [])
    except ResponseError:
        entries = []
    if not entries:
        result = await redis.xreadgroup(
            LIVE_EVIDENCE_GROUP,
            consumer,
            {stream_key: ">"},
            count=count,
            block=1,
        )
        if result:
            entries = list(result[0][1])
    return [(_text(message_id), fields) for message_id, fields in entries]


def _decode_stream_entries(entries: list[tuple[str, dict]]) -> list[dict]:
    """Validate batch manifests and expand them for relational projection.

    A compatibility branch accepts the per-event record used during the
    rolling cutover. Malformed durable evidence is left pending by raising;
    it is never projected as an empty payload.
    """
    decoded: list[dict] = []
    for stream_id, fields in entries:
        encoded_events = _field(fields, "events_json", None)
        if encoded_events is not None:
            try:
                events = json.loads(_text(encoded_events))
                event_ids = json.loads(_text(_field(fields, "event_ids_json")))
                event_count = int(_field(fields, "event_count"))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("live evidence batch encoding is malformed") from exc
            if (
                not isinstance(events, list)
                or not isinstance(event_ids, list)
                or event_count < 1
                or len(events) != event_count
                or len(event_ids) != event_count
            ):
                raise RuntimeError("live evidence batch count does not match payload")
            session_id = _text(_field(fields, "session_id"))
            batch_id = _text(_field(fields, "batch_id"))
            batch_digest = _text(_field(fields, "batch_digest"))
            run_id = _text(_field(fields, "run_id"))
            normalized = []
            for event in events:
                if not isinstance(event, dict):
                    raise RuntimeError("live evidence event must be an object")
                without_internal_run = dict(event)
                without_internal_run.pop("run_id", None)
                normalized.append(sanitize_test_result_payload(without_internal_run))
            canonical = json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
            if hashlib.sha256(canonical.encode()).hexdigest() != batch_digest:
                raise RuntimeError("live evidence batch digest mismatch")
            trim_legacy = _text(_field(fields, "trim_legacy", "0")) == "1"
            for index, (payload, event_id) in enumerate(zip(normalized, event_ids)):
                expected = hashlib.sha256(
                    f"{session_id}\0{batch_id}\0{index}".encode()
                ).hexdigest()
                if not isinstance(event_id, str) or event_id != expected:
                    raise RuntimeError("live evidence event identity mismatch")
                decoded.append({
                    "stream_id": stream_id,
                    "event_id": event_id,
                    "batch_id": batch_id,
                    "batch_event_count": event_count,
                    "batch_digest": batch_digest,
                    "dedupe_field": "batch:" + hashlib.sha256(
                        f"{session_id}\0{batch_id}".encode()
                    ).hexdigest(),
                    "session_id": session_id,
                    "run_id": run_id,
                    "event_index": index,
                    "event_type": str(payload.get("event_type") or "test_result"),
                    "payload": payload,
                    "trim_legacy": trim_legacy,
                    "legacy_stream_record": False,
                })
            continue

        # Rolling-cutover fallback: one Stream record per event.
        try:
            event_index = int(_field(fields, "event_index", 0))
            batch_event_count = int(
                _field(fields, "batch_event_count", event_index + 1)
            )
            payload = json.loads(_text(_field(fields, "payload", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("legacy live evidence record is malformed") from exc
        event_id = _text(_field(fields, "event_id"))
        batch_id = _text(_field(fields, "batch_id"))
        session_id = _text(_field(fields, "session_id"))
        expected = hashlib.sha256(
            f"{session_id}\0{batch_id}\0{event_index}".encode()
        ).hexdigest()
        if event_id != expected:
            raise RuntimeError("legacy live evidence event identity mismatch")
        decoded.append({
            "stream_id": stream_id,
            "event_id": event_id,
            "batch_id": batch_id,
            "batch_event_count": batch_event_count,
            "session_id": session_id,
            "event_index": event_index,
            "event_type": _text(_field(fields, "event_type", "test_result")),
            "payload": sanitize_test_result_payload(payload),
            "trim_legacy": False,
            "legacy_stream_record": True,
        })
    return decoded


def _decode_legacy_entries(run_id: str, raw_entries: list) -> list[dict]:
    payloads = []
    for raw in raw_entries:
        raw_text = _text(raw)
        try:
            payload = sanitize_test_result_payload(json.loads(raw_text))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payloads.append(payload)
    session_id = "legacy-runtime-" + hashlib.sha256(run_id.encode()).hexdigest()
    canonical = json.dumps(
        payloads, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    batch_id = "legacy-" + hashlib.sha256(
        f"{session_id}\0{run_id}\0{canonical}".encode()
    ).hexdigest()
    decoded = []
    for index, payload in enumerate(payloads):
        event_id = hashlib.sha256(
            f"{session_id}\0{batch_id}\0{index}".encode()
        ).hexdigest()
        decoded.append({
            "stream_id": f"legacy-{index}",
            "event_id": event_id,
            "batch_id": batch_id,
            "batch_event_count": len(raw_entries),
            "session_id": session_id,
            "event_index": index,
            "event_type": "test_result",
            "payload": payload,
        })
    return decoded


def _attempt_id(session_id: str, batch_id: str) -> _uuid_mod.UUID:
    return _uuid_mod.uuid5(
        _uuid_mod.NAMESPACE_URL, f"testlookup-live:{session_id}\0{batch_id}"
    )


async def _upsert_projection_rows(
    db, evidence: list[dict], *, run_uuid: _uuid_mod.UUID,
    stream_key: str, session_suite: Optional[str], now: datetime,
) -> None:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in evidence:
        grouped.setdefault((item["session_id"], item["batch_id"]), []).append(item)
    attempts: list[dict[str, object]] = []
    receipts: list[dict[str, object]] = []
    for (session_id, batch_id), items in grouped.items():
        attempt_uuid = _attempt_id(session_id, batch_id)
        attempts.append({
            "id": attempt_uuid, "run_id": run_uuid, "session_id": session_id,
            "batch_id": batch_id,
            "event_count": max(item["batch_event_count"] for item in items),
            "first_event_id": items[0]["event_id"], "last_event_id": items[-1]["event_id"],
            "first_stream_id": items[0]["stream_id"], "last_stream_id": items[-1]["stream_id"],
            "projected_at": now,
        })
        receipts.extend({
            "id": _uuid_mod.uuid5(_uuid_mod.NAMESPACE_URL, item["event_id"]),
            "attempt_id": attempt_uuid, "run_id": run_uuid,
            "event_id": item["event_id"], "stream_id": item["stream_id"],
            "event_index": item["event_index"], "event_type": item["event_type"],
            "payload": item["payload"],
            "projected_at": now,
        } for item in items)
    attempt_insert = _pg_insert(LiveIngestionAttempt)
    await db.execute(attempt_insert.on_conflict_do_update(
        constraint="uq_live_ingestion_attempt_session_batch",
        set_={
            "event_count": func.greatest(
                LiveIngestionAttempt.event_count,
                attempt_insert.excluded.event_count,
            ),
            "last_event_id": attempt_insert.excluded.last_event_id,
            "last_stream_id": attempt_insert.excluded.last_stream_id,
            "projected_at": attempt_insert.excluded.projected_at,
        },
    ), attempts)
    await db.execute(_pg_insert(LiveEventReceipt).on_conflict_do_nothing(
        constraint="uq_live_event_receipt_event"
    ), receipts)

    by_fingerprint = {}
    for item in evidence:
        if item["event_type"] == "test_result":
            row = _event_to_row(item["payload"], run_uuid=run_uuid, default_suite=session_suite)
            by_fingerprint[row["test_fingerprint"]] = row
    rows = list(by_fingerprint.values())
    if rows:
        case_insert = _pg_insert(TestCase)
        fields = ("test_name", "suite_name", "class_name", "status", "duration_ms",
                  "error_message", "stack_trace", "tags")
        statement = case_insert.on_conflict_do_update(
            constraint="uq_test_cases_run_fingerprint",
            set_={name: getattr(case_insert.excluded, name) for name in fields},
        )
        chunk = max(1, settings.PERSIST_LIVE_BULK_INSERT_CHUNK)
        for offset in range(0, len(rows), chunk):
            await db.execute(statement, rows[offset:offset + chunk])

    last = evidence[-1]
    checkpoint = _pg_insert(LiveProjectionCheckpoint)
    await db.execute(checkpoint.on_conflict_do_update(
        index_elements=[LiveProjectionCheckpoint.run_id],
        set_={
            "stream_key": checkpoint.excluded.stream_key,
            "consumer_group": checkpoint.excluded.consumer_group,
            "last_stream_id": checkpoint.excluded.last_stream_id,
            "last_event_id": checkpoint.excluded.last_event_id,
            "updated_at": now,
        },
    ), [{
        "run_id": run_uuid, "stream_key": stream_key,
        "consumer_group": LIVE_EVIDENCE_GROUP,
        "last_stream_id": last["stream_id"], "last_event_id": last["event_id"],
        "updated_at": now,
    }])


async def _project(
    db, evidence: list[dict], *, run_uuid: _uuid_mod.UUID,
    stream_key: str, project_uuid: _uuid_mod.UUID, build_number: str,
    session_suite: Optional[str], state: dict, now: datetime,
) -> None:
    run = (await db.execute(_sel(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if run is None and await run_is_tombstoned(db, run_uuid):
        raise RuntimeError(f"run {run_uuid} is tombstoned")
    agg_passed = int(state.get("passed", 0) or 0)
    agg_failed = int(state.get("failed", 0) or 0)
    agg_skipped = int(state.get("skipped", 0) or 0)
    agg_broken = int(state.get("broken", 0) or 0)
    agg_unknown = int(state.get("unknown", 0) or 0)
    total = int(state.get("total", 0) or 0) or (
        agg_passed + agg_failed + agg_skipped + agg_broken + agg_unknown
    )
    if run is None:
        run = TestRun(
            id=run_uuid, project_id=project_uuid,
            build_number=build_number or state.get("build_number") or str(run_uuid)[:8],
            trigger_source="live_stream", ingestion_source="live",
            status=LaunchStatus.IN_PROGRESS, total_tests=total,
            passed_tests=agg_passed, failed_tests=agg_failed,
            skipped_tests=agg_skipped, broken_tests=agg_broken,
            unknown_tests=agg_unknown, primary_suite_name=session_suite,
            suite_names=[session_suite] if session_suite else None,
            start_time=_resolved_started_at(state, now), end_time=now,
        )
        db.add(run)
        await db.flush()
    else:
        run.total_tests, run.end_time = total, now
        run.passed_tests = agg_passed
        run.failed_tests = agg_failed
        run.skipped_tests = agg_skipped
        run.broken_tests = agg_broken
        run.unknown_tests = agg_unknown
        if session_suite and not run.primary_suite_name:
            run.primary_suite_name, run.suite_names = session_suite, [session_suite]
    await _upsert_projection_rows(
        db, evidence, run_uuid=run_uuid, stream_key=stream_key,
        session_suite=session_suite, now=now,
    )


async def drain_run_buffer(
    run_id: str, project_id: str, *, build_number: str = "",
    suite_name: Optional[str] = None, max_events: Optional[int] = None,
) -> dict:
    """Commit one bounded projection, then ACK/trim through its watermark."""
    from app.services.stream_service import canonical_test_run_uuid

    result: dict[str, object] = {
        "drained": 0,
        "lock_held": False,
        "run_uuid": None,
    }
    chunk = max_events or settings.LIVE_SESSION_DRAIN_BATCH_SIZE
    if chunk <= 0:
        return result
    try:
        project_uuid = _uuid_mod.UUID(project_id)
    except ValueError:
        logger.error("drain_invalid_project_id", run_id=run_id, project_id=project_id)
        return result
    redis = get_redis()
    token = await _acquire_lock(redis, run_id)
    if token is None:
        return result
    result["lock_held"] = True
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    list_key = LIVE_TESTCASES_KEY.format(run_id=run_id)
    try:
        stream_exists = bool(await redis.exists(stream_key)) if hasattr(redis, "exists") else False
        stream_entries, raw_entries = [], []
        if stream_exists and hasattr(redis, "xreadgroup"):
            consumer = f"drainer-{secrets.token_hex(12)}"
            stream_entries = await _read_stream_entries(
                redis, stream_key, consumer, 1
            )
            evidence = _decode_stream_entries(stream_entries)
        else:
            raw_entries = await redis.lrange(list_key, 0, chunk - 1)
            evidence = _decode_legacy_entries(run_id, raw_entries)
        if not evidence:
            return result
        run_uuid = canonical_test_run_uuid(run_id)
        result["run_uuid"] = str(run_uuid)
        state = await RedisLiveRunState.get(run_id) or {}
        # Keep the effective total explicit at this boundary. UNKNOWN is a
        # durable outcome bucket and must never disappear from run totals.
        agg_passed = int(state.get("passed", 0) or 0)
        agg_failed = int(state.get("failed", 0) or 0)
        agg_skipped = int(state.get("skipped", 0) or 0)
        agg_broken = int(state.get("broken", 0) or 0)
        agg_unknown = int(state.get("unknown", 0) or 0)
        state = {
            **state,
            "total": int(state.get("total", 0) or 0) or (
                agg_passed + agg_failed + agg_skipped + agg_broken + agg_unknown
            ),
        }
        session_suite = (suite_name or state.get("suite_name") or "").strip() or None
        now = datetime.now(timezone.utc)
        if hasattr(redis, "eval") and not await _renew_lock(redis, run_id, token):
            raise RuntimeError("live drain lock ownership lost before projection")
        async with AsyncSessionLocal() as db:
            await _project(
                db, evidence, run_uuid=run_uuid,
                stream_key=stream_key if stream_entries else list_key,
                project_uuid=project_uuid, build_number=build_number,
                session_suite=session_suite, state=state, now=now,
            )
            await db.commit()
        if stream_entries:
            stream_id = stream_entries[0][0]
            legacy_ids = [
                item["event_id"]
                for item in evidence
                if item["event_type"] == "test_result"
            ]
            if evidence[0].get("legacy_stream_record"):
                ack_result = await redis.eval(
                    _ACK_LEGACY_RECORD_LUA,
                    3,
                    stream_key,
                    _DRAIN_LOCK_KEY.format(run_id=run_id),
                    list_key,
                    LIVE_EVIDENCE_GROUP,
                    consumer,
                    stream_id,
                    token,
                    json.dumps(legacy_ids, separators=(",", ":")),
                )
            else:
                ledger_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
                all_event_ids = [item["event_id"] for item in evidence]
                ack_result = await redis.eval(
                    _ACK_BATCH_LUA,
                    11,
                    stream_key,
                    _DRAIN_LOCK_KEY.format(run_id=run_id),
                    ledger_key,
                    list_key,
                    f"{ledger_key}:pending",
                    f"{ledger_key}:passed",
                    f"{ledger_key}:failed",
                    f"{ledger_key}:skipped",
                    f"{ledger_key}:broken",
                    f"{ledger_key}:unknown",
                    f"{ledger_key}:received",
                    LIVE_EVIDENCE_GROUP,
                    consumer,
                    stream_id,
                    token,
                    evidence[0]["dedupe_field"],
                    str(evidence[0]["batch_event_count"]),
                    json.dumps(all_event_ids, separators=(",", ":")),
                    str(15 * 24 * 60 * 60),
                )
            ack_status = _text(ack_result[0])
            if ack_status != "acknowledged":
                raise RuntimeError(
                    f"live evidence cleanup fence rejected: {ack_status}"
                )
        else:
            await redis.ltrim(list_key, len(raw_entries), -1)
        result["drained"] = len(evidence)
        logger.info("live_session_drained", run_id=run_id, project_id=project_id,
                    drained=len(evidence), source="stream" if stream_entries else "legacy-list")
        return result
    finally:
        await _release_lock(redis, run_id, token)


async def drain_all_active_runs(*, max_events_per_run: Optional[int] = None) -> dict:
    if not settings.LIVE_SESSION_DRAIN_ENABLED:
        return {"runs_scanned": 0, "drained": 0, "errors": 0, "disabled": True}
    totals = {"runs_scanned": 0, "drained": 0, "errors": 0}
    for state in await RedisLiveRunState.get_all_active():
        run_id, project_id = state.get("run_id"), state.get("project_id")
        if not run_id or not project_id:
            continue
        totals["runs_scanned"] += 1
        try:
            outcome = await drain_run_buffer(
                run_id=run_id, project_id=project_id,
                build_number=state.get("build_number") or "",
                suite_name=state.get("suite_name"), max_events=max_events_per_run,
            )
            totals["drained"] += int(outcome.get("drained", 0))
        except Exception as exc:
            totals["errors"] += 1
            logger.warning("drain_run_failed", run_id=run_id,
                           project_id=project_id, error=str(exc))
    return totals
