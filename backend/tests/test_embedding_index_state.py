"""W12 SRCH-02: ``services/embedding_index_state.py`` -- the durable state of the
embedding index (contract §1.2, §1.3, §2.4).

``parse_state`` turns anything malformed into the legacy idle default;
``read_state`` is ONE statement on the caller's session and never raises
(``None`` on a fault, the legacy default for a missing row or a bad value);
``request_target`` bumps the fence, retires a replaced target and takes a
wanted generation out of ``retiring``. Stage-only: nothing here commits.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import embedding_index_state as eis
from app.services.chroma_collections import LEGACY_GENERATION, Generation
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderRefused, EmbedderSpec
from app.services.embedding_index_state import (
    DEFAULT_STATE,
    EmbeddingIndexState,
    base_generation,
    parse_state,
    read_state,
    request_target,
)

NOW = datetime(2026, 10, 24, 14, 5, tzinfo=timezone.utc)
NOW_ISO = "2026-10-24T14:05:00+00:00"
ACTOR = "5b0c7a52-9a64-4c55-9f53-3c3c0f0e2a11"
OLLAMA = Generation("", EmbedderSpec("ollama", "nomic-embed-text:v1.5"))
OPENAI = Generation("", EmbedderSpec("openai", "text-embedding-3-small"))
DEMO = Generation("demo", DEFAULT_EMBEDDER)


def _retiring(state: EmbeddingIndexState) -> list[Generation]:
    return list(state.retiring_generations())


# ── parse_state: anything malformed -> the legacy idle default ───────────────

def test_the_default_state_is_the_legacy_generation_idle():
    assert DEFAULT_STATE.active_generation() == LEGACY_GENERATION
    assert DEFAULT_STATE.target_generation() is None
    assert (DEFAULT_STATE.status, DEFAULT_STATE.fence, DEFAULT_STATE.retiring) == ("idle", 0, [])
    assert DEFAULT_STATE.generations().visit_order() == (LEGACY_GENERATION,)
    assert eis.EMBEDDING_STATE_KEY == "embedding_index"
    assert eis.EMBEDDING_INDEX_STATUSES == ("idle", "building", "failed")


@pytest.mark.parametrize("value", [
    None, "", "{not json", '{"version": 1}', [], 7, True,
    {"version": 2, "active": OLLAMA.as_dict()},
    {"version": 1, "active": "ollama"},
    {"version": 1, "active": {"prefix": "", "provider": 3, "model": "m"}},
    {"version": 1, "active": {"prefix": "Demo_1", "provider": "default", "model": None}},
])
def test_a_malformed_value_parses_to_the_default(value):
    assert parse_state(value) == DEFAULT_STATE


def test_an_absent_active_is_the_legacy_generation():
    assert parse_state({"version": 1}).active_generation() == LEGACY_GENERATION


def test_a_malformed_optional_field_is_dropped_on_its_own():
    state = parse_state({
        "version": 1,
        "active": OLLAMA.as_dict(),
        "target": {"provider": ["x"]},
        "status": "building",
        "families": {"eighth": {"state": "done"}, "knowledge_chunks": {"state": "bogus", "processed": -4, "total": True}},
        "retiring": [{"provider": "ollama", "model": "old"}, "junk", {"prefix": "BAD", "provider": "default"}],
        "lease": {"owner": "w1"},
        "last_error": "x" * 500,
        "fence": True,
        "requested_at": 5,
    })
    assert state.active_generation() == OLLAMA
    assert state.target is None and state.status == "idle"   # a build always names its target
    assert state.families == {"knowledge_chunks": {"state": "pending", "processed": 0, "total": None, "cursor": 0}}
    assert _retiring(state) == [Generation("", EmbedderSpec("ollama", "old"))]
    assert state.lease is None and state.requested_at is None
    assert state.last_error == "x" * 300
    assert state.fence == 0


def test_replaced_is_kept_on_a_retiring_entry_only():
    value = {
        "version": 1,
        "active": {**OLLAMA.as_dict(), "replaced": True, "switched_at": NOW_ISO},
        "target": {**OPENAI.as_dict(), "replaced": True},
        "status": "building",
        "fence": 3,
        "retiring": [{**DEMO.as_dict(), "replaced": True, "switched_at": NOW_ISO}],
    }
    state = parse_state(value)
    assert state.active == OLLAMA.as_dict()
    assert state.target == OPENAI.as_dict()
    assert state.retiring == [{**DEMO.as_dict(), "replaced": True, "switched_at": NOW_ISO}]
    assert parse_state(state.as_value()) == state   # the stored value round-trips


# ── read_state: one statement, never raises ──────────────────────────────────

class _Session:
    """Records every statement; answers ``row`` (or raises ``exc``)."""

    def __init__(self, row=None, exc: BaseException | None = None):
        self.row, self.exc, self.statements = row, exc, []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def commit(self):  # pragma: no cover - a stage-only module never commits
        raise AssertionError("embedding_index_state must not commit")


async def test_a_missing_row_reads_as_the_legacy_default():
    session = _Session(row=None)
    assert await read_state(session) == DEFAULT_STATE
    assert len(session.statements) == 1
    assert "app_settings" in str(session.statements[0])


@pytest.mark.parametrize("value", [None, "{not json", ["a"], {"version": 9}])
async def test_a_bad_stored_value_reads_as_the_legacy_default(value):
    session = _Session(row=SimpleNamespace(value=value))
    assert await read_state(session) == DEFAULT_STATE
    assert len(session.statements) == 1


async def test_a_stored_state_is_read_back():
    stored = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    state = await read_state(_Session(row=SimpleNamespace(value=stored.as_value())))
    assert state == stored
    assert state is not None and state.target_generation() == OLLAMA


@pytest.mark.parametrize("session", [
    MagicMock(),                                   # a plain MagicMock (the direct-call tests' session)
    _Session(exc=RuntimeError("connection refused to 10.0.0.5")),
    _Session(exc=TimeoutError()),
], ids=["magicmock", "raising", "timeout"])
async def test_read_state_never_raises_it_answers_none(session):
    assert await read_state(session) is None


# ── request_target: the fence, retiring, a wanted generation ─────────────────

def test_a_first_request_sets_the_target_and_bumps_the_fence():
    state = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    assert state.target_generation() == OLLAMA
    assert state.active_generation() == LEGACY_GENERATION
    assert (state.status, state.fence) == ("building", 1)
    assert (state.requested_at, state.requested_by) == (NOW_ISO, ACTOR)
    assert (state.families, state.retiring, state.lease, state.last_error) == ({}, [], None, None)
    assert base_generation(state) == OLLAMA            # a further change builds on the target
    assert base_generation(DEFAULT_STATE) == LEGACY_GENERATION
    assert DEFAULT_STATE.fence == 0                    # pure: the input is not changed


def test_a_replaced_target_joins_retiring_and_the_fence_moves_on():
    building = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    building = replace(
        building,
        families={"knowledge_chunks": {"state": "running", "processed": 10, "total": 40, "cursor": 10}},
        lease={"owner": "w1", "until": NOW_ISO},
    )
    replaced = request_target(building, OPENAI, actor_id=None, now=NOW)
    assert replaced.target_generation() == OPENAI
    assert (replaced.status, replaced.fence) == ("building", 2)      # the old build stops at its next checkpoint
    assert replaced.retiring == [{**OLLAMA.as_dict(), "switched_at": NOW_ISO, "replaced": True}]
    assert (replaced.families, replaced.lease, replaced.requested_by) == ({}, None, None)


def test_asking_for_the_same_target_again_bumps_the_fence_without_retiring_it():
    once = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    twice = request_target(once, OLLAMA, actor_id=ACTOR, now=NOW)
    assert (twice.fence, twice.retiring, twice.target_generation()) == (2, [], OLLAMA)


def test_asking_for_the_active_generation_clears_the_target_and_retires_it():
    building = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    cleared = request_target(building, LEGACY_GENERATION, actor_id=ACTOR, now=NOW)
    assert cleared.target is None
    assert (cleared.status, cleared.fence) == ("idle", 2)
    assert _retiring(cleared) == [OLLAMA]
    assert (cleared.requested_at, cleared.requested_by) == (None, None)


def test_asking_for_the_active_generation_with_no_target_changes_nothing_that_matters():
    same = request_target(DEFAULT_STATE, LEGACY_GENERATION, actor_id=ACTOR, now=NOW)
    assert (same.target, same.status, same.fence, same.retiring) == (None, "idle", 0, [])


def test_a_wanted_generation_leaves_retiring():
    # OLLAMA was active, then switched away from (retiring, still inside its grace period).
    switched = EmbeddingIndexState(
        active=OPENAI.as_dict(), retiring=[{**OLLAMA.as_dict(), "switched_at": NOW_ISO}], fence=4,
    )
    back = request_target(switched, OLLAMA, actor_id=ACTOR, now=NOW)
    assert back.target_generation() == OLLAMA
    assert back.retiring == []                      # never dropped while it is being built into again
    # A replaced target that is asked for again also leaves retiring.
    first = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    second = request_target(first, OPENAI, actor_id=ACTOR, now=NOW)
    assert _retiring(second) == [OLLAMA]
    third = request_target(second, OLLAMA, actor_id=ACTOR, now=NOW)
    assert _retiring(third) == [OPENAI] and third.target_generation() == OLLAMA
    assert third.fence == 3


def test_the_active_generation_never_stays_in_retiring():
    odd = EmbeddingIndexState(active=OLLAMA.as_dict(), retiring=[{**OLLAMA.as_dict(), "switched_at": NOW_ISO}])
    assert request_target(odd, OPENAI, actor_id=ACTOR, now=NOW).retiring == []
    assert request_target(odd, OLLAMA, actor_id=ACTOR, now=NOW).retiring == []


def test_a_new_request_clears_a_failed_build():
    failed = replace(request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW),
                     status="failed", last_error="the build stopped with an error (ValueError)")
    retried = request_target(failed, OLLAMA, actor_id=ACTOR, now=NOW)
    assert (retried.status, retried.last_error, retried.fence) == ("building", None, 2)


def test_a_naive_now_is_read_as_utc():
    state = request_target(DEFAULT_STATE, OLLAMA, actor_id=None, now=NOW.replace(tzinfo=None))
    assert state.requested_at == NOW_ISO


# ── stage_state (no commit), failure_sentence, the configured generation ─────

def test_stage_state_adds_or_updates_the_row_and_never_commits():
    state = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    db = MagicMock()
    eis.stage_state(db, None, state, actor_id=ACTOR)
    added = db.add.call_args.args[0]
    assert (added.key, added.value, added.updated_by) == ("embedding_index", state.as_value(), uuid.UUID(ACTOR))
    row = SimpleNamespace(value={}, updated_by=None)
    eis.stage_state(db, row, state, actor_id="not-a-uuid")
    assert row.value == state.as_value() and row.updated_by is None
    db.commit.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("exc, phrase", [
    (EmbedderRefused("AI_OFFLINE_MODE=true but LLM base_url host '8.8.4.4' is not local"),
     "the embedder was refused by this deployment's AI policy (EmbedderRefused)"),
    (ConnectionError("http://10.0.0.5:11434 refused; api_key=sk-live-123"),
     "the embedding service or the vector store did not answer (ConnectionError)"),
    (TimeoutError("chroma at 10.0.0.7 timed out"),
     "the embedding service or the vector store did not answer (TimeoutError)"),
    (ValueError("query text: password reset fails for alice@example.com"),
     "the build stopped with an error (ValueError)"),
])
def test_failure_sentence_names_the_type_and_never_the_message(exc, phrase):
    sentence = eis.failure_sentence(exc)
    assert sentence == phrase
    assert str(exc) not in sentence and len(sentence) <= 300


def test_the_configured_generation_reads_the_ai_config_and_the_storage_prefix():
    ai = {"embedding_provider": "Ollama", "embedding_model": " nomic-embed-text:v1.5 "}
    assert eis.configured_generation(ai, {"chroma_collection": "demo"}) == (
        Generation("demo", EmbedderSpec("ollama", "nomic-embed-text:v1.5")), None,
    )
    assert eis.configured_generation({}, {"chroma_collection": "testlookup_embeddings"}) == (LEGACY_GENERATION, None)
    gen, note = eis.configured_generation({}, {"chroma_collection": "Demo_1"})
    assert gen == LEGACY_GENERATION
    assert note == "The stored value 'Demo_1' is not a valid prefix and is ignored: collections carry no prefix."


async def test_read_embedding_index_answers_none_on_any_fault():
    assert await eis.read_embedding_index(MagicMock(), ai_config={}, storage_values={}) is None
    assert await eis.read_embedding_index(_Session(exc=RuntimeError("down")), ai_config={}, storage_values={}) is None


async def test_read_embedding_index_reports_a_build_and_the_configured_generation():
    state = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=NOW)
    body = await eis.read_embedding_index(
        _Session(row=SimpleNamespace(value=state.as_value())),
        ai_config={"embedding_provider": "ollama", "embedding_model": "nomic-embed-text:v1.5"},
        storage_values={},
    )
    assert body is not None
    assert (body["status"], body["in_sync"], body["configured_note"]) == ("building", False, None)
    assert body["active"]["key"] == "default" and body["target"]["key"] == "ollama:nomic-embed-text:v1.5"
    assert [f["family"] for f in body["families"]] == [
        "test_case_search", "knowledge_chunks", "agent_memory_vectors", "ai_analysis_cache",
        "open_defects", "failure_clusters", "dupdetect",
    ]
    assert [f["state"] for f in body["families"]] == ["pending"] * 3 + ["skipped"] * 4
    unsupported = await eis.read_embedding_index(
        _Session(row=None), ai_config={"embedding_provider": "cohere", "embedding_model": "x"}, storage_values={},
    )
    assert unsupported is not None and unsupported["configured_note"] == (
        "The stored provider 'cohere' is not supported; Bundled MiniLM (all-MiniLM-L6-v2) stays in use."
    )


# ── the generation read (chroma_collections.current_generations): never raises ─

class _Redis:
    def __init__(self, cached=None, broken=False):
        self.cached, self.broken, self.written = cached, broken, []

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.cached

    async def setex(self, key, ttl, value):
        self.written.append((key, ttl, value))


@pytest.fixture
def generation_read(monkeypatch):
    from app.db import redis_client
    from app.services import chroma_collections as cc

    cc.reset_generations_memo()
    reads: list[None] = []
    holder = {"redis": _Redis(), "value": None, "exc": None}

    async def _read_value():
        reads.append(None)
        if holder["exc"] is not None:
            raise holder["exc"]
        return holder["value"]

    monkeypatch.setattr(redis_client, "get_redis", lambda: holder["redis"])
    monkeypatch.setattr(cc, "_read_state_value", _read_value)
    yield cc, holder, reads
    cc.reset_generations_memo()


async def test_bad_json_in_redis_falls_back_to_the_database_read(generation_read):
    cc, holder, reads = generation_read
    stored = replace(DEFAULT_STATE, active=OLLAMA.as_dict())
    holder["redis"], holder["value"] = _Redis(cached="{not json"), stored.as_value()
    generations = await cc.current_generations()
    assert generations.active == OLLAMA and generations.known
    assert len(reads) == 1
    assert holder["redis"].written[0][:2] == (cc.STATE_CACHE_KEY, cc.STATE_CACHE_TTL_SECONDS)
    # The memo answers the next read without Redis or the database.
    holder["redis"] = _Redis(broken=True)
    assert (await cc.current_generations()).active == OLLAMA
    assert len(reads) == 1


async def test_nothing_known_and_the_read_failing_is_the_legacy_generation(generation_read):
    cc, holder, reads = generation_read
    holder["redis"], holder["exc"] = _Redis(broken=True), RuntimeError("database down")
    generations = await cc.current_generations()
    assert generations.active == LEGACY_GENERATION
    assert (generations.target, generations.retiring, generations.known) == (None, (), False)
    assert len(reads) == 1


async def test_a_missing_row_is_the_legacy_generation_known(generation_read):
    cc, holder, _reads = generation_read
    generations = await cc.current_generations()
    assert generations.active == LEGACY_GENERATION and generations.known


# ── a stalled build (G-C5 residual 2): no worker holds it, none is about to ──


def _at(seconds: float) -> str:
    from datetime import timedelta

    return (NOW + timedelta(seconds=seconds)).isoformat()


@pytest.mark.parametrize(
    ("lease", "requested_at", "stalled"),
    [
        ({"owner": "w", "until": _at(1)}, _at(-3000), False),                 # a slice holds it
        ({"owner": "w", "until": _at(0)}, _at(-3000), True),                  # expired: the worker died
        ({"owner": "w", "until": "not a time"}, _at(-1), True),
        ({"owner": "w", "until": _at(-539), "released": True}, _at(-3000), False),   # the next slice is due
        ({"owner": "w", "until": _at(-540), "released": True}, _at(-3000), True),    # nothing resumed it
        (None, _at(-539), False),                                             # queued, not claimed yet
        (None, _at(-540), True),                                              # never claimed
        (None, None, True),
    ],
)
def test_build_is_stalled(lease, requested_at, stalled):
    state = replace(request_target(DEFAULT_STATE, OLLAMA, actor_id=None, now=NOW),
                    lease=lease, requested_at=requested_at)

    assert eis.build_is_stalled(state, now=NOW) is stalled
    assert eis.build_is_stalled(state, now=NOW.replace(tzinfo=None)) is stalled   # a naive now is UTC


def test_only_a_building_state_can_be_stalled():
    long_ago = {"lease": None, "requested_at": _at(-9000)}
    failed = replace(request_target(DEFAULT_STATE, OLLAMA, actor_id=None, now=NOW), status="failed", **long_ago)

    assert not eis.build_is_stalled(failed, now=NOW)
    assert not eis.build_is_stalled(replace(DEFAULT_STATE, **long_ago), now=NOW)
    assert eis.BUILD_STALL_SECONDS == 540


async def test_read_embedding_index_says_whether_the_build_is_stalled():
    fresh = request_target(DEFAULT_STATE, OLLAMA, actor_id=ACTOR, now=datetime.now(timezone.utc))
    dead = replace(fresh, lease={"owner": "w", "until": "2026-01-01T00:00:00+00:00"})
    cfg = {"embedding_provider": "ollama", "embedding_model": "nomic-embed-text:v1.5"}

    bodies = [
        await eis.read_embedding_index(_Session(row=SimpleNamespace(value=s.as_value())), ai_config=cfg, storage_values={})
        for s in (DEFAULT_STATE, fresh, dead)
    ]

    assert [b["stalled"] if b else None for b in bodies] == [False, False, True]
