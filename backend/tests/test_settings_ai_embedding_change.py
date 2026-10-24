"""W12 SRCH-02 §2.4 test 7: a CHANGED embedder or Collection prefix starts a re-embed.

``PUT /settings/ai`` / ``PUT /settings/storage`` run as direct handler calls on
a recording session (every statement, the commit, the cache drops and the
enqueue land in ONE ordered list); the two new routes run through the ASGI app
with the REAL ``require_role`` dependency. The embedding index state row is an
in-memory value (``lock_state`` / ``stage_state`` / ``read_state`` patched on
the module); the build's ``apply_async`` payload is bound against the task's
REAL signature. No network: the Ollama hosts are IP literals.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.models.postgres import User, UserRole
from app.models.schemas import AIConfigUpdate, StorageConfigUpdate
from app.routers import app_settings as router_mod
from app.services import ai_config_resolver as resolver
from app.services import chroma_collections as cc
from app.services import embedding_function as ef
from app.services import embedding_index_state as eis
from app.worker import tasks

NOMIC = ef.EmbedderSpec("ollama", "nomic-embed-text:v1.5")
ON_BOX, OFF_BOX = "http://127.0.0.1:11434", "http://8.8.8.8:11434"
STORED_AI = {
    "llm_provider": "ollama",
    "llm_model": "qwen2.5:7b",
    "ai_offline_mode": True,
    "embedding_provider": "default",
    "embedding_model": "all-MiniLM-L6-v2",
}


def _user(role: UserRole, *, bound_project: uuid.UUID | None = None) -> User:
    user = User(id=uuid.uuid4(), username=f"u-{role.value}", email=f"{role.value}@example.test",
                role=role, is_active=True)
    if bound_project is not None:
        from app.core.deps import _API_KEY_BOUND_PROJECT_ATTR

        setattr(user, _API_KEY_BOUND_PROJECT_ATTR, bound_project)
    return user


class Harness:
    def __init__(self):
        self.events: list[str] = []
        self.state = eis.DEFAULT_STATE
        self.audits: list[tuple[str, str, tuple]] = []
        self.enqueued: list[dict] = []
        self.stored_ai = dict(STORED_AI)
        self.stored_storage: dict | None = None
        self.effective = {"offline_mode": True, "provider": "ollama", "base_url": ON_BOX, "openai_api_key": None}

    def db(self):
        """A recording session: ``execute`` answers the ai_config / storage row by the statement's key."""
        harness = self

        async def execute(statement, *args, **kwargs):
            text = str(statement.compile(compile_kwargs={"literal_binds": True})) if hasattr(statement, "compile") else ""
            if "storage_config" in text:
                value = harness.stored_storage
            else:
                value = harness.stored_ai
            result = MagicMock()
            result.scalar_one_or_none.return_value = SimpleNamespace(value=value) if value is not None else None
            return result

        db = MagicMock()
        db.execute = AsyncMock(side_effect=execute)
        db.commit = AsyncMock(side_effect=lambda: harness.events.append("commit"))
        db.rollback = AsyncMock()
        db.add = MagicMock()
        return db


@pytest.fixture
def h(monkeypatch):
    harness = Harness()

    async def lock_state(db):
        harness.events.append("lock_state")
        return SimpleNamespace(value=harness.state.as_value()), harness.state

    def stage_state(db, row, state, *, actor_id):
        harness.events.append("stage_state")
        harness.state = state

    async def read_state(db):
        return harness.state

    async def audit(db, key, action, actor, *, changed_fields=None, **kwargs):
        harness.events.append(f"audit:{key}:{action}")
        harness.audits.append((key, action, tuple(changed_fields or ())))

    async def invalidate_generations():
        harness.events.append("invalidate_generations")

    def enqueue(args=None, kwargs=None, **options):
        inspect.signature(tasks.rebuild_embedding_generation.run).bind(*(args or ()), **(kwargs or {}))
        harness.events.append("enqueue")
        harness.enqueued.append(dict(kwargs or {}))

    def no_redis():
        raise ConnectionError("no redis in this test")

    monkeypatch.setattr(eis, "lock_state", lock_state)
    monkeypatch.setattr(eis, "stage_state", stage_state)
    monkeypatch.setattr(eis, "read_state", read_state)
    monkeypatch.setattr(router_mod, "log_settings_change", audit)
    monkeypatch.setattr(cc, "invalidate_generations_cache", invalidate_generations)
    monkeypatch.setattr(tasks.rebuild_embedding_generation, "apply_async", enqueue)
    monkeypatch.setattr(tasks.retire_embedding_generations, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(resolver, "get_effective_ai_config", AsyncMock(side_effect=lambda **kw: dict(harness.effective)))
    monkeypatch.setattr(resolver, "invalidate_ai_config_cache", AsyncMock(side_effect=lambda: harness.events.append("invalidate_ai")))
    monkeypatch.setattr(resolver.settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(router_mod.settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr("app.db.redis_client.get_redis", no_redis)
    monkeypatch.setattr("app.services.feature_flags.is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(router_mod, "extract_secrets_from_config", lambda _k, _u: {})
    monkeypatch.setattr(router_mod, "strip_secrets_from_config", lambda _k, merged: dict(merged))
    monkeypatch.setattr(router_mod, "_ml_status", AsyncMock(return_value={
        "ml_model_available": False, "ml_model_accuracy": None, "ml_training_sample_count": 0,
        "ml_human_label_count": 0, "ml_human_label_floor": 50, "ml_maturity": "not_trained",
    }))
    monkeypatch.setattr(router_mod, "_get_storage_connection_status", AsyncMock(return_value={
        "postgres_connected": True, "mongo_connected": True, "redis_connected": True,
    }))
    monkeypatch.setattr("app.services.storage_config_service.invalidate_storage_config_cache",
                        AsyncMock(side_effect=lambda: harness.events.append("invalidate_storage")))
    return harness


async def _put_ai(h: Harness, **fields):
    return await router_mod.update_ai_config(
        payload=AIConfigUpdate(**fields), current_user=_user(UserRole.ADMIN), db=h.db(),
    )


async def _refused(h: Harness, **fields) -> str:
    with pytest.raises(HTTPException) as caught:
        await _put_ai(h, **fields)
    assert caught.value.status_code == 422
    assert "commit" not in h.events and "lock_state" not in h.events and h.enqueued == []
    return caught.value.detail


# ── PUT /settings/ai: the embedder is left alone ────────────────────────────


async def test_a_put_without_embedding_fields_never_touches_the_index_state(h):
    out = await _put_ai(h, llm_model="qwen2.5:14b")

    assert out.llm_model == "qwen2.5:14b"
    assert "lock_state" not in h.events and "stage_state" not in h.events and h.enqueued == []
    assert [a[:2] for a in h.audits] == [("ai_config", "updated")]
    assert out.embedding_index is not None and out.embedding_index.status == "idle"


async def test_an_unchanged_embedder_posted_back_passes_through_untouched(h):
    # The form posts every field back, even a legacy value this release does not support.
    h.stored_ai.update(embedding_provider="HuggingFace", embedding_model="all-mpnet-base-v2")

    out = await _put_ai(h, embedding_provider="HuggingFace", embedding_model="all-mpnet-base-v2",
                        llm_temperature=0.2)

    assert out.embedding_provider == "HuggingFace" and out.embedding_model == "all-mpnet-base-v2"
    assert "lock_state" not in h.events and h.enqueued == []
    assert h.events == ["audit:ai_config:updated", "commit", "invalidate_ai"]


# ── PUT /settings/ai: a CHANGED embedder is validated ───────────────────────


async def test_openai_while_offline_is_refused_with_its_sentence(h):
    detail = await _refused(h, embedding_provider="openai", embedding_model="text-embedding-3-small")

    assert detail == ef.OPENAI_OFFLINE_REFUSED
    assert detail == "OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama."


async def test_openai_without_a_key_is_refused_with_its_sentence(h):
    h.stored_ai["ai_offline_mode"] = False

    detail = await _refused(h, embedding_provider="openai", embedding_model="text-embedding-3-small")

    assert detail == ef.OPENAI_KEY_MISSING


async def test_an_unsupported_provider_is_refused_only_when_it_changes(h):
    detail = await _refused(h, embedding_provider="huggingface", embedding_model="x")

    assert detail == "Embedding provider must be one of: default, ollama, openai."


async def test_an_ollama_model_must_be_named(h):
    detail = await _refused(h, embedding_provider="ollama", embedding_model="  ")

    assert detail == ef.MODEL_REQUIRED


async def test_an_ollama_host_off_the_box_is_refused_while_offline(h):
    h.effective["base_url"] = OFF_BOX

    detail = await _refused(h, embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")

    assert detail == (
        "AI_OFFLINE_MODE=true but LLM base_url host '8.8.8.8' is "
        "not a loopback/private address — refusing to send prompts off-box"
    )


async def test_a_valid_change_stages_the_target_audits_and_enqueues_after_the_commit(h):
    out = await _put_ai(h, embedding_provider=" Ollama ", embedding_model=" nomic-embed-text:v1.5 ")

    target = cc.Generation("", NOMIC)
    assert h.state.target == target.as_dict() and h.state.status == "building" and h.state.fence == 1
    assert h.state.active == cc.LEGACY_GENERATION.as_dict()       # reads stay on the active generation
    assert h.events == [
        "lock_state", "stage_state", "audit:embedding_index:reembed_requested",
        "audit:ai_config:updated", "commit", "invalidate_ai", "invalidate_generations", "enqueue",
    ]
    assert ("embedding_index", "reembed_requested", ("embedding_provider", "embedding_model")) in h.audits
    assert h.enqueued == [{"generation_tag": target.tag, "fence": 1}]
    assert (out.embedding_provider, out.embedding_model) == ("ollama", "nomic-embed-text:v1.5")   # normalised
    assert out.embedding_index is not None and out.embedding_index.status == "building"
    assert out.embedding_index.target is not None and out.embedding_index.target.model == "nomic-embed-text:v1.5"


async def test_a_change_while_building_keeps_the_target_s_prefix_and_bumps_the_fence(h):
    h.state = eis.EmbeddingIndexState(target=cc.Generation("demo", ef.DEFAULT_EMBEDDER).as_dict(),
                                      status="building", fence=4)

    await _put_ai(h, embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")

    assert h.state.target == cc.Generation("demo", NOMIC).as_dict() and h.state.fence == 5
    assert h.enqueued == [{"generation_tag": cc.Generation("demo", NOMIC).tag, "fence": 5}]


# ── GET /settings/ai/embedding-index and POST …/rebuild (ASGI, real require_role) ──


@pytest.fixture
def api(h):
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    app.include_router(router_mod.router)
    caller: dict = {}
    app.dependency_overrides[get_db] = lambda: caller["db"]
    app.dependency_overrides[get_current_active_user] = lambda: caller["user"]

    async def call(method: str, path: str, user: User):
        caller["user"], caller["db"] = user, h.db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, f"/api/v1/settings{path}")

    return call


async def test_the_embedding_index_read_is_for_a_qa_lead_and_up(api, h):
    refused = await api("GET", "/ai/embedding-index", _user(UserRole.QA_ENGINEER))
    allowed = await api("GET", "/ai/embedding-index", _user(UserRole.QA_LEAD))

    assert refused.status_code == 403
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["status"] == "idle" and body["in_sync"] is True
    assert body["active"]["provider"] == "default" and body["active"]["tag"] == ""


async def test_an_unreadable_state_is_a_503_never_in_sync(api, h, monkeypatch):
    async def unreadable(db):
        return None

    monkeypatch.setattr(eis, "read_state", unreadable)

    response = await api("GET", "/ai/embedding-index", _user(UserRole.QA_LEAD))

    assert response.status_code == 503
    assert response.json()["detail"] == "Could not read the embedding index state."


async def test_rebuild_is_admin_only_and_refused_to_a_project_key(api, h):
    h.stored_ai.update(embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")

    lead = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.QA_LEAD))
    bound = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.ADMIN, bound_project=uuid.uuid4()))

    assert (lead.status_code, bound.status_code) == (403, 403)
    assert "lock_state" not in h.events and h.enqueued == []


async def test_rebuild_with_nothing_to_do_is_a_422(api, h):
    response = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.ADMIN))

    assert response.status_code == 422
    assert response.json()["detail"] == eis.REBUILD_NOTHING_TO_DO
    assert "commit" not in h.events and h.enqueued == []


def _iso(seconds_from_now: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)).isoformat()


def _timed(lease: dict | None, requested_in: float) -> tuple[dict | None, str]:
    """Build the lease / requested_at AT RUN TIME: offsets in seconds from now.

    A timestamp computed in a parametrize list is fixed at collection, so in a
    suite that runs twenty minutes before reaching the test a "live" lease has
    already expired (the full-suite failure this replaced).
    """
    if lease is not None:
        lease = {**lease, "until": _iso(lease["until"])}
    return lease, _iso(requested_in)


@pytest.mark.parametrize(
    ("lease", "requested_in"),
    [
        ({"owner": "worker-1", "until": 400}, -60),                      # a slice holds it
        ({"owner": "worker-1", "until": -5, "released": True}, -900),    # handed back between slices
        (None, -30),                                                     # queued, not claimed yet
    ],
    ids=["live-lease", "just-handed-back", "just-queued"],
)
async def test_rebuild_toward_a_build_with_a_worker_is_a_409(api, h, lease, requested_in):
    lease, requested_at = _timed(lease, requested_in)
    h.stored_ai.update(embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")
    h.state = eis.EmbeddingIndexState(target=cc.Generation("", NOMIC).as_dict(), status="building", fence=2,
                                      lease=lease, requested_at=requested_at)

    response = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.ADMIN))

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A re-embed is already running. Wait for it, or change the setting again to replace it."
    )
    assert h.state.fence == 2 and h.enqueued == []


@pytest.mark.parametrize(
    ("lease", "requested_in"),
    [
        ({"owner": "dead-worker", "until": -1}, -600),                   # killed mid-slice
        ({"owner": "worker-1", "until": -600, "released": True}, -900),  # nothing resumed it
        (None, -600),                                                    # nobody ever claimed it
    ],
    ids=["expired-lease", "never-resumed", "never-claimed"],
)
async def test_rebuild_resumes_a_stalled_build_under_a_new_fence(api, h, lease, requested_in):
    lease, requested_at = _timed(lease, requested_in)
    h.stored_ai.update(embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")
    stalled = eis.EmbeddingIndexState(target=cc.Generation("", NOMIC).as_dict(), status="building", fence=2,
                                      lease=lease, requested_at=requested_at)
    h.state = stalled
    before = await api("GET", "/ai/embedding-index", _user(UserRole.QA_LEAD))
    assert before.status_code == 200
    body = await eis.read_embedding_index(None, ai_config=dict(h.stored_ai), storage_values={})
    assert body is not None and body["stalled"] is True              # the page can say so

    response = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.ADMIN))

    assert response.status_code == 200 and response.json()["status"] == "building"
    assert h.enqueued == [{"generation_tag": cc.Generation("", NOMIC).tag, "fence": 3}]
    assert h.state.target == stalled.target and h.state.lease is None and h.state.retiring == []
    assert ("embedding_index", "reembed_requested",
            ("embedding_provider", "embedding_model", "chroma_collection")) in h.audits
    assert h.events[-4:] == ["audit:embedding_index:reembed_requested", "commit", "invalidate_generations", "enqueue"]


async def test_rebuild_builds_the_configured_generation_after_the_commit(api, h):
    h.stored_ai.update(embedding_provider="ollama", embedding_model="nomic-embed-text:v1.5")
    h.state = eis.EmbeddingIndexState(target=cc.Generation("", NOMIC).as_dict(), status="failed",
                                      fence=2, last_error="the build stopped with an error (RuntimeError)")

    response = await api("POST", "/ai/embedding-index/rebuild", _user(UserRole.ADMIN))

    assert response.status_code == 200
    assert response.json()["status"] == "building" and response.json()["last_error"] is None
    target = cc.Generation("", NOMIC)
    assert h.enqueued == [{"generation_tag": target.tag, "fence": 3}]           # Retry = a new fence
    assert h.events[-4:] == ["audit:embedding_index:reembed_requested", "commit", "invalidate_generations", "enqueue"]


# ── PUT / GET /settings/storage: the Collection prefix ──────────────────────


async def _put_storage(h: Harness, **fields):
    return await router_mod.update_storage_config(
        StorageConfigUpdate(**fields), current_user=_user(UserRole.ADMIN), db=h.db(),
    )


@pytest.mark.parametrize("value", ["Demo", "demo_1", "-demo", "toolongprefix", "dé"])
async def test_an_invalid_changed_prefix_is_a_422(h, value):
    with pytest.raises(HTTPException) as caught:
        await _put_storage(h, chroma_collection=value)

    assert caught.value.status_code == 422
    assert caught.value.detail == (
        "A collection prefix is up to 8 lower-case letters, digits or '-', starting with a letter or digit."
    )
    assert "commit" not in h.events and "lock_state" not in h.events


async def test_a_valid_prefix_builds_a_target_with_the_active_embedder(h):
    h.state = eis.EmbeddingIndexState(active=cc.Generation("", NOMIC).as_dict())

    out = await _put_storage(h, chroma_collection="demo")

    target = cc.Generation("demo", NOMIC)
    assert h.state.target == target.as_dict() and h.state.status == "building"
    assert ("embedding_index", "reembed_requested", ("chroma_collection",)) in h.audits
    assert h.events == [
        "lock_state", "stage_state", "audit:embedding_index:reembed_requested",
        "audit:storage_config:updated", "commit", "invalidate_storage", "invalidate_generations", "enqueue",
    ]
    assert h.enqueued == [{"generation_tag": target.tag, "fence": 1}]
    assert (out.collection_prefix, out.collection_prefix_note, out.active_collection_prefix) == ("demo", None, "")


async def test_the_legacy_default_and_an_empty_prefix_are_the_same_generation(h):
    h.stored_storage = {"chroma_collection": "testlookup_embeddings"}

    out = await _put_storage(h, chroma_collection="")

    assert "lock_state" not in h.events and h.enqueued == []
    assert h.events == ["audit:storage_config:updated", "commit", "invalidate_storage"]
    assert out.collection_prefix == ""


async def test_an_unchanged_stored_invalid_value_passes_and_reads_as_no_prefix_with_the_note(h):
    h.stored_storage = {"chroma_collection": "Legacy_Collection"}

    out = await _put_storage(h, chroma_collection="Legacy_Collection", chroma_port=8001)

    note = cc.PREFIX_INVALID_NOTE.format(value="Legacy_Collection")
    assert note == (
        "The stored value 'Legacy_Collection' is not a valid prefix and is ignored: collections carry no prefix."
    )
    assert "lock_state" not in h.events and h.enqueued == []
    assert (out.collection_prefix, out.collection_prefix_note) == ("", note)

    read = await router_mod.get_storage_config(_=_user(UserRole.QA_LEAD), db=h.db())

    assert (read.collection_prefix, read.collection_prefix_note, read.active_collection_prefix) == ("", note, "")
