"""W34 STRAT-13 — the drop-folder sweep's glue without Redis, Postgres, S3 or Celery.

``tasks._drop_folder_watcher_sweep`` runs on a scripted world: a session
factory that counts open sessions, an in-memory ``drop_folder_builds`` table
and baseline (the service's staged-SQL functions replaced by faithful fakes --
the real SQL runs in ``integration/test_drop_folder_watcher_postgres.py``), a
fake object store that FAILS if it is called while a session is open, a
scripted lock and flag, and a fake ``ingest_test_run.delay``. Each rule of the
contract (§1 STRAT-13) is asserted on its own, with a controllable clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.storage import ObjectHead
from app.services import drop_folder_watcher as dfw
from app.worker import tasks

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
PID = uuid.UUID("0b6f0b5e-2a49-4d55-9c39-2f7a5a1f7c10")
SLUG = "auth-service"


class _World:
    def __init__(self) -> None:
        self.projects: list[tuple[uuid.UUID, str]] = [(PID, SLUG)]
        self.open = self.max_open = self.sessions = self.commits = 0
        self.lock = "free"                       # "free" | "held" | "raise"
        self.released: list[tuple[str, str]] = []
        self.flag_any = True
        self.flag_off_for: set[uuid.UUID] = set()
        self.rows: dict[str, dict[str, Any]] = {}
        self.baseline: datetime | None = None

    def factory(self) -> "_Session":
        return _Session(self)


class _Session:
    def __init__(self, world: _World) -> None:
        self.world = world

    async def __aenter__(self) -> "_Session":
        self.world.open += 1
        self.world.sessions += 1
        self.world.max_open = max(self.world.max_open, self.world.open)
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.world.open -= 1
        return False

    async def commit(self) -> None:
        self.world.commits += 1


class _Storage:
    """Objects as key -> LastModified. A folder is listed while any object is below it."""

    def __init__(self, world: _World) -> None:
        self.world = world
        self.objects: dict[str, datetime] = {}
        self.listed: list[str] = []
        self.heads: list[str] = []
        self.fail_listing: set[str] = set()

    def drop(self, key: str, at: datetime) -> None:
        self.objects[key] = at

    async def list_common_prefixes(self, prefix: str, bucket: str | None = None) -> list[str]:
        assert self.world.open == 0, "a storage call with a DB session open"
        self.listed.append(prefix)
        if prefix in self.fail_listing:
            raise ConnectionError("store down")
        found = {prefix + k[len(prefix):].split("/", 1)[0] + "/" for k in self.objects
                 if k.startswith(prefix) and "/" in k[len(prefix):]}
        return sorted(found)

    async def head_object(self, key: str, bucket: str | None = None) -> ObjectHead | None:
        assert self.world.open == 0, "a storage call with a DB session open"
        self.heads.append(key)
        at = self.objects.get(key)
        return None if at is None else ObjectHead(key=key, etag=f"etag-{key}", size=2, last_modified=at)

    async def list_objects(self, *_: Any, **__: Any) -> list:      # pragma: no cover - must never be called
        raise AssertionError("the watcher must never list objects")


class _Delay:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False

    def delay(self, **kwargs: Any) -> SimpleNamespace:
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.calls)}")


def _session_open(world: _World, db: Any) -> None:
    assert isinstance(db, _Session) and world.open == 1


def _install_fake_sql(monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
    async def load_projects(db: Any) -> list:
        _session_open(world, db)
        return list(world.projects)

    async def ensure_baseline(db: Any, now: datetime) -> datetime:
        _session_open(world, db)
        world.baseline = world.baseline or now
        return world.baseline

    async def abandon_stale_pending(db: Any, now: datetime) -> int:
        _session_open(world, db)
        stale = [r for r in world.rows.values()
                 if r["status"] == "pending" and r["first_seen_at"] <= now - dfw.PENDING_WINDOW]
        for row in stale:
            row.update(status="abandoned", last_checked_at=now)
        return len(stale)

    async def known_builds(db: Any, prefixes: list[str]) -> dict:
        _session_open(world, db)
        return {p: dfw.KnownBuild(world.rows[p]["status"], world.rows[p]["first_seen_at"],
                                  world.rows[p]["last_checked_at"])
                for p in prefixes if p in world.rows}

    async def record_decisions(db: Any, decisions: list, now: datetime) -> None:
        _session_open(world, db)
        for d in decisions:
            row = world.rows.get(d.build_prefix)
            if row is not None and row["status"] != "pending":
                continue                                   # a terminal row is never rewritten
            first = row["first_seen_at"] if row else now
            world.rows[d.build_prefix] = {
                "status": d.status, "first_seen_at": first, "last_checked_at": now, "task_id": d.task_id,
                "queued_at": d.queued_at, "etag": d.sentinel_etag, "last_modified": d.sentinel_last_modified,
            }

    for name, fn in (("load_projects", load_projects), ("ensure_baseline", ensure_baseline),
                     ("abandon_stale_pending", abandon_stale_pending), ("known_builds", known_builds),
                     ("record_decisions", record_decisions)):
        monkeypatch.setattr(dfw, name, fn)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    warning = info

    def named(self, event: str) -> list[dict]:
        return [kw for name, kw in self.events if name == event]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from app.db import storage as storage_module
    from app.services import feature_flags

    world = _World()
    store = _Storage(world)
    delay = _Delay()
    log = _Log()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", world.factory, raising=False)
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: store)
    monkeypatch.setattr(tasks, "ingest_test_run", delay)
    monkeypatch.setattr(tasks, "_slog", log)
    _install_fake_sql(monkeypatch, world)

    async def _is_duplicate(key: str, ttl: int = 3600, owner: str | None = None) -> bool:
        assert key == dfw.LOCK_KEY and ttl == dfw.LOCK_TTL_SECONDS and owner
        if world.lock == "raise":
            raise ConnectionError("redis down")
        return world.lock == "held"

    async def _release(key: str, owner: str) -> None:
        world.released.append((key, owner))

    async def _is_enabled(key: str, *, db: Any = None, project_id: Any = None, any_scope: bool = False,
                          **_: Any) -> bool:
        assert key == "drop_folder_watcher"
        if any_scope:
            assert db is None and world.open == 0
            return world.flag_any
        _session_open(world, db)
        return world.flag_any and project_id not in world.flag_off_for

    monkeypatch.setattr(tasks, "_is_duplicate", _is_duplicate)
    monkeypatch.setattr(tasks, "_release_duplicate_lock", _release)
    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)

    async def sweep(at: datetime) -> dict:
        return await tasks._drop_folder_watcher_sweep(owner="tick", now=at)

    return SimpleNamespace(world=world, store=store, delay=delay, log=log, sweep=sweep)


def _sentinel(segment: str, build: str) -> str:
    return f"{segment}/runs/{build}/upload_complete.json"


def _result(segment: str, build: str) -> str:
    return f"{segment}/runs/{build}/allure/a-result.json"


# ── the rules ────────────────────────────────────────────────────────────────


async def test_a_new_sentinel_is_queued_once_and_a_second_sweep_queues_nothing(env) -> None:
    await env.sweep(T0)                                            # the first sweep: baseline = T0
    env.store.drop(_result(SLUG, "7"), T0 + timedelta(seconds=20))
    env.store.drop(_sentinel(SLUG, "7"), T0 + timedelta(seconds=30))
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert env.delay.calls == [{"sentinel_key": _sentinel(SLUG, "7"), "minio_prefix": f"{SLUG}/runs/7/"}]
    row = env.world.rows[f"{SLUG}/runs/7/"]
    assert (row["status"], row["task_id"], row["queued_at"]) == ("queued", "task-1", T0 + timedelta(minutes=1))
    assert row["last_modified"] == T0 + timedelta(seconds=30) and row["etag"] == f"etag-{_sentinel(SLUG, '7')}"
    assert out["queued"] == 1 and out["heads"] == 1 and out["capped"] is None
    assert env.log.named("drop_folder_queued") == [{"sentinel_key": _sentinel(SLUG, "7"), "task_id": "task-1"}]

    heads_before = len(env.store.heads)
    out = await env.sweep(T0 + timedelta(minutes=2))
    assert len(env.delay.calls) == 1 and out["queued"] == 0
    assert len(env.store.heads) == heads_before, "a queued folder is never HEADed again"
    assert env.world.max_open == 1 and env.world.released[-1] == (dfw.LOCK_KEY, "tick")


async def test_history_present_at_the_first_sweep_is_recorded_baseline_and_never_queued(env) -> None:
    env.store.drop(_result(SLUG, "1"), T0 - timedelta(days=30))
    env.store.drop(_sentinel(SLUG, "1"), T0 - timedelta(days=30))
    env.store.drop(_sentinel(str(PID), "2"), T0 - timedelta(seconds=1))
    out = await env.sweep(T0)
    assert env.world.baseline == T0 and env.delay.calls == []
    assert {p: r["status"] for p, r in env.world.rows.items()} == {
        f"{SLUG}/runs/1/": "baseline", f"{PID}/runs/2/": "baseline",
    }
    assert out["baseline"] == 2 and out["queued"] == 0
    await env.sweep(T0 + timedelta(hours=1))
    assert env.delay.calls == [] and len(env.store.heads) == 2


async def test_a_folder_without_a_sentinel_is_abandoned_after_24_hours(env) -> None:
    await env.sweep(T0)
    env.store.drop(_result(SLUG, "9"), T0)
    await env.sweep(T0 + timedelta(minutes=1))
    await env.sweep(T0 + timedelta(hours=23))
    assert env.world.rows[f"{SLUG}/runs/9/"]["status"] == "pending"
    assert env.world.rows[f"{SLUG}/runs/9/"]["first_seen_at"] == T0 + timedelta(minutes=1)
    assert env.store.heads.count(_sentinel(SLUG, "9")) == 2
    out = await env.sweep(T0 + timedelta(hours=24, minutes=1))
    assert out["abandoned"] == 1 and env.world.rows[f"{SLUG}/runs/9/"]["status"] == "abandoned"
    assert env.store.heads.count(_sentinel(SLUG, "9")) == 2, "an abandoned folder is not HEADed"
    # a sentinel written after the folder was abandoned is not picked up (use a new build number)
    env.store.drop(_sentinel(SLUG, "9"), T0 + timedelta(hours=25))
    await env.sweep(T0 + timedelta(hours=25, minutes=1))
    assert env.delay.calls == []


async def test_a_folder_whose_key_fails_the_sentinel_rule_is_refused_without_a_head(env) -> None:
    await env.sweep(T0)
    env.store.drop("auth-service/runs/a\\b/upload_complete.json", T0 + timedelta(seconds=5))
    env.store.drop("auth-service/runs/../upload_complete.json", T0 + timedelta(seconds=5))
    env.store.drop(_sentinel(SLUG, "ok"), T0 + timedelta(seconds=5))
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert env.world.rows["auth-service/runs/a\\b/"]["status"] == "refused"
    assert env.world.rows["auth-service/runs/../"]["status"] == "refused"
    assert env.store.heads == [_sentinel(SLUG, "ok")], "a refused key is never HEADed"
    assert [c["sentinel_key"] for c in env.delay.calls] == [_sentinel(SLUG, "ok")]
    assert out["refused"] == 2 and len(env.log.named("drop_folder_refused")) == 2
    await env.sweep(T0 + timedelta(minutes=2))
    assert len(env.log.named("drop_folder_refused")) == 2, "a refused folder is not examined again"


async def test_the_flag_off_makes_no_storage_call_and_opens_no_session(env) -> None:
    env.world.flag_any = False
    env.store.drop(_sentinel(SLUG, "7"), T0)
    assert await env.sweep(T0) == {"skipped": "flag_off"}
    assert env.store.listed == [] and env.store.heads == [] and env.delay.calls == []
    assert env.world.sessions == 0 and env.world.baseline is None
    assert env.world.released == [(dfw.LOCK_KEY, "tick")]


@pytest.mark.parametrize("lock, skipped", [("held", "already_running"), ("raise", "lock_unavailable")])
async def test_a_held_or_unavailable_lock_does_no_work(env, lock: str, skipped: str) -> None:
    env.world.lock = lock
    env.store.drop(_sentinel(SLUG, "7"), T0)
    assert await env.sweep(T0) == {"skipped": skipped}
    assert env.world.sessions == 0 and env.store.listed == [] and env.delay.calls == []
    assert env.world.released == [], "a lock this sweep does not own is never released"


async def test_both_the_slug_and_the_uuid_prefix_of_a_project_are_scanned(env) -> None:
    other = uuid.UUID(int=7)
    env.world.projects.append((other, "billing"))
    await env.sweep(T0)
    assert sorted(env.store.listed) == sorted([f"{PID}/runs/", f"{SLUG}/runs/", f"{other}/runs/", "billing/runs/"])
    env.store.drop(_sentinel(SLUG, "7"), T0 + timedelta(seconds=1))
    env.store.drop(_sentinel(str(PID), "8"), T0 + timedelta(seconds=1))
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert sorted(c["sentinel_key"] for c in env.delay.calls) == sorted([_sentinel(SLUG, "7"), _sentinel(str(PID), "8")])
    assert sorted(c["minio_prefix"] for c in env.delay.calls) == sorted([f"{SLUG}/runs/7/", f"{PID}/runs/8/"])
    assert out["projects"] == 2 and out["prefixes"] == 4


async def test_a_project_the_flag_is_off_for_is_not_scanned(env) -> None:
    other = uuid.UUID(int=7)
    env.world.projects.append((other, "billing"))
    env.world.flag_off_for = {PID}
    out = await env.sweep(T0)
    assert sorted(env.store.listed) == sorted([f"{other}/runs/", "billing/runs/"]) and out["projects"] == 1


async def test_the_new_folder_cap_leaves_the_rest_for_the_next_sweep(env, monkeypatch) -> None:
    monkeypatch.setattr(dfw, "MAX_NEW_CHECKS_PER_SWEEP", 3)
    env.world.projects = [(PID, str(PID))]                       # one prefix: a deterministic order
    await env.sweep(T0)
    for build in "abcde":
        env.store.drop(_result(str(PID), build), T0)
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert env.store.heads == [_sentinel(str(PID), b) for b in "abc"] and out["capped"] == "new_checks"
    assert env.log.named("drop_folder_sweep_capped") == [{"reason": "new_checks", "heads": 3, "queued": 0}]
    assert sorted(env.world.rows) == [f"{PID}/runs/{b}/" for b in "abc"]
    env.store.heads.clear()
    out = await env.sweep(T0 + timedelta(minutes=2))
    # the two new folders first, then the three pending ones on their own budget
    assert env.store.heads == [_sentinel(str(PID), b) for b in "deabc"] and out["capped"] is None
    assert len(env.world.rows) == 5


async def test_pending_folders_cannot_starve_a_new_upload(env, monkeypatch) -> None:
    monkeypatch.setattr(dfw, "MAX_PENDING_CHECKS_PER_SWEEP", 2)
    env.world.projects = [(PID, str(PID))]
    await env.sweep(T0)
    for build in "1234":
        env.store.drop(_result(str(PID), build), T0)
    await env.sweep(T0 + timedelta(minutes=1))                    # four new folders: all pending
    env.store.drop(_sentinel(str(PID), "9"), T0 + timedelta(minutes=1, seconds=30))
    env.store.heads.clear()
    out = await env.sweep(T0 + timedelta(minutes=2))
    assert [c["minio_prefix"] for c in env.delay.calls] == [f"{PID}/runs/9/"]
    assert env.store.heads == [_sentinel(str(PID), b) for b in "912"] and out["capped"] == "pending_checks"
    env.store.heads.clear()
    await env.sweep(T0 + timedelta(minutes=3))
    assert env.store.heads == [_sentinel(str(PID), b) for b in "34"], "the least recently checked go next"


async def test_the_per_sweep_queue_cap_leaves_the_rest_unrecorded_for_the_next_sweep(env, monkeypatch) -> None:
    monkeypatch.setattr(dfw, "MAX_QUEUED_PER_SWEEP", 1)
    env.world.projects = [(PID, str(PID))]
    await env.sweep(T0)
    env.store.drop(_sentinel(str(PID), "1"), T0 + timedelta(seconds=1))
    env.store.drop(_sentinel(str(PID), "2"), T0 + timedelta(seconds=1))
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert len(env.delay.calls) == 1 and out["capped"] == "queued"
    assert [p for p, r in env.world.rows.items() if r["status"] == "queued"] == [f"{PID}/runs/1/"]
    assert f"{PID}/runs/2/" not in env.world.rows
    out = await env.sweep(T0 + timedelta(minutes=2))
    assert [c["minio_prefix"] for c in env.delay.calls] == [f"{PID}/runs/1/", f"{PID}/runs/2/"]


async def test_a_failing_prefix_is_counted_and_the_others_still_run(env) -> None:
    await env.sweep(T0)
    env.store.fail_listing = {f"{SLUG}/runs/"}
    env.store.drop(_sentinel(str(PID), "3"), T0 + timedelta(seconds=1))
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert out["failed"] == 1 and [c["minio_prefix"] for c in env.delay.calls] == [f"{PID}/runs/3/"]
    assert env.log.named("drop_folder_prefix_failed") == [
        {"prefix": f"{SLUG}/runs/", "error_type": "ConnectionError"},
    ]


async def test_a_broker_failure_stops_queueing_and_the_folder_is_retried(env) -> None:
    env.world.projects = [(PID, str(PID))]
    await env.sweep(T0)
    env.store.drop(_sentinel(str(PID), "4"), T0 + timedelta(seconds=1))
    env.delay.fail = True
    out = await env.sweep(T0 + timedelta(minutes=1))
    assert out["capped"] == "enqueue_failed" and f"{PID}/runs/4/" not in env.world.rows
    env.delay.fail = False
    await env.sweep(T0 + timedelta(minutes=2))
    assert [c["minio_prefix"] for c in env.delay.calls] == [f"{PID}/runs/4/"]
    assert env.world.rows[f"{PID}/runs/4/"]["status"] == "queued"


async def test_a_deleted_runs_objects_never_come_back(env) -> None:
    """A slug-prefixed run deleted in the UI leaves its objects; its row stays ``queued``."""
    await env.sweep(T0)
    env.store.drop(_sentinel(SLUG, "11"), T0 + timedelta(seconds=1))
    await env.sweep(T0 + timedelta(minutes=1))
    env.store.drop(_sentinel(SLUG, "11"), T0 + timedelta(days=2))        # even a rewritten sentinel
    for minutes in (2, 3, 60 * 24 * 3):
        await env.sweep(T0 + timedelta(minutes=minutes))
    assert len(env.delay.calls) == 1
