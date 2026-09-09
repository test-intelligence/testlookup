"""Cutover guards for the stable, durable live-evidence protocol."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


class _MemoryPipeline:
    def __init__(self, redis: "_MemoryRedis") -> None:
        self.redis = redis
        self.in_multi = False
        self.commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def watch(self, *_keys):
        return None

    def multi(self):
        self.in_multi = True

    async def llen(self, *args, **kwargs):
        return await self.redis.llen(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self.redis.get(*args, **kwargs)

    async def lrange(self, *args, **kwargs):
        return await self.redis.lrange(*args, **kwargs)

    async def exists(self, *args, **kwargs):
        return await self.redis.exists(*args, **kwargs)

    async def xinfo_groups(self, *args, **kwargs):
        return await self.redis.xinfo_groups(*args, **kwargs)

    async def xrevrange(self, *args, **kwargs):
        return await self.redis.xrevrange(*args, **kwargs)

    async def hget(self, *args, **kwargs):
        return await self.redis.hget(*args, **kwargs)

    async def scard(self, *args, **kwargs):
        return await self.redis.scard(*args, **kwargs)

    def __getattr__(self, name: str):
        if not self.in_multi:
            raise AttributeError(name)

        def _queue(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self):
        self.redis.execute_count += 1
        if self.redis.fail_execute_number == self.redis.execute_count:
            from redis.exceptions import WatchError

            raise WatchError("simulated concurrent mutation")
        results = []
        for name, args, kwargs in self.commands:
            results.append(await getattr(self.redis, name)(*args, **kwargs))
        return results


class _MemoryRedis:
    def __init__(self, list_key: str, rows: list[str]) -> None:
        self.lists = {list_key: list(rows)}
        self.values: dict[str, str] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.groups: dict[str, list[dict[str, Any]]] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.sets: dict[str, set[str]] = {}
        self.persisted: set[str] = set()
        self.expirations: dict[str, int] = {}
        self.execute_count = 0
        self.fail_execute_number: int | None = None

    def pipeline(self, **_kwargs):
        return _MemoryPipeline(self)

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def get(self, key):
        return self.values.get(key)

    async def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]

    async def exists(self, key):
        return int(key in self.lists or key in self.streams or key in self.values)

    async def xinfo_groups(self, key):
        return self.groups.get(key, [])

    async def xadd(self, key, fields, id=None):
        self.streams.setdefault(key, []).append(dict(fields))
        return id or f"{len(self.streams[key])}-0"

    async def xrevrange(self, key, count=1):
        rows = self.streams.get(key, [])
        return [] if not rows else [(f"{len(rows)}-0", rows[-1])]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hincrby(self, key, field, amount):
        value = int(self.hashes.setdefault(key, {}).get(field, 0)) + amount
        self.hashes[key][field] = value
        return value

    async def sadd(self, key, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(values)
        return len(target) - before

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def set(self, key, value):
        self.values[key] = value
        return True

    async def persist(self, key):
        self.persisted.add(key)
        return True

    async def expire(self, _key, _seconds):
        self.expirations[_key] = _seconds
        return True

    async def delete(self, key):
        removed = int(
            key in self.lists or key in self.streams or key in self.values
        )
        self.lists.pop(key, None)
        self.streams.pop(key, None)
        self.values.pop(key, None)
        return removed


@pytest.mark.asyncio
async def test_legacy_list_migration_is_sanitized_stable_and_idempotent():
    from app.services.live_persistence_scrub import (
        migrate_redis_list_to_evidence_stream,
    )

    list_key = "testlookup:live:testcases:run-1"
    stream_key = "testlookup:live:evidence:run-1"
    rows = [
        json.dumps({"test_name": "a", "error_message": "password=secret"}),
        json.dumps({"test_name": "b", "status": "PASSED"}),
        "malformed-json",
    ]
    redis = _MemoryRedis(list_key, rows)

    assert await migrate_redis_list_to_evidence_stream(
        redis,
        list_key,
        stream_key,
        run_id="run-1",
        batch_size=2,
    ) == 3
    assert await migrate_redis_list_to_evidence_stream(
        redis,
        list_key,
        stream_key,
        run_id="run-1",
        batch_size=1,
    ) == 0

    entries = redis.streams[stream_key]
    assert len(entries) == 2
    assert [entry["event_count"] for entry in entries] == ["2", "1"]
    assert {entry["trim_legacy"] for entry in entries} == {"0"}
    canonical_payloads = []
    for entry in entries:
        events = json.loads(entry["events_json"])
        ids = json.loads(entry["event_ids_json"])
        assert len(events) == len(ids) == int(entry["event_count"])
        for index, event in enumerate(events):
            assert event.pop("run_id") == "run-1"
            canonical_payloads.append(event)
            expected = hashlib.sha256(
                f"{entry['session_id']}\0{entry['batch_id']}\0{index}".encode()
            ).hexdigest()
            assert ids[index] == expected
    assert "password=secret" not in json.dumps(entries)
    assert list_key in redis.lists
    assert list_key in redis.persisted


@pytest.mark.asyncio
async def test_legacy_list_cleanup_requires_acknowledged_drain():
    from app.services.live_persistence_scrub import (
        migrate_redis_list_to_evidence_stream,
        remove_drained_legacy_redis_list,
    )
    list_key = "testlookup:live:testcases:run-2"
    stream_key = "testlookup:live:evidence:run-2"
    redis = _MemoryRedis(list_key, [json.dumps({"test_name": "a"})])
    await migrate_redis_list_to_evidence_stream(
        redis,
        list_key,
        stream_key,
        run_id="run-2",
    )

    redis.groups[stream_key] = [{
        "name": "live-persistence-v1",
        "pending": 1,
        "lag": 0,
    }]
    with pytest.raises(RuntimeError, match="acknowledged"):
        await remove_drained_legacy_redis_list(
            redis,
            list_key,
            stream_key,
            group_name="live-persistence-v1",
        )
    assert list_key in redis.lists

    redis.groups[stream_key][0]["pending"] = 0
    redis.sets["testlookup:live:batch_state:run-2:pending"] = set()
    assert await remove_drained_legacy_redis_list(
        redis,
        list_key,
        stream_key,
        group_name="live-persistence-v1",
    ) == 1
    assert list_key not in redis.lists
    assert not any("legacy-list-migration" in key for key in redis.values)
    assert redis.expirations["testlookup:live:batch_state:run-2"] == 15 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_legacy_list_migration_resumes_after_an_interrupted_page():
    from app.services.live_persistence_scrub import (
        migrate_redis_list_to_evidence_stream,
    )

    list_key = "testlookup:live:testcases:run-interrupted"
    stream_key = "testlookup:live:evidence:run-interrupted"
    redis = _MemoryRedis(
        list_key,
        [json.dumps({"test_name": name}) for name in ("a", "b", "c")],
    )
    redis.fail_execute_number = 2

    with pytest.raises(RuntimeError, match="changed during migration"):
        await migrate_redis_list_to_evidence_stream(
            redis,
            list_key,
            stream_key,
            run_id="run-interrupted",
            batch_size=2,
        )
    assert len(redis.streams[stream_key]) == 1

    redis.fail_execute_number = None
    assert await migrate_redis_list_to_evidence_stream(
        redis,
        list_key,
        stream_key,
        run_id="run-interrupted",
        batch_size=2,
    ) == 1
    assert len(redis.streams[stream_key]) == 2
    ids = [
        event_id
        for entry in redis.streams[stream_key]
        for event_id in json.loads(entry["event_ids_json"])
    ]
    assert len(set(ids)) == 3


@pytest.mark.asyncio
async def test_cutover_scan_repeats_until_a_read_only_verification_pass(
    monkeypatch,
):
    from scripts import sanitize_live_persistence as cutover

    class _ScanRedis:
        def __init__(self):
            self.scans = 0

        def scan_iter(self, **_kwargs):
            self.scans += 1

            async def _rows():
                yield b"testlookup:live:testcases:run-3"

            return _rows()

    redis = _ScanRedis()
    migrate = AsyncMock(side_effect=[2, 0])
    monkeypatch.setattr(cutover, "get_redis", lambda: redis)
    monkeypatch.setattr(cutover, "migrate_redis_list_to_evidence_stream", migrate)

    assert await cutover._scrub_redis(10, legacy_lists_only=True) == (0, 2)
    assert redis.scans == 2
    assert migrate.await_count == 2


@pytest.mark.asyncio
async def test_cutover_cleanup_repeats_scan_after_deleting_keys(monkeypatch):
    from scripts import sanitize_live_persistence as cutover

    class _ScanRedis:
        def __init__(self):
            self.scans = 0

        def scan_iter(self, **_kwargs):
            self.scans += 1
            scan = self.scans

            async def _rows():
                if scan == 1:
                    yield "testlookup:live:testcases:run-4"

            return _rows()

    redis = _ScanRedis()
    cleanup = AsyncMock(return_value=1)
    monkeypatch.setattr(cutover, "get_redis", lambda: redis)
    monkeypatch.setattr(cutover, "remove_drained_legacy_redis_list", cleanup)

    assert await cutover._scrub_redis(
        10,
        cleanup_drained_legacy_lists=True,
    ) == (0, 1)
    assert redis.scans == 2
    cleanup.assert_awaited_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_legacy_list_migration_is_atomic_on_real_redis():
    if os.environ.get("TESTLOOKUP_RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("real Redis integration tests are not enabled")
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.fail("REDIS_URL must be configured for real Redis integration tests")
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app.services.live_persistence_scrub import (
        migrate_redis_list_to_evidence_stream,
        remove_drained_legacy_redis_list,
    )
    from app.streams import LIVE_BATCH_DEDUP_KEY

    suffix = uuid.uuid4().hex
    run_id = f"run-{suffix}"
    list_key = f"testlookup:live:testcases:{run_id}"
    stream_key = f"testlookup:live:evidence:{run_id}"
    group = "live-persistence-v1"
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        raise
    try:
        await client.rpush(
            list_key,
            json.dumps({"test_name": "a", "error_message": "token=secret"}),
            json.dumps({"test_name": "b", "status": "PASSED"}),
        )
        await client.expire(list_key, 60)
        assert await migrate_redis_list_to_evidence_stream(
            client,
            list_key,
            stream_key,
            run_id=run_id,
            batch_size=1,
        ) == 2
        assert await migrate_redis_list_to_evidence_stream(
            client,
            list_key,
            stream_key,
            run_id=run_id,
            batch_size=2,
        ) == 0
        assert await client.xlen(stream_key) == 2
        assert await client.ttl(list_key) == -1

        await client.xgroup_create(stream_key, group, id="0")
        delivery = await client.xreadgroup(
            group,
            "cutover-test",
            {stream_key: ">"},
            count=10,
        )
        ids = [message_id for _key, rows in delivery for message_id, _ in rows]
        event_ids = [
            event_id
            for _key, rows in delivery
            for _message_id, fields in rows
            for event_id in json.loads(fields["event_ids_json"])
        ]
        assert await client.xack(stream_key, group, *ids) == 2
        # XACK only records Redis delivery.  The production drainer clears
        # these identities after the matching PostgreSQL receipt commits; the
        # cutover must refuse to delete the legacy source until that durable
        # projection watermark has advanced.
        dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
        assert await client.srem(f"{dedupe_key}:pending", *event_ids) == 2
        assert await remove_drained_legacy_redis_list(
            client,
            list_key,
            stream_key,
            group_name=group,
        ) == 1
        assert not await client.exists(list_key)
    finally:
        keys = [
            key
            async for key in client.scan_iter(
                match=f"testlookup:live:*:{run_id}*",
                count=100,
            )
        ]
        if keys:
            await client.delete(*keys)
        await client.aclose()


def _redis_command_from_compose(path: Path) -> str:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    redis = document["services"]["redis"]
    assert redis["volumes"] == ["redis_data:/data"]
    return redis["command"]


@pytest.mark.parametrize(
    "compose_path",
    [ROOT / "docker-compose.yml", ROOT / "docker-compose.release.yml"],
)
def test_compose_redis_is_durable_and_does_not_evict_evidence(compose_path):
    command = _redis_command_from_compose(compose_path)
    assert "--appendonly yes" in command
    assert "--appendfsync everysec" in command
    assert "--maxmemory-policy volatile-lru" in command


@pytest.mark.parametrize(
    ("manifest", "storage_class"),
    [
        (ROOT / "k8s/overlays/homelab/infra-redis.yaml", "local-path"),
        (
            ROOT / "k8s/overlays/openshift-artifactory/infra-redis.yaml",
            "STORAGE_CLASS_PLACEHOLDER",
        ),
    ],
)
def test_kubernetes_redis_has_aof_non_evictable_evidence_and_pvc(
    manifest,
    storage_class,
):
    resources = {
        item["kind"]: item
        for item in yaml.safe_load_all(manifest.read_text(encoding="utf-8"))
        if item
    }
    deployment = resources["Deployment"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    command = container["command"]
    assert command[command.index("--appendonly") + 1] == "yes"
    assert command[command.index("--appendfsync") + 1] == "everysec"
    assert command[command.index("--maxmemory-policy") + 1] == "volatile-lru"
    assert container["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    assert deployment["spec"]["template"]["spec"]["volumes"] == [{
        "name": "data",
        "persistentVolumeClaim": {"claimName": "redis-data"},
    }]
    pvc = resources["PersistentVolumeClaim"]
    assert pvc["spec"]["storageClassName"] == storage_class
    assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"


def test_standard_openshift_declares_external_durable_redis_contract():
    kustomization = (
        ROOT / "k8s/overlays/openshift/kustomization.yaml"
    ).read_text(encoding="utf-8")
    runbook = (
        ROOT / "deploy/H02_H03_STABLE_LIVE_CUTOVER.md"
    ).read_text(encoding="utf-8")
    assert "does not deploy Redis" in kustomization
    for requirement in (
        "authenticated, TLS-protected Redis",
        "append-only persistence",
        "backups",
        "point-in-time recovery",
        "cannot evict TTL-less evidence/broker keys",
    ):
        assert requirement in runbook


def test_cutover_runbook_orders_consumers_migration_drain_cleanup_producers():
    runbook = (
        ROOT / "deploy/H02_H03_STABLE_LIVE_CUTOVER.md"
    ).read_text(encoding="utf-8")
    positions = [
        runbook.index("Apply the new consumer/drainer image first"),
        runbook.index("migrate all frozen legacy LISTs"),
        runbook.index("Start only the new persistence consumers"),
        runbook.index("Remove the legacy LISTs"),
        runbook.index("Deploy and resume the new producers"),
    ]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_legacy_list_migration_real_redis_test_skips_without_explicit_opt_in(
    monkeypatch,
):
    monkeypatch.delenv("TESTLOOKUP_RUN_REDIS_INTEGRATION", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with pytest.raises(pytest.skip.Exception, match="not enabled"):
        await test_legacy_list_migration_is_atomic_on_real_redis()


class _PingFailsClient:
    def __init__(self):
        self.closed = False

    async def ping(self):
        raise ConnectionError("no redis here")

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_legacy_list_migration_real_redis_test_fails_when_opted_in_and_unreachable(
    monkeypatch,
):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = _PingFailsClient()
    monkeypatch.setenv("TESTLOOKUP_RUN_REDIS_INTEGRATION", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_asyncio.Redis, "from_url", lambda *args, **kwargs: client)

    with pytest.raises(ConnectionError, match="no redis here"):
        await test_legacy_list_migration_is_atomic_on_real_redis()

    assert client.closed
