"""The semantic cache must never fall back to a collection shared across tenants.

Re-audit finding M15, second half. The exact-match analysis cache and the
ChromaDB semantic cache had the same fallback::

    name = f"{_COLLECTION_NAME}_{project_id}" if project_id else _COLLECTION_NAME

The triage agent guarded its semantic LOOKUP (``... if project_id else None``)
but not its STORE, so every analysis with no project was written into one
collection shared by all tenants. Nothing reads that collection today, which
makes it a leak waiting for its first unguarded reader: the cached analysis
embeds project-specific Splunk and OpenShift evidence, and a semantic match is
looser than an exact one.

The guard now lives in the service, not in one caller: without a project,
lookup, store and eviction do nothing, and the collection helper refuses, so no
future caller can reach the shared collection by omission.
"""
from __future__ import annotations

import pytest

from app.services import semantic_cache

pytestmark = pytest.mark.regression

PROJECT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FAILURE = ("test_login", "AssertionError: expected 200, got 500", "Traceback ...")


class _Collection:
    def __init__(self) -> None:
        self.queries = 0
        self.upserts = 0
        self.deletes = 0

    def query(self, **_kw):
        self.queries += 1
        return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}

    def upsert(self, **_kw):
        self.upserts += 1

    def delete(self, **_kw):
        self.deletes += 1

    def count(self):
        return 0


class _Client:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.collection = _Collection()

    def get_or_create_collection(self, name, metadata=None):
        self.opened.append(name)
        return self.collection


@pytest.fixture
def chroma(monkeypatch):
    client = _Client()

    async def _get_client():
        return client

    monkeypatch.setattr(semantic_cache, "_get_chroma_client", _get_client)
    return client


# ── The shared collection is unreachable ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_collection_helper_refuses_a_missing_project(chroma):
    """Defense in depth: no caller can obtain the shared collection by omission."""
    with pytest.raises(ValueError):
        await semantic_cache._get_or_create_collection(None)
    with pytest.raises(ValueError):
        await semantic_cache._get_or_create_collection("")
    assert chroma.opened == []


@pytest.mark.asyncio
async def test_an_unscoped_store_writes_nothing(chroma):
    """The path the triage agent took unguarded."""
    await semantic_cache.semantic_cache_store(
        *FAILURE, {"root_cause": "x", "confidence_score": 0.9}, project_id=None
    )
    assert chroma.opened == [], (
        "an analysis with no project was written to the semantic cache's "
        "shared collection, where any unscoped reader would match it"
    )
    assert chroma.collection.upserts == 0


@pytest.mark.asyncio
async def test_an_unscoped_lookup_reads_nothing(chroma):
    assert await semantic_cache.semantic_cache_lookup(*FAILURE, project_id=None) is None
    assert chroma.opened == []
    assert chroma.collection.queries == 0


@pytest.mark.asyncio
async def test_an_unscoped_eviction_touches_nothing(chroma):
    await semantic_cache.semantic_cache_invalidate(*FAILURE, project_id=None)
    assert chroma.opened == []
    assert chroma.collection.deletes == 0


# ── Scoped behaviour is unchanged ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scoped_lookup_uses_the_projects_own_collection(chroma):
    await semantic_cache.semantic_cache_lookup(*FAILURE, project_id=PROJECT)
    assert chroma.opened, "a scoped lookup no longer reaches the cache"
    assert all(name.endswith(f"_{PROJECT}") for name in chroma.opened), (
        f"a scoped lookup opened a collection that is not the project's: {chroma.opened}"
    )
