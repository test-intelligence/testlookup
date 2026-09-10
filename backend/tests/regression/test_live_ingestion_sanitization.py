"""M11: live and file ingestion must enforce one persistence privacy policy."""
from __future__ import annotations

import json
import math
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.services.privacy_service import sanitize_for_persistence


RAW_ERROR = (
    "Auth failed for admin@corp.example with password=Admin123! "
    "and api_key=sk_live_abcdefghijklmnop"
)
RAW_STACK = (
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\n"
    "request from 192.168.50.10"
)
EXPECTED_ERROR = sanitize_for_persistence(RAW_ERROR)
EXPECTED_STACK = sanitize_for_persistence(RAW_STACK)
NESTED_SECRETS = {
    "metadata": {
        "contact": "nested@example.com",
        "password": "NestedSecret123!",
    },
    "tags": ["owner=tag@example.com", "password=TagSecret123!"],
    "custom": {"authorization": "Bearer arbitrary-secret-token-123456"},
    "token": "top-level-token-123456",
}


def _assert_no_raw_canaries(payload: object) -> None:
    serialized = json.dumps(payload, default=str)
    for secret in (
        "admin@corp.example",
        "Admin123!",
        "sk_live_abcdefghijklmnop",
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "192.168.50.10",
        "nested@example.com",
        "NestedSecret123!",
        "tag@example.com",
        "TagSecret123!",
        "arbitrary-secret-token-123456",
        "top-level-token-123456",
        "StructuredSecret123!",
        "QuotedSecret123!",
        "XApiSecret123!",
        "MalformedSecret123!",
    ):
        assert secret not in serialized


def _assert_failure_fields_are_safe(payload: dict) -> None:
    assert payload["error_message"] == EXPECTED_ERROR
    assert payload["stack_trace"] == EXPECTED_STACK
    _assert_no_raw_canaries(payload)


def test_shared_normalizer_is_copying_idempotent_and_preserves_none():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    raw = {
        "test_name": "checkout",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
        "unchanged": {"key": "value"},
        **NESTED_SECRETS,
    }
    safe = sanitize_test_result_payload(raw)

    _assert_failure_fields_are_safe(safe)
    assert safe is not raw
    assert raw["error_message"] == RAW_ERROR
    assert sanitize_test_result_payload(safe) == safe
    assert sanitize_test_result_payload({
        "error_message": None,
        "stack_trace": None,
    }) == {"error_message": None, "stack_trace": None}
    assert safe["test_name"] == "checkout"
    assert len(safe["tags"]) == len(raw["tags"])
    _assert_no_raw_canaries(safe)


@pytest.mark.parametrize(
    "failure_value",
    [
        {"password": "StructuredSecret123!"},
        ["password=StructuredSecret123!"],
    ],
)
def test_shared_normalizer_fails_closed_for_structured_failure_fields(
    failure_value,
):
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    safe = sanitize_test_result_payload({
        "error_message": failure_value,
        "stack_trace": failure_value,
    })

    assert safe == {"error_message": "[REDACTED]", "stack_trace": "[REDACTED]"}
    _assert_no_raw_canaries(safe)


def test_shared_normalizer_sanitizes_embedded_json_and_bounds_text():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    safe = sanitize_test_result_payload({
        "error_message": '{"password":"QuotedSecret123!"}',
        "metadata": {"oversized": "x" * 100_001},
        "tags": ["safe"] * 1_001,
    })

    assert json.loads(safe["error_message"])["password"] == "[REDACTED]"
    assert safe["metadata"]["oversized"] == "[REDACTED]"
    assert safe["tags"] == ["[REDACTED]"]
    _assert_no_raw_canaries(safe)


def test_shared_normalizer_fails_closed_for_malformed_json_and_bad_identity():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    safe = sanitize_test_result_payload({
        "error_message": '{"password":"MalformedSecret123!"',
        "payload": '{"X-API-Key":"XApiSecret123!"',
        "metadata": {"headers": {"X-API-Key": "XApiSecret123!"}},
        "test_name": {"password": "MalformedSecret123!"},
        "duration_ms": "100",
    })

    assert safe["error_message"] == "[REDACTED]"
    assert safe["payload"] == "[REDACTED]"
    assert safe["metadata"]["headers"]["X-API-Key"] == "[REDACTED]"
    assert safe["test_name"] == "[REDACTED]"
    assert safe["duration_ms"] is None
    _assert_no_raw_canaries(safe)


def test_shared_normalizer_redacts_short_password_basic_auth_ipv6_and_nonfinite():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    safe = sanitize_test_result_payload({
        "error_message": (
            "password=abc login password=\"Secret Value 123!\" "
            "Authorization: Basic dXNlcjpwYXNz Authorization: Bearer abc "
            "token=abc api_key=abc secret=abc from 2001:db8::1"
        ),
        "metadata": {
            "nan": math.nan,
            "infinity": math.inf,
            "huge_integer": 10**5_000,
        },
        "duration_ms": 2_147_483_648,
        "timestamp_ms": -1,
        "total_tests": True,
    })

    serialized = json.dumps(safe)
    assert "password=abc" not in serialized
    assert "Secret Value 123!" not in serialized
    assert "dXNlcjpwYXNz" not in serialized
    assert "Bearer abc" not in serialized
    assert "token=abc" not in serialized
    assert "api_key=abc" not in serialized
    assert "secret=abc" not in serialized
    assert "2001:db8::1" not in serialized
    assert safe["metadata"] == {
        "nan": "[REDACTED]",
        "infinity": "[REDACTED]",
        "huge_integer": "[REDACTED]",
    }
    assert safe["duration_ms"] is None
    assert safe["timestamp_ms"] is None
    assert safe["total_tests"] is None


def test_shared_normalizer_returns_mapping_for_scalar_input():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    assert sanitize_test_result_payload(1) == {"_redacted": "[REDACTED]"}


def test_shared_normalizer_sanitizes_and_bounds_mapping_keys():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    safe = sanitize_test_result_payload({
        "metadata": {
            "password=KeySecret123!": "safe",
            "admin@corp.example": "safe",
            "k" * 200_000: "safe",
            " password ": "TrimmedKeySecret123!",
            "X-API-Key ": "XApiTrailingSecret123!",
        },
    })

    serialized = json.dumps(safe)
    assert "KeySecret123!" not in serialized
    assert "admin@corp.example" not in serialized
    assert "k" * 256 not in serialized
    assert "TrimmedKeySecret123!" not in serialized
    assert "XApiTrailingSecret123!" not in serialized


def test_live_identifier_accepts_ci_punctuation_and_rejects_unsafe_values():
    from app.services.ingestion_sanitization import validate_live_identifier

    assert (
        validate_live_identifier("run_id", "github/org/repo#123")
        == "github/org/repo#123"
    )
    assert validate_live_identifier("run_id", "owner@example.com") == "owner@example.com"
    for unsafe in (
        "run\nheader",
        "",
        "x" * 256,
    ):
        with pytest.raises(ValueError):
            validate_live_identifier("run_id", unsafe)


@pytest.mark.asyncio
async def test_api_key_ingest_rejects_unsafe_run_id_before_side_effects():
    from fastapi import HTTPException
    from app.services.stream_service import ingest_via_api_key

    db = SimpleNamespace(get=AsyncMock())
    request = SimpleNamespace(run_id="run\nheader")

    with pytest.raises(HTTPException) as raised:
        await ingest_via_api_key(
            db,
            uuid.uuid4(),
            "key-name",
            request,
        )

    assert raised.value.status_code == 422
    db.get.assert_not_awaited()


def test_shared_normalizer_bounds_top_level_and_total_nested_work():
    from app.services.ingestion_sanitization import sanitize_test_result_payload

    too_wide = {
        "test_name": "bounded",
        "error_message": RAW_ERROR,
        **{f"field_{index}": "safe" for index in range(1_001)},
    }
    compact = sanitize_test_result_payload(too_wide)
    assert compact == {
        "_redacted": "[REDACTED]",
        "test_name": "bounded",
        "error_message": "[REDACTED]",
    }

    nested = {f"branch_{index}": ["safe"] * 1_000 for index in range(11)}
    bounded = sanitize_test_result_payload({"metadata": nested})
    assert len(json.dumps(bounded)) < 100_000
    assert "[REDACTED]" in json.dumps(bounded)

    forged = "[REDACTEDAdminSecret]"
    placeholder_bypass = sanitize_test_result_payload({
        "error_message": forged,
        "metadata": ["[REDACTED" + ("A" * 99_988) + "]"] * 1_000,
    })
    serialized = json.dumps(placeholder_bypass)
    assert forged not in serialized
    assert len(serialized) < 1_100_000


@pytest.mark.asyncio
async def test_file_and_live_sql_use_identical_sanitized_failure_fields():
    from app.services.ingestion import _upsert_test_case
    from app.services.live_session_drainer import _event_to_row

    history_result = SimpleNamespace(scalar_one_or_none=lambda: None)
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=history_result),
        add=added.append,
        flush=AsyncMock(),
    )
    run = SimpleNamespace(id=uuid.uuid4())
    event = {
        "test_name": "checkout",
        "class_name": "CheckoutTests",
        "status": "failed",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
        **NESTED_SECRETS,
        "steps": [],
        "attachments": [],
    }

    file_row = await _upsert_test_case(
        db,
        event,
        run,
        existing=None,
        fingerprint="fp",
        existing_was_prefetched=True,
    )
    live_row = _event_to_row(
        event,
        run_uuid=run.id,
        default_suite=None,
    )

    assert file_row.error_message == live_row["error_message"] == EXPECTED_ERROR
    assert file_row.stack_trace == live_row["stack_trace"] == EXPECTED_STACK
    assert file_row.tags == live_row["tags"]
    _assert_no_raw_canaries(file_row.tags)
    _assert_no_raw_canaries(live_row["tags"])


class _Pipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return _record

    async def execute(self):
        self.calls.append(("execute", (), {}))
        return ["1-0" for name, _args, _kwargs in self.calls if name == "xadd"]


class _WatchPipeline:
    def __init__(self, redis) -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple, dict]] = []
        self.in_multi = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def watch(self, *_keys):
        return None

    def multi(self):
        self.in_multi = True

    async def _read(self, name, *args, **kwargs):
        return await getattr(self.redis, name)(*args, **kwargs)

    async def exists(self, *args, **kwargs):
        return await self._read("exists", *args, **kwargs)

    async def xinfo_groups(self, *args, **kwargs):
        return await self._read("xinfo_groups", *args, **kwargs)

    async def xlen(self, *args, **kwargs):
        return await self._read("xlen", *args, **kwargs)

    async def xrevrange(self, *args, **kwargs):
        return await self._read("xrevrange", *args, **kwargs)

    async def xrange(self, *args, **kwargs):
        return await self._read("xrange", *args, **kwargs)

    async def pttl(self, *args, **kwargs):
        return await self._read("pttl", *args, **kwargs)

    async def lrange(self, *args, **kwargs):
        return await self._read("lrange", *args, **kwargs)

    def __getattr__(self, name: str):
        if not self.in_multi:
            raise AttributeError(name)

        def _queue(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self):
        if getattr(self.redis, "watch_error", False):
            from redis.exceptions import WatchError

            raise WatchError("source changed")
        results = []
        for name, args, kwargs in self.commands:
            result = getattr(self.redis, name)(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            results.append(result)
        return results


@pytest.mark.asyncio
async def test_live_batch_sanitizes_both_redis_stream_and_buffer(monkeypatch):
    """The first cache write must not retain raw failure evidence."""
    from app.services import stream_service

    event = {
        "event_type": "test_result",
        "test_name": "checkout",
        "status": "FAILED",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
        **NESTED_SECRETS,
    }
    original = dict(event)
    redis = MagicMock()
    redis.eval = AsyncMock(return_value=[b"accepted", b"1"])
    monkeypatch.setattr(stream_service.settings, "LIVE_BUFFER_MAX_EVENTS_PER_RUN", 50_000)

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(
            stream_service,
            "_resolve_project_id_for_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        await stream_service._persist_event_batch("session-1", "run-1", [event])

    eval_args = redis.eval.await_args.args
    manifest = json.loads(eval_args[32])
    wire = json.loads(eval_args[34])
    published = manifest[0]
    buffered = json.loads(wire[0]["legacy_entry"])
    _assert_failure_fields_are_safe(published)
    _assert_failure_fields_are_safe(buffered)
    _assert_no_raw_canaries(published)
    _assert_no_raw_canaries(buffered)
    assert event == original, "normalization must not mutate the caller's event"


def test_incremental_drainer_sanitizes_legacy_buffer_before_sql():
    """Defense at the SQL boundary protects buffers written before rollout."""
    from app.services.live_session_drainer import _event_to_row

    row = _event_to_row(
        {
            "test_name": "checkout",
            "status": "FAILED",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        },
        run_uuid=uuid.uuid4(),
        default_suite="smoke",
    )

    _assert_failure_fields_are_safe(row)


def test_incremental_drainer_handles_scalar_legacy_event_as_safe_unknown():
    from app.services.live_session_drainer import _event_to_row

    row = _event_to_row(1, run_uuid=uuid.uuid4(), default_suite="smoke")

    assert row["test_name"] == ""
    assert row["status"] == "UNKNOWN"
    assert row["error_message"] is None
    assert row["stack_trace"] is None


class _Result:
    def __init__(self, scalar=None) -> None:
        self._scalar = scalar

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class _CapturingSession:
    def __init__(self) -> None:
        self.results = [_Result(0), _Result(None)]
        self.added = []
        self.executions: list[tuple[object, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement, params=None):
        self.executions.append((statement, params))
        return self.results.pop(0) if self.results else _Result()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self):
        return None


class _RedisWithLegacyEvent:
    async def lrange(self, *_args):
        return [json.dumps({
            "test_name": "checkout",
            "status": "FAILED",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        })]

    async def delete(self, *_args):
        return None


def test_close_worker_projects_through_the_sanitizing_drainer():
    """Terminal persistence must not bypass the sanitizer-aware drainer."""
    pytest.importorskip("celery")
    from app.worker import tasks

    session = _CapturingSession()
    drain = AsyncMock(return_value=1)
    with (
        patch("app.db.postgres.AsyncSessionLocal", return_value=session),
        patch.object(
            tasks,
            "_drain_live_evidence_before_finalize",
            new=drain,
        ),
        patch("app.services.ingestion_pipeline.finalize_run", new=AsyncMock()),
        patch(
            "app.services.stream_service.finalize_closed_session_redis",
            new=AsyncMock(),
        ),
        patch(
            "app.services.high_volume_detector.is_high_volume",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = tasks.persist_live_session.apply(kwargs={
            "run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "build_number": "m11",
            "final_state": {"total": 1, "failed": 1},
        })

    if not result.successful():
        raise AssertionError(result.traceback)
    drain.assert_awaited_once()
    insert_params = next(
        params for statement, params in session.executions
        if params and getattr(getattr(statement, "table", None), "name", None) == "test_cases"
    )
    _assert_no_raw_canaries(insert_params[0])


@pytest.mark.asyncio
async def test_archive_recovery_restages_only_sanitized_events(monkeypatch):
    from app.services import live_run_recovery_service

    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"accepted", b"1"]))
    monkeypatch.setattr(live_run_recovery_service, "get_redis", lambda: redis)

    await live_run_recovery_service._stage_archive_to_redis(
        "legacy-run",
        [{
            "test_name": "checkout",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        }],
    )

    staged = json.loads(redis.eval.await_args.args[17])[0]
    _assert_failure_fields_are_safe(staged)


@pytest.mark.asyncio
async def test_archive_recovery_restages_scalar_as_safe_mapping(monkeypatch):
    from app.services import live_run_recovery_service

    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"accepted", b"1"]))
    monkeypatch.setattr(live_run_recovery_service, "get_redis", lambda: redis)

    await live_run_recovery_service._stage_archive_to_redis("legacy-run", [1])

    staged = json.loads(redis.eval.await_args.args[17])[0]
    staged.pop("run_id")
    assert staged == {
        "_redacted": "[REDACTED]",
    }


@pytest.mark.asyncio
async def test_producers_defensively_sanitize_direct_callers(monkeypatch):
    from app.streams import producer

    pipe = _Pipeline()
    redis = SimpleNamespace(
        xadd=AsyncMock(return_value="1-0"),
        xlen=AsyncMock(return_value=0),
        pipeline=lambda: pipe,
    )
    monkeypatch.setattr(producer, "get_redis", lambda: redis)
    event = {
        "type": "test_result",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
        **NESTED_SECRETS,
    }

    await producer.publish_live_event("run-1", event)

    payload = json.loads(redis.xadd.await_args.args[1]["payload"])
    _assert_failure_fields_are_safe(payload)
    _assert_no_raw_canaries(payload)

    accepted = await producer.publish_event_batch("session-1", "run-1", [event])

    assert accepted == 1
    batch_payload = json.loads(next(
        call[1][1]["payload"] for call in pipe.calls if call[0] == "xadd"
    ))
    _assert_failure_fields_are_safe(batch_payload)
    _assert_no_raw_canaries(batch_payload)


@pytest.mark.asyncio
async def test_shared_producer_dlq_sanitizes_payload_error_and_log(
    monkeypatch,
    caplog,
):
    from app.streams import producer

    redis = SimpleNamespace(xadd=AsyncMock(return_value="1-0"))
    monkeypatch.setattr(producer, "get_redis", lambda: redis)
    caplog.set_level("ERROR", logger="streams.producer")

    await producer.publish_to_dlq(
        "source-stream",
        "1-0",
        {"payload": {"error_message": RAW_ERROR, **NESTED_SECRETS}},
        "password=abc Authorization: Basic dXNlcjpwYXNz",
        3,
    )

    fields = redis.xadd.await_args.args[1]
    _assert_no_raw_canaries(fields)
    assert "password=abc" not in json.dumps(fields)
    assert "dXNlcjpwYXNz" not in json.dumps(fields)
    assert "password=abc" not in caplog.text
    assert "dXNlcjpwYXNz" not in caplog.text


@pytest.mark.asyncio
async def test_worker_dlq_sanitizes_kwargs_error_and_log(monkeypatch, caplog):
    pytest.importorskip("celery")
    from app.db import redis_client
    from app.worker import tasks

    redis = SimpleNamespace(xadd=AsyncMock(return_value="1-0"))
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    caplog.set_level("ERROR", logger="app.worker.tasks")

    await tasks._send_to_dlq(
        "task-name",
        "task-id",
        {"payload": {"error_message": RAW_ERROR, **NESTED_SECRETS}},
        "password=abc Authorization: Basic dXNlcjpwYXNz",
    )

    fields = redis.xadd.await_args.args[1]
    _assert_no_raw_canaries(fields)
    assert "password=abc" not in json.dumps(fields)
    assert "dXNlcjpwYXNz" not in json.dumps(fields)
    assert "password=abc" not in caplog.text
    assert "dXNlcjpwYXNz" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_batch_persists_only_sanitized_evidence_in_real_redis(monkeypatch):
    """Exercise the actual Redis Stream and LIST serialization boundaries."""
    if os.environ.get("TESTLOOKUP_RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("real Redis integration tests are not enabled")
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.fail("REDIS_URL must be configured for real Redis integration tests")

    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app import streams
    from app.services import stream_service
    from app.services.live_persistence_scrub import (
        purge_drained_live_stream,
        scrub_redis_list,
        scrub_unconsumed_redis_stream,
    )
    from app.streams import producer

    suffix = uuid.uuid4().hex
    stream_key = f"testlookup:m11:stream:{suffix}"
    dispatch_key = f"testlookup:m11:dispatch:{suffix}"
    dlq_key = f"testlookup:m11:dlq:{suffix}"
    group_name = f"m11-{suffix}"
    list_template = f"testlookup:m11:list:{suffix}:{{run_id}}"
    state_template = f"testlookup:m11:state:{suffix}:{{run_id}}"
    run_id = f"run-{suffix}"
    list_key = list_template.format(run_id=run_id)
    state_key = state_template.format(run_id=run_id)
    dedupe_key = streams.LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        raise
    try:
        monkeypatch.setattr(producer, "get_redis", lambda: client)
        monkeypatch.setattr(stream_service, "get_redis", lambda: client)
        monkeypatch.setattr(streams, "LIVE_EVENTS_STREAM", dispatch_key)
        monkeypatch.setattr(streams, "LIVE_EVIDENCE_STREAM_KEY", stream_key)
        monkeypatch.setattr(streams, "LIVE_TESTCASES_KEY", list_template)
        monkeypatch.setattr(streams, "LIVE_STATE_KEY", state_template)
        monkeypatch.setattr(
            stream_service,
            "_resolve_project_id_for_run",
            AsyncMock(return_value=None),
        )

        await stream_service._persist_event_batch(
            "session-1",
            run_id,
            [{
                "event_type": "test_result",
                "test_name": "checkout",
                "status": "FAILED",
                "error_message": RAW_ERROR,
                "stack_trace": RAW_STACK,
                **NESTED_SECRETS,
            }],
        )

        stream_rows = await client.xrange(stream_key)
        buffered_rows = await client.lrange(list_key, 0, -1)
        assert len(stream_rows) == 1
        assert buffered_rows == [], (
            "current writers must use the lossless evidence stream instead of "
            "creating a second legacy LIST copy"
        )
        assert await client.ttl(stream_key) == -1, (
            "accepted evidence must remain non-expiring until its PostgreSQL "
            "watermark is committed and the drainer acknowledges it"
        )
        stream_payload = json.loads(stream_rows[0][1]["events_json"])[0]
        _assert_failure_fields_are_safe(stream_payload)
        _assert_no_raw_canaries(stream_payload)

        legacy_payload = {
            "test_name": "legacy",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        }
        await client.xadd(stream_key, {
            "run_id": run_id,
            "event_type": "test_result",
            "payload": json.dumps(legacy_payload),
        })
        await client.rpush(list_key, json.dumps(legacy_payload))
        await client.xgroup_create(stream_key, group_name, id="0")
        deliveries = await client.xreadgroup(
            group_name,
            "m11-consumer",
            {stream_key: ">"},
            count=100,
        )
        delivered_ids = [message_id for _key, rows in deliveries for message_id, _ in rows]
        assert len(delivered_ids) == 2
        assert await client.xack(stream_key, group_name, *delivered_ids) == 2

        purged = await purge_drained_live_stream(
            client,
            stream_key,
            group_name=group_name,
        )
        assert purged == 2
        assert (await client.xpending(stream_key, group_name))["pending"] == 0
        assert await client.xreadgroup(
            group_name,
            "m11-consumer",
            {stream_key: ">"},
            count=100,
        ) == []

        dlq_id = await client.xadd(dlq_key, {
            "source_stream": stream_key,
            "original_msg_id": "1-0",
            "original_data": json.dumps({"payload": json.dumps(legacy_payload)}),
            "error": RAW_ERROR,
            "attempt_count": "3",
        })
        assert await scrub_unconsumed_redis_stream(
            client,
            dlq_key,
            batch_size=1,
        ) == 1
        await scrub_redis_list(client, list_key, batch_size=1)

        scrubbed_stream = await client.xrange(dlq_key)
        scrubbed_list = await client.lrange(list_key, 0, -1)
        assert scrubbed_stream[0][0] == dlq_id
        _assert_no_raw_canaries(scrubbed_stream)
        _assert_no_raw_canaries(scrubbed_list)
    finally:
        await client.delete(
            stream_key,
            dispatch_key,
            dlq_key,
            list_key,
            state_key,
            dedupe_key,
            *(f"{dedupe_key}:{suffix}" for suffix in (
                "pending", "passed", "failed", "skipped", "broken", "unknown", "received"
            )),
        )
        await client.aclose()


@pytest.mark.asyncio
async def test_legacy_live_route_sanitizes_mongo_and_redis(monkeypatch):
    from app.db.mongo import Collections
    from app.routers import live
    from app.services.ingestion_sanitization import (
        LIVE_SANITIZATION_VERSION,
        LIVE_SANITIZATION_VERSION_FIELD,
    )

    collection = SimpleNamespace(insert_one=AsyncMock())
    mongo = {Collections.LIVE_EXECUTION_EVENTS: collection}
    published = AsyncMock(return_value="1-0")
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: mongo)
    monkeypatch.setattr("app.streams.producer.publish_live_event", published)
    # Since re-audit H6 the route also counts the result into the live-run
    # state hash, which stores the current test's name. That is a third
    # place event data lands in Redis, so it is held to the same rule as the
    # other two below: record what it is given, and prove it was sanitized.
    counted = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.record_test_event", counted
    )

    event = {
        "type": "test_result",
        "run_id": "payload-must-not-override-path",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
        **NESTED_SECRETS,
    }
    # The route now authenticates (re-audit H1). A direct call bypasses the
    # router dependency entirely, so the credential is passed explicitly —
    # otherwise the FastAPI Header default object arrives instead of None and
    # is read as a supplied API key.
    from app.core.config import settings

    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", False)
    await live.ingest_live_event(
        "path-run",
        event,
        x_api_key=None,
        x_webhook_secret=settings.WEBHOOK_SECRET,
        db=SimpleNamespace(),
    )

    mongo_event = collection.insert_one.await_args.args[0]
    redis_event = published.await_args.args[1]
    _assert_failure_fields_are_safe(mongo_event)
    _assert_failure_fields_are_safe(redis_event)
    _assert_no_raw_canaries(mongo_event)
    _assert_no_raw_canaries(redis_event)

    # The counting write sees the SANITIZED event, never the raw one.
    counted.assert_awaited_once()
    count_args = counted.await_args
    counted_name = count_args.args[2] if len(count_args.args) > 2 else count_args.kwargs.get("test_name")
    assert counted_name == str(redis_event.get("test_name") or ""), (
        "the live-state counter was given a test name that differs from the "
        "sanitized published event — it is reading the raw payload"
    )
    _assert_no_raw_canaries({"test_name": counted_name})
    assert mongo_event["run_id"] == "path-run"
    assert (
        mongo_event[LIVE_SANITIZATION_VERSION_FIELD]
        == LIVE_SANITIZATION_VERSION
    )


@pytest.mark.asyncio
async def test_close_session_sanitizes_legacy_buffer_before_event_archive(monkeypatch):
    from app.services import stream_service

    session_id = uuid.uuid4()
    project_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    session = SimpleNamespace(
        id=session_id,
        project_id=project_id,
        run_id=run_id,
        client_name="sdk",
        framework="pytest",
        branch="main",
        commit_hash="abc",
        build_number="m11",
        release_name=None,
        status="active",
        extra_metadata={},
        events_received=0,
        completed_at=None,
        started_at=None,
    )
    run = SimpleNamespace(id=uuid.UUID(run_id), event_archive=None, event_archive_at=None)
    result = SimpleNamespace(scalar_one_or_none=lambda: run)
    db = SimpleNamespace(
        get=AsyncMock(return_value=session),
        execute=AsyncMock(return_value=result),
    )
    redis = SimpleNamespace(eval=AsyncMock(return_value=[b"closing", b'{"total":1,"failed":1,"passed":0,"skipped":0,"broken":0,"unknown":0,"events_received":1}', b"1"]), lrange=AsyncMock(return_value=[json.dumps({
        "test_name": "checkout",
        "error_message": RAW_ERROR,
        "stack_trace": RAW_STACK,
    })]))

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "upsert_test_run", new=AsyncMock()),
        patch(
            "app.streams.live_run_state.RedisLiveRunState.complete",
            new=AsyncMock(return_value={"total": 1, "failed": 1}),
        ),
        patch(
            "app.services.release_linker.link_run_or_default",
            new=AsyncMock(),
        ),
        patch(
            "app.services.run_downstream_outbox.stage_live_persist_operation",
            new=AsyncMock(),
        ),
    ):
        await stream_service.close_session(db, str(session_id))

    assert len(run.event_archive) == 1
    _assert_failure_fields_are_safe(run.event_archive[0])


@pytest.mark.asyncio
async def test_predeployment_raw_stream_record_is_sanitized_before_dlq(
    monkeypatch,
    caplog,
):
    from app.streams import live_consumer

    redis = SimpleNamespace(xadd=AsyncMock(return_value="1-0"))
    monkeypatch.setattr(live_consumer, "get_redis", lambda: redis)
    caplog.set_level("ERROR", logger="app.streams.live_consumer")
    raw_record = {
        "run_id": "run-1",
        "event_type": "test_result",
        "payload": json.dumps({
            "test_name": "checkout",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        }),
    }

    await live_consumer.LiveEventStreamConsumer()._move_to_dlq(
        "1-0",
        raw_record,
        "consumer failed password=Admin123!",
        3,
    )

    dlq_fields = redis.xadd.await_args.args[1]
    original_data = json.loads(dlq_fields["original_data"])
    payload = json.loads(original_data["payload"])
    _assert_failure_fields_are_safe(payload)
    _assert_no_raw_canaries(original_data)
    assert "Admin123!" not in dlq_fields["error"]
    assert "Admin123!" not in caplog.text


def test_legacy_postgres_archive_scrub_is_idempotent_and_preserves_order():
    from app.services.live_persistence_scrub import sanitize_event_archive

    archive = [
        {
            "test_name": "first",
            "error_message": RAW_ERROR,
            **NESTED_SECRETS,
        },
        {
            "test_name": "second",
            "stack_trace": RAW_STACK,
        },
    ]

    safe = sanitize_event_archive(archive)

    assert [event["test_name"] for event in safe] == ["first", "second"]
    _assert_no_raw_canaries(safe)
    assert sanitize_event_archive(safe) == safe


def test_legacy_archive_and_buffer_scalars_become_safe_mapping_placeholders():
    from app.services.live_persistence_scrub import (
        sanitize_buffer_entry,
        sanitize_event_archive,
    )

    placeholder = {"_redacted": "[REDACTED]"}
    assert sanitize_event_archive([1, "raw"]) == [placeholder, placeholder]
    assert json.loads(sanitize_buffer_entry("1")) == placeholder
    assert json.loads(sanitize_buffer_entry("not-json")) == placeholder


@pytest.mark.asyncio
async def test_legacy_postgres_scrub_updates_one_bounded_keyset_page():
    from app.services.live_persistence_scrub import scrub_postgres_archive_batch

    first_id = uuid.UUID(int=1)
    second_id = uuid.UUID(int=2)
    runs = [
        SimpleNamespace(
            id=first_id,
            event_archive=[{"test_name": "first", "error_message": RAW_ERROR}],
        ),
        SimpleNamespace(
            id=second_id,
            event_archive=[{"test_name": "second", "stack_trace": RAW_STACK}],
        ),
    ]
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: runs),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    count, cursor = await scrub_postgres_archive_batch(
        db,
        after_id=first_id,
        batch_size=2,
    )

    assert (count, cursor) == (2, second_id)
    _assert_no_raw_canaries([run.event_archive for run in runs])


@pytest.mark.asyncio
async def test_legacy_live_test_case_scrub_sanitizes_failure_evidence_and_tags():
    from app.services.live_persistence_scrub import scrub_postgres_test_case_batch

    first_id = uuid.UUID(int=1)
    second_id = uuid.UUID(int=2)
    rows = [
        SimpleNamespace(
            id=first_id,
            error_message=RAW_ERROR,
            stack_trace=RAW_STACK,
            tags=NESTED_SECRETS["tags"],
        ),
        SimpleNamespace(
            id=second_id,
            error_message=None,
            stack_trace=None,
            tags=["safe"],
        ),
    ]
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    count, cursor = await scrub_postgres_test_case_batch(
        db,
        after_id=first_id,
        batch_size=2,
    )

    assert (count, cursor) == (2, second_id)
    _assert_no_raw_canaries([
        (row.error_message, row.stack_trace, row.tags) for row in rows
    ])


@pytest.mark.asyncio
async def test_m11_rebuild_replaces_search_index_with_sanitized_rows(monkeypatch):
    from app.services import semantic_search

    collection = SimpleNamespace(upsert=MagicMock(), count=MagicMock(return_value=1))
    client = SimpleNamespace(
        list_collections=MagicMock(return_value=[
            SimpleNamespace(name="test_case_search"),
            SimpleNamespace(name="unrelated"),
        ]),
        delete_collection=MagicMock(),
        get_or_create_collection=MagicMock(return_value=collection),
    )
    monkeypatch.setattr(
        semantic_search,
        "_get_chroma_client",
        AsyncMock(return_value=client),
    )
    monkeypatch.setattr(semantic_search, "_update_cursor", MagicMock())
    row = SimpleNamespace(
        id=uuid.UUID(int=1),
        test_name="checkout",
        suite_name="inactive-project-suite",
        error_message=RAW_ERROR,
        status="FAILED",
        test_run_id=uuid.UUID(int=2),
        project_id=uuid.UUID(int=3),
        created_at=None,
        step_text=(
            "assert password=abc Authorization: Basic dXNlcjpwYXNz "
            "from 2001:db8::1 owner@example.com"
        ),
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [row]),
        SimpleNamespace(all=lambda: []),
    ]))

    count = await semantic_search.rebuild_test_case_search_for_m11(
        db,
        batch_size=1,
    )

    assert count == 1
    client.delete_collection.assert_called_once_with(name="test_case_search")
    document = collection.upsert.call_args.kwargs["documents"][0]
    assert "admin@corp.example" not in document
    assert "Admin123!" not in document
    assert "password=abc" not in document
    assert "dXNlcjpwYXNz" not in document
    assert "2001:db8::1" not in document
    assert "owner@example.com" not in document


@pytest.mark.asyncio
async def test_m11_purges_global_and_project_semantic_caches(monkeypatch):
    from app.services import semantic_cache

    client = SimpleNamespace(
        list_collections=MagicMock(return_value=[
            SimpleNamespace(name="ai_analysis_cache"),
            SimpleNamespace(name="ai_analysis_cache_project-1"),
            SimpleNamespace(name="agent_memory_project-1"),
        ]),
        delete_collection=MagicMock(),
    )
    monkeypatch.setattr(
        semantic_cache,
        "_get_chroma_client",
        AsyncMock(return_value=client),
    )

    count = await semantic_cache.purge_all_m11_semantic_cache_collections()

    assert count == 2
    assert client.delete_collection.call_args_list == [
        call(name="ai_analysis_cache"),
        call(name="ai_analysis_cache_project-1"),
    ]


@pytest.mark.asyncio
async def test_semantic_cache_store_uses_m11_failure_sanitizer(monkeypatch):
    from app.services import semantic_cache

    collection = SimpleNamespace(
        upsert=MagicMock(),
        count=MagicMock(return_value=1),
    )
    monkeypatch.setattr(
        semantic_cache,
        "_get_or_create_collection",
        AsyncMock(return_value=collection),
    )

    await semantic_cache.semantic_cache_store(
        "checkout",
        "password=abc Authorization: Basic dXNlcjpwYXNz",
        "request from 2001:db8::1",
        {"failure_category": "PRODUCT_BUG", "confidence_score": 80},
        project_id="project-1",
    )

    document = collection.upsert.call_args.kwargs["documents"][0]
    assert "password=abc" not in document
    assert "dXNlcjpwYXNz" not in document
    assert "2001:db8::1" not in document


class _MongoCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def __aiter__(self):
        async def _iterate():
            for document in self.documents:
                yield document
        return _iterate()

    async def to_list(self, *, length):
        return self.documents[:length]


@pytest.mark.asyncio
async def test_legacy_mongo_scrub_handles_mixed_ids_and_forged_marker():
    from app.services.live_persistence_scrub import (
        M11_MONGO_MARKER,
        M11_SANITIZATION_VERSION,
        scrub_mongo_live_events,
    )

    documents = [
        {
            "_id": document_id,
            "_m11_sanitization_version": 1,
            "run_id": "run-1",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        }
        for document_id in ("mongo-1", 7, uuid.UUID(int=3))
    ]
    collection = SimpleNamespace(
        find=MagicMock(return_value=_MongoCursor(documents)),
        bulk_write=AsyncMock(),
    )

    count = await scrub_mongo_live_events(collection, batch_size=2)

    assert count == 3
    collection.find.assert_called_once_with({})
    assert [len(call.args[0]) for call in collection.bulk_write.await_args_list] == [2, 1]
    replacements = [
        operation._doc
        for call in collection.bulk_write.await_args_list
        for operation in call.args[0]
    ]
    assert [row["_id"] for row in replacements] == [row["_id"] for row in documents]
    assert all(
        row[M11_MONGO_MARKER] == M11_SANITIZATION_VERSION
        for row in replacements
    )
    for replacement in replacements:
        _assert_failure_fields_are_safe(replacement)
    _assert_no_raw_canaries(replacements)


@pytest.mark.asyncio
async def test_drained_live_stream_purge_recreates_group_without_replay():
    from app.services.live_persistence_scrub import purge_drained_live_stream

    redis = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        xinfo_groups=AsyncMock(return_value=[{
            "name": "live-consumers",
            "pending": 0,
            "lag": 0,
        }]),
        xlen=AsyncMock(return_value=2),
        delete=AsyncMock(),
        xgroup_create=AsyncMock(),
    )
    redis.pipeline = lambda **_kwargs: _WatchPipeline(redis)

    count = await purge_drained_live_stream(
        redis,
        "live-stream",
        group_name="live-consumers",
    )

    assert count == 2
    redis.delete.assert_awaited_once_with("live-stream")
    redis.xgroup_create.assert_awaited_once_with(
        "live-stream",
        "live-consumers",
        id="$",
        mkstream=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "lag"),
    [(1, 0), (0, 1), (0, None)],
)
async def test_live_stream_purge_refuses_pending_or_undelivered_events(
    pending,
    lag,
):
    from app.services.live_persistence_scrub import purge_drained_live_stream

    redis = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        xinfo_groups=AsyncMock(return_value=[{
            "name": "live-consumers",
            "pending": pending,
            "lag": lag,
        }]),
        delete=AsyncMock(),
    )
    redis.pipeline = lambda **_kwargs: _WatchPipeline(redis)

    with pytest.raises(RuntimeError):
        await purge_drained_live_stream(
            redis,
            "live-stream",
            group_name="live-consumers",
        )
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_stream_purge_aborts_on_watched_source_change():
    from app.services.live_persistence_scrub import purge_drained_live_stream

    redis = SimpleNamespace(
        watch_error=True,
        exists=AsyncMock(return_value=True),
        xinfo_groups=AsyncMock(return_value=[{
            "name": b"live-consumers",
            "pending": 0,
            "lag": 0,
        }]),
        xlen=AsyncMock(return_value=2),
        delete=AsyncMock(),
        xgroup_create=AsyncMock(),
    )
    redis.pipeline = lambda **_kwargs: _WatchPipeline(redis)

    with pytest.raises(RuntimeError, match="changed during cutover"):
        await purge_drained_live_stream(
            redis,
            "live-stream",
            group_name="live-consumers",
        )

    redis.delete.assert_not_awaited()
    redis.xgroup_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconsumed_stream_scrub_preserves_ids_and_sanitizes_records():
    from app.services.live_persistence_scrub import scrub_unconsumed_redis_stream

    raw_fields = {
        "run_id": "run-1",
        "event_type": "test_result",
        "payload": json.dumps({
            "test_name": "checkout",
            "error_message": RAW_ERROR,
            "stack_trace": RAW_STACK,
            **NESTED_SECRETS,
        }),
    }
    redis = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        xinfo_groups=AsyncMock(return_value=[]),
        xrevrange=AsyncMock(return_value=[("2-0", raw_fields)]),
        xrange=AsyncMock(side_effect=[
            [("1-0", raw_fields), ("2-0", raw_fields)],
            [],
        ]),
        xadd=AsyncMock(),
        pexpire=AsyncMock(),
        persist=AsyncMock(),
        rename=AsyncMock(),
        delete=AsyncMock(),
    )
    redis.pipeline = lambda **_kwargs: _WatchPipeline(redis)

    count = await scrub_unconsumed_redis_stream(
        redis,
        "dlq-stream",
        batch_size=2,
    )

    assert count == 2
    assert [call.kwargs["id"] for call in redis.xadd.await_args_list] == [
        "1-0",
        "2-0",
    ]
    replacements = [call.args[1] for call in redis.xadd.await_args_list]
    _assert_no_raw_canaries(replacements)
    for replacement in replacements:
        _assert_failure_fields_are_safe(json.loads(replacement["payload"]))
    redis.rename.assert_awaited_once()


@pytest.mark.asyncio
async def test_unconsumed_stream_scrub_cleans_temp_on_watched_change():
    from app.services.live_persistence_scrub import scrub_unconsumed_redis_stream

    raw_fields = {"payload": json.dumps({"error_message": RAW_ERROR})}
    redis = SimpleNamespace(
        watch_error=True,
        exists=AsyncMock(return_value=True),
        xinfo_groups=AsyncMock(return_value=[]),
        xrevrange=AsyncMock(return_value=[("1-0", raw_fields)]),
        xrange=AsyncMock(side_effect=[[("1-0", raw_fields)], []]),
        xadd=AsyncMock(),
        pexpire=AsyncMock(),
        persist=AsyncMock(),
        rename=AsyncMock(),
        delete=AsyncMock(),
    )
    redis.pipeline = lambda **_kwargs: _WatchPipeline(redis)

    with pytest.raises(RuntimeError, match="changed during cutover"):
        await scrub_unconsumed_redis_stream(redis, "dlq-stream", batch_size=1)

    redis.rename.assert_not_awaited()
    redis.delete.assert_awaited_once()


class _RedisList:
    def __init__(self, key: str, rows: list[str]):
        self.values = {key: list(rows)}
        self.ttls = {key: 90_000}
        self.watch_error = False

    async def pttl(self, key):
        return self.ttls.get(key, -2)

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def pexpire(self, key, ttl):
        self.ttls[key] = ttl

    async def persist(self, key):
        self.ttls[key] = -1

    async def lrange(self, key, start, end):
        return self.values.get(key, [])[start:end + 1]

    async def rpush(self, key, *rows):
        self.values.setdefault(key, []).extend(rows)

    async def rename(self, source, target):
        self.values[target] = self.values.pop(source)
        if source in self.ttls:
            self.ttls[target] = self.ttls.pop(source)

    def pipeline(self, **_kwargs):
        return _WatchPipeline(self)


@pytest.mark.asyncio
async def test_legacy_redis_list_scrub_preserves_order_cardinality_and_ttl():
    from app.services.live_persistence_scrub import scrub_redis_list

    key = "testlookup:live:testcases:run-1"
    raw_rows = [
        json.dumps({"test_name": "first", "error_message": RAW_ERROR}),
        json.dumps({"test_name": "second", "stack_trace": RAW_STACK}),
    ]
    redis = _RedisList(key, raw_rows)

    count = await scrub_redis_list(redis, key, batch_size=1)

    assert count == 2
    assert redis.ttls[key] == 90_000
    safe_rows = [json.loads(row) for row in redis.values[key]]
    assert [row["test_name"] for row in safe_rows] == ["first", "second"]
    _assert_no_raw_canaries(safe_rows)


@pytest.mark.asyncio
async def test_legacy_redis_list_scrub_preserves_source_on_watched_change():
    from app.services.live_persistence_scrub import scrub_redis_list

    key = "testlookup:live:testcases:run-1"
    raw_rows = [json.dumps({"error_message": RAW_ERROR})]
    redis = _RedisList(key, raw_rows)
    redis.watch_error = True

    with pytest.raises(RuntimeError, match="changed during cutover"):
        await scrub_redis_list(redis, key, batch_size=1)

    assert redis.values[key] == raw_rows
    assert all(not temp.startswith(f"{key}:m11-scrub:") for temp in redis.values)


@pytest.mark.asyncio
async def test_live_batch_real_redis_test_skips_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("TESTLOOKUP_RUN_REDIS_INTEGRATION", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with pytest.raises(pytest.skip.Exception, match="not enabled"):
        await test_live_batch_persists_only_sanitized_evidence_in_real_redis(
            monkeypatch,
        )


class _PingFailsClient:
    def __init__(self):
        self.closed = False

    async def ping(self):
        raise ConnectionError("no redis here")

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_live_batch_real_redis_test_fails_when_opted_in_and_unreachable(
    monkeypatch,
):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = _PingFailsClient()
    monkeypatch.setenv("TESTLOOKUP_RUN_REDIS_INTEGRATION", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_asyncio.Redis, "from_url", lambda *args, **kwargs: client)

    with pytest.raises(ConnectionError, match="no redis here"):
        await test_live_batch_persists_only_sanitized_evidence_in_real_redis(
            monkeypatch,
        )

    assert client.closed
