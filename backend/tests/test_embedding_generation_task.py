"""W12 SRCH-02 §2.4 test 5: the embedding generation build and its retirement.

``rebuild_embedding_generation`` / ``retire_embedding_generations`` run through
their REAL Celery wrappers (``task.run``) over fakes: an in-memory state row
(``lock_state`` / ``read_state`` / ``stage_state`` patched on the module, a
staged value lands only on ``commit``), an in-memory ChromaDB client, and the
PostgreSQL family's keyset build patched. Every ``apply_async`` the tasks make
is bound against the task's REAL signature, never accepted by a bare mock.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core import metrics
from app.services import chroma_collections as cc
from app.services import embedding_index_state as eis
from app.services import semantic_search
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderSpec
from app.worker import tasks

LEGACY = cc.LEGACY_GENERATION
DEMO = cc.Generation("demo", DEFAULT_EMBEDDER)                         # same embedder, a prefix
OLLAMA = cc.Generation("", EmbedderSpec("ollama", "nomic-embed-text:v1.5"))
LONG_AGO = "2026-01-01T00:00:00+00:00"


class FakeEF:
    """A ChromaDB embedding function that records every call (the model call)."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, input):  # noqa: A002 - ChromaDB's protocol name
        self.calls.append(list(input))
        return [[float(len(text)), 1.0] for text in input]


class FakeCollection:
    def __init__(self, name, ef=None, metadata=None):
        self.name = name
        self.ef = ef
        self.metadata = metadata
        self.rows: dict[str, dict] = {}

    def seed(self, item_id, document, embedding, metadata=None):
        self.rows[item_id] = {"document": document, "embedding": embedding, "metadata": metadata or {"k": item_id}}

    def count(self):
        return len(self.rows)

    def get(self, ids=None, limit=None, offset=None, include=None):
        keys = sorted(self.rows)
        if ids is not None:
            keys = [k for k in keys if k in ids]
        start = offset or 0
        keys = keys[start:start + limit] if limit is not None else keys[start:]
        include = include or []
        out: dict = {"ids": keys}
        if "documents" in include:
            out["documents"] = [self.rows[k]["document"] for k in keys]
        if "metadatas" in include:
            out["metadatas"] = [self.rows[k]["metadata"] for k in keys]
        if "embeddings" in include:
            out["embeddings"] = [self.rows[k]["embedding"] for k in keys]
        return out

    def upsert(self, ids, embeddings=None, documents=None, metadatas=None):
        if embeddings is None:
            embeddings = self.ef(documents) if self.ef is not None else [[0.0, 0.0] for _ in ids]
        for index, item_id in enumerate(ids):
            self.rows[item_id] = {
                "document": documents[index] if documents else None,
                "embedding": embeddings[index],
                "metadata": metadatas[index] if metadatas else None,
            }


class FakeClient:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}
        self.deleted: list[str] = []

    def add(self, name) -> FakeCollection:
        self.collections[name] = FakeCollection(name)
        return self.collections[name]

    def get_collection(self, name, **kwargs):
        if name not in self.collections:
            raise ValueError(f"Collection {name} does not exist.")
        return self.collections[name]

    def get_or_create_collection(self, name, embedding_function=None, metadata=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection(name, ef=embedding_function, metadata=metadata)
        return self.collections[name]

    def list_collections(self):
        return [SimpleNamespace(name=n) for n in self.collections]

    def delete_collection(self, name):
        self.deleted.append(name)
        del self.collections[name]


def kc(generation):
    return cc.collection_name("knowledge_chunks", generation=generation)


def amv(generation):
    return cc.collection_name("agent_memory_vectors", generation=generation)


def counter(metric, **labels) -> float:
    return float(metric.labels(**labels)._value.get())


class FakeSession:
    def __init__(self, world):
        self.world = world
        self.pending = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        if self.pending is not None:
            self.world.state = self.pending
            self.pending = None
        self.world.events.append("commit")

    async def rollback(self):
        self.pending = None
        self.world.events.append("rollback")


class World:
    def __init__(self, state):
        self.state = state
        self.events: list[str] = []
        self.enqueued: list[tuple[str, dict, dict]] = []
        self.audits: list[tuple] = []
        self.cursor_resets: list[str] = []
        self.forgotten: list = []
        self.build_calls: list[dict] = []
        self.client = FakeClient()
        self.ef = FakeEF()
        self.build_result: tuple[int, bool] | Exception = (4, True)


def building(target, *, fence=3, **extra):
    return eis.EmbeddingIndexState(target=target.as_dict(), status="building", fence=fence, **extra)


@pytest.fixture
def world(monkeypatch):
    w = World(eis.DEFAULT_STATE)

    async def lock_state(db):
        return SimpleNamespace(key="embedding_index"), w.state

    async def read_state(db):
        return w.state

    def stage_state(db, row, state, *, actor_id):
        db.pending = state

    async def invalidate():
        w.events.append("invalidate")

    async def chroma_client():
        return w.client

    async def ef_for(generation):
        return None if generation.embedder.provider == "default" else w.ef

    async def count_indexable(db):
        return 4

    async def build_index(db, generation, *, deadline):
        w.build_calls.append({"generation": generation, "seconds_left": deadline - time.monotonic()})
        if isinstance(w.build_result, Exception):
            raise w.build_result
        return w.build_result

    async def high_water(db, generation):
        return "hw-" + (generation.tag or "legacy")

    async def forget(db, generation):
        w.forgotten.append(generation)

    async def audit(db, key, action, actor, *, changed_fields=None, **kwargs):
        w.audits.append((key, action, actor, tuple(changed_fields or ())))

    def reset_cursor(high_water_id):
        w.cursor_resets.append(high_water_id)
        return True

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: FakeSession(w), raising=False)
    monkeypatch.setattr("app.db.chroma.get_configured_chroma_client", chroma_client)
    monkeypatch.setattr("app.services.settings_audit_service.log_settings_change", audit)
    monkeypatch.setattr(eis, "lock_state", lock_state)
    monkeypatch.setattr(eis, "read_state", read_state)
    monkeypatch.setattr(eis, "stage_state", stage_state)
    monkeypatch.setattr(cc, "invalidate_generations_cache", invalidate)
    monkeypatch.setattr(cc, "embedding_function_for", ef_for)
    monkeypatch.setattr(semantic_search, "count_indexable_test_cases", count_indexable)
    monkeypatch.setattr(semantic_search, "build_generation_index", build_index)
    monkeypatch.setattr(semantic_search, "generation_high_water", high_water)
    monkeypatch.setattr(semantic_search, "forget_generation_job", forget)
    monkeypatch.setattr(semantic_search, "reset_incremental_cursor", reset_cursor)

    for task in (tasks.rebuild_embedding_generation, tasks.retire_embedding_generations):
        def bound(args=None, kwargs=None, *, _task=task, **options):
            # The payload must bind to the task's REAL signature (a live worker would refuse it).
            inspect.signature(_task.run).bind(*(args or ()), **(kwargs or {}))
            short = _task.name.rsplit(".", 1)[-1]
            w.enqueued.append((short, dict(kwargs or {}), options))
            w.events.append("enqueue:" + short)
        monkeypatch.setattr(task, "apply_async", bound)
    return w


# ── declarations ────────────────────────────────────────────────────────────


def test_the_tasks_are_declared_as_the_contract_pins_them():
    from app.worker.celery_app import celery_app

    build = tasks.rebuild_embedding_generation
    assert build.name == "app.worker.tasks.rebuild_embedding_generation"
    assert (build.soft_time_limit, build.time_limit) == (600, 660)
    assert list(inspect.signature(build.run).parameters) == ["generation_tag", "fence"]
    assert celery_app.conf.task_routes["app.worker.tasks.rebuild_embedding_generation"] == {"queue": "ai_analysis"}
    # One invocation's work budget fits under the soft limit with room for the switch.
    assert cc.GENERATION_SLICE_SECONDS == 480 < build.soft_time_limit
    retire = tasks.retire_embedding_generations
    assert retire.name == "app.worker.tasks.retire_embedding_generations"
    assert list(inspect.signature(retire.run).parameters) == []
    assert "app.worker.tasks.retire_embedding_generations" not in (celery_app.conf.task_routes or {})


# ── the build ───────────────────────────────────────────────────────────────


def _seed_active_copy_families(client, generation):
    knowledge = client.add(kc(generation))
    for item_id in ("k1", "k2", "k3"):
        knowledge.seed(item_id, f"chunk {item_id}", [0.5, 0.25])
    memory = client.add(amv(generation))
    for item_id in ("m1", "m2"):
        memory.seed(item_id, f"memory {item_id}", [0.125, 0.75])


def test_a_build_with_the_same_embedder_copies_vectors_and_switches(world):
    _seed_active_copy_families(world.client, LEGACY)
    world.state = building(DEMO)
    switched = counter(metrics.embedding_generation_builds_total, outcome="switched")
    docs_kc = counter(metrics.embedding_generation_documents_total, family="knowledge_chunks")
    docs_tcs = counter(metrics.embedding_generation_documents_total, family="test_case_search")

    outcome = tasks.rebuild_embedding_generation.run(DEMO.tag, 3)

    assert outcome == {"status": "switched", "generation_tag": DEMO.tag}
    target_kc = world.client.collections[kc(DEMO)]
    assert {k: r["embedding"] for k, r in target_kc.rows.items()} == {"k1": [0.5, 0.25], "k2": [0.5, 0.25], "k3": [0.5, 0.25]}
    assert set(world.client.collections[amv(DEMO)].rows) == {"m1", "m2"}
    assert world.ef.calls == []                                   # vectors copied: no model call
    state = world.state
    assert state.active == DEMO.as_dict() and state.target is None and state.status == "idle"
    assert state.families == {} and state.lease is None and state.last_error is None
    assert [cc.Generation.from_dict(i) for i in state.retiring] == [LEGACY]
    assert world.audits == [("embedding_index", "switched", None, ("active", f"generation:{DEMO.tag}"))]
    assert world.cursor_resets == [f"hw-{DEMO.tag}"]
    assert counter(metrics.embedding_generation_builds_total, outcome="switched") == switched + 1
    assert counter(metrics.embedding_generation_documents_total, family="knowledge_chunks") == docs_kc + 3
    assert counter(metrics.embedding_generation_documents_total, family="test_case_search") == docs_tcs + 4
    # The switch commits, THEN invalidates the cache, THEN schedules the retirement.
    tail = world.events[-3:]
    assert tail == ["commit", "invalidate", "enqueue:retire_embedding_generations"]
    assert world.enqueued == [("retire_embedding_generations", {}, {"countdown": cc.RETIRE_GRACE_SECONDS})]


def test_a_build_with_another_embedder_re_embeds_the_documents(world):
    _seed_active_copy_families(world.client, LEGACY)
    world.state = building(OLLAMA)

    outcome = tasks.rebuild_embedding_generation.run(OLLAMA.tag, 3)

    assert outcome["status"] == "switched"
    target_kc = world.client.collections[kc(OLLAMA)]
    assert target_kc.ef is world.ef                               # opened with the target's function
    assert sorted(text for call in world.ef.calls for text in call) == sorted(
        ["chunk k1", "chunk k2", "chunk k3", "memory m1", "memory m2"]
    )
    assert target_kc.rows["k1"]["embedding"] == [float(len("chunk k1")), 1.0]   # never the old vector
    assert world.state.active == OLLAMA.as_dict()


def test_a_stale_fence_is_superseded_and_touches_nothing(world):
    world.state = building(DEMO, fence=4)        # the setting changed again after this invocation was queued
    before = world.state
    superseded = counter(metrics.embedding_generation_builds_total, outcome="superseded")

    outcome = tasks.rebuild_embedding_generation.run(DEMO.tag, 3)

    assert outcome == {"status": "superseded", "generation_tag": DEMO.tag}
    assert world.state is before
    assert "commit" not in world.events and world.build_calls == [] and world.enqueued == []
    assert counter(metrics.embedding_generation_builds_total, outcome="superseded") == superseded + 1


def test_a_failure_leaves_the_active_generation_and_records_a_sentence(world):
    world.state = building(OLLAMA)
    world.build_result = ConnectionError("http://gpu-box.internal:11434 refused; api_key=sk-live-abc123")
    failed = counter(metrics.embedding_generation_builds_total, outcome="failed")

    outcome = tasks.rebuild_embedding_generation.run(OLLAMA.tag, 3)

    assert outcome["status"] == "failed"
    state = world.state
    assert state.status == "failed" and state.lease is None
    assert state.active == LEGACY.as_dict()                       # reads keep the old generation
    assert state.target == OLLAMA.as_dict()                       # Retry rebuilds the same target
    assert state.last_error == "the embedding service or the vector store did not answer (ConnectionError)"
    for secret in ("gpu-box", "11434", "sk-live", "api_key"):
        assert secret not in state.last_error and secret not in outcome["error"]
    assert len(state.last_error) <= eis.LAST_ERROR_MAX
    assert counter(metrics.embedding_generation_builds_total, outcome="failed") == failed + 1
    assert world.enqueued == []                                   # no Celery retry, no switch
    assert world.audits == [] and world.cursor_resets == []


def test_the_deadline_hands_the_lease_back_and_re_enqueues_under_its_fence(world, monkeypatch):
    monkeypatch.setattr(cc, "GENERATION_SLICE_SECONDS", 0)       # the slice budget is spent at once
    world.state = building(OLLAMA, fence=7)
    world.build_result = (5, False)

    outcome = tasks.rebuild_embedding_generation.run(OLLAMA.tag, 7)

    assert outcome == {"status": "continuing", "generation_tag": OLLAMA.tag, "countdown": 1}
    assert world.enqueued == [
        ("rebuild_embedding_generation", {"generation_tag": OLLAMA.tag, "fence": 7}, {"countdown": 1})
    ]
    state = world.state
    assert state.status == "building" and state.fence == 7
    # Handed back, not dropped: it expires at once and records when (a chain that never resumes is "stalled").
    assert state.lease is not None and state.lease["released"] is True
    assert datetime.fromisoformat(state.lease["until"]) <= datetime.now(timezone.utc)
    assert not eis.build_is_stalled(state, now=datetime.now(timezone.utc))
    assert state.active == LEGACY.as_dict() and state.target == OLLAMA.as_dict()
    assert state.families["test_case_search"] == {"state": "running", "processed": 5, "total": 4, "cursor": 0}
    assert state.families["knowledge_chunks"]["state"] == "pending"   # the next slice resumes it
    last_commit = max(i for i, event in enumerate(world.events) if event == "commit")
    assert last_commit < world.events.index("enqueue:rebuild_embedding_generation")


def test_each_postgres_slice_is_bounded_by_the_slice_budget(world):
    world.state = building(DEMO)

    tasks.rebuild_embedding_generation.run(DEMO.tag, 3)

    assert world.build_calls, "the PostgreSQL family was not built"
    for call in world.build_calls:
        assert call["generation"] == DEMO
        assert 0 < call["seconds_left"] <= cc.GENERATION_SLICE_SECONDS


def test_a_live_lease_of_another_owner_answers_busy(world):
    world.state = building(DEMO, lease={"owner": "someone-else", "until": "2999-01-01T00:00:00+00:00"})

    outcome = tasks.rebuild_embedding_generation.run(DEMO.tag, 3)

    assert outcome == {"status": "busy", "generation_tag": DEMO.tag}
    assert world.build_calls == [] and world.enqueued == [] and "commit" not in world.events


# ── the retirement ──────────────────────────────────────────────────────────


def _retiring(generation, *, replaced=False, switched_at=LONG_AGO):
    item = {**generation.as_dict(), "switched_at": switched_at}
    if replaced:
        item["replaced"] = True
    return item


def test_retire_catches_up_an_old_active_but_never_a_replaced_target(world):
    replaced_target = cc.Generation("other", DEFAULT_EMBEDDER)
    active = world.client.add(kc(DEMO))
    active.seed("k1", "chunk k1", [0.5, 0.25])
    old_active = world.client.add(kc(LEGACY))
    old_active.seed("k1", "chunk k1", [0.5, 0.25])
    old_active.seed("late", "written through a stale cache", [0.5, 0.5])
    stale_copy = world.client.add(kc(replaced_target))
    stale_copy.seed("k1", "chunk k1", [0.5, 0.25])
    stale_copy.seed("deleted", "deleted from the active generation since", [0.5, 0.5])
    world.state = eis.EmbeddingIndexState(
        active=DEMO.as_dict(),
        retiring=[_retiring(LEGACY), _retiring(replaced_target, replaced=True)],
        fence=5,
    )

    outcome = tasks.retire_embedding_generations.run()

    assert outcome == {"status": "retired", "dropped": 2, "retry": False}
    assert set(world.client.collections) == {kc(DEMO)}
    assert set(world.client.collections[kc(DEMO)].rows) == {"k1", "late"}   # "deleted" stays deleted
    assert world.client.deleted == [kc(LEGACY), kc(replaced_target)]
    assert world.cursor_resets == [f"hw-{DEMO.tag}"]               # once: the old ACTIVE only
    assert world.forgotten == [LEGACY, replaced_target]           # a later build of either starts over
    assert world.state.retiring == [] and world.state.active == DEMO.as_dict()
    assert world.enqueued == []


def test_retire_waits_for_the_grace_period_and_re_enqueues_itself(world):
    from datetime import datetime, timezone

    recent = datetime.now(timezone.utc).isoformat()
    world.client.add(kc(LEGACY)).seed("k1", "chunk k1", [0.5, 0.25])
    world.state = eis.EmbeddingIndexState(active=DEMO.as_dict(), retiring=[_retiring(LEGACY, switched_at=recent)])

    outcome = tasks.retire_embedding_generations.run()

    assert outcome == {"status": "idle", "dropped": 0, "retry": True}
    assert kc(LEGACY) in world.client.collections and world.client.deleted == []
    assert world.enqueued == [("retire_embedding_generations", {}, {"countdown": cc.RETIRE_GRACE_SECONDS})]


def test_a_chroma_fault_keeps_the_generation_listed_and_retries(world, monkeypatch):
    async def unreachable():
        raise ConnectionError("chroma down")

    monkeypatch.setattr("app.db.chroma.get_configured_chroma_client", unreachable)
    world.state = eis.EmbeddingIndexState(active=DEMO.as_dict(), retiring=[_retiring(LEGACY)])
    before = world.state

    outcome = tasks.retire_embedding_generations.run()

    assert outcome == {"status": "retired", "dropped": 0, "retry": True}
    assert world.state is before and world.forgotten == []
    assert world.enqueued == [("retire_embedding_generations", {}, {"countdown": cc.RETIRE_GRACE_SECONDS})]


def test_a_retiring_generation_wanted_again_is_never_dropped(world):
    world.client.add(kc(DEMO)).seed("k1", "chunk k1", [0.5, 0.25])
    world.state = replace(building(DEMO), retiring=[_retiring(DEMO, replaced=True)])

    outcome = tasks.retire_embedding_generations.run()

    assert outcome["dropped"] == 0
    assert kc(DEMO) in world.client.collections and world.forgotten == []
    assert world.state.retiring == []


class _ReRequestingClient(FakeClient):
    """A settings change lands while the retire task works: on its FIRST Chroma call
    (the catch-up), the old generation is asked for again and becomes the target."""

    def __init__(self, world, wanted):
        super().__init__()
        self.world, self.wanted, self.flipped = world, wanted, False

    def get_collection(self, name, **kwargs):
        if not self.flipped:
            self.flipped = True
            self.world.state = eis.request_target(
                self.world.state, self.wanted, actor_id=None, now=datetime.now(timezone.utc)
            )
        return super().get_collection(name, **kwargs)


def test_a_generation_asked_for_again_during_the_retirement_keeps_its_collections(world):
    client = _ReRequestingClient(world, LEGACY)
    client.add(kc(LEGACY)).seed("k1", "chunk k1", [0.5, 0.25])
    client.add(amv(LEGACY)).seed("m1", "memory m1", [0.125, 0.75])
    client.add(kc(DEMO)).seed("k1", "chunk k1", [0.5, 0.25])
    world.client = client
    world.state = eis.EmbeddingIndexState(active=DEMO.as_dict(), retiring=[_retiring(LEGACY)], fence=2)

    outcome = tasks.retire_embedding_generations.run()

    assert client.flipped, "the re-request never landed"
    assert outcome["dropped"] == 0
    assert client.deleted == [] and {kc(LEGACY), amv(LEGACY)} <= set(client.collections)
    assert world.forgotten == []                                  # its build resumes its own job row
    assert world.state.target == LEGACY.as_dict() and world.state.status == "building"
    assert world.state.fence == 3 and world.state.retiring == []


def test_a_handed_back_lease_never_blocks_the_next_slice_whatever_the_clocks(world):
    ahead = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()   # the releasing worker's clock runs fast
    world.state = building(DEMO, lease={"owner": "previous-slice", "until": ahead, "released": True})

    outcome = tasks.rebuild_embedding_generation.run(DEMO.tag, 3)

    assert outcome["status"] == "switched"
