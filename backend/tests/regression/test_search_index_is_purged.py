"""SEARCH-009 — the test-case vector index was never purged.

Both semantic indexers filter ``Project.is_active`` at WRITE time, so deleting
a project stops new documents being added. Nothing retired the ones already
written: ``semantic_search`` contained no ``collection.delete`` of any kind.

Measured on a live deployment::

    postgres: 49,413 test_cases -- only    600 in ACTIVE projects
                                        48,813 in soft-deleted projects
    chromadb: test_case_search  ->      49,380 documents

so 98.8% of the index was embeddings of deleted projects' tests.

**Not a leak** — ``semantic_search`` applies a ``project_id`` metadata filter
and the Postgres layer re-filters, so a deleted project's documents were never
served to anyone. This is deletion completeness and unbounded growth.

**Why it matters anyway.** Retention ships as a *cross-store* purge and audits
itself as one. Its ``semantic`` counter comes from ``semantic_cache`` — the
``ai_analysis_cache_*`` collections — which is a DIFFERENT store from
``test_case_search``. So an executed purge reported complete having never
visited the store holding 49k embeddings. Two similarly-named collections is
exactly how that goes unnoticed, which is why the two counters are asserted as
distinct keys below.

**Why the hook is on retention and not on project delete.** Deleting a project
only flips ``is_active`` and is reversible. Purging embeddings there would make
un-deleting silently lossy — the incremental indexer will not re-add the rows
because its cursor has already passed them, so search would stay empty until a
FULL reindex. Retention's execute path is the deliberate, audited, already
irreversible one.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services import semantic_search  # noqa: E402

pytestmark = pytest.mark.regression


class _Collection:
    """Stands in for the Chroma collection, recording what it was asked."""

    def __init__(self, ids):
        self._ids = list(ids)
        self.get_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def get(self, where=None, include=None):
        self.get_calls.append({"where": where, "include": include})
        return {"ids": list(self._ids)}

    def delete(self, where=None):
        self.delete_calls.append({"where": where})
        self._ids = []


def _install(monkeypatch, collection):
    async def _get():
        return collection

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _get)


@pytest.mark.asyncio
async def test_preview_counts_without_deleting(monkeypatch):
    """A preview must never write. Retention's preview/execute split is a
    contract: an operator reads the preview before authorising the purge."""
    collection = _Collection(["a", "b", "c"])
    _install(monkeypatch, collection)

    count = await semantic_search.purge_project_documents("proj-1", execute=False)

    assert count == 3
    assert collection.delete_calls == [], "preview deleted documents"


@pytest.mark.asyncio
async def test_execute_deletes_this_project_only(monkeypatch):
    collection = _Collection(["a", "b", "c"])
    _install(monkeypatch, collection)

    count = await semantic_search.purge_project_documents("proj-1", execute=True)

    assert count == 3
    assert len(collection.delete_calls) == 1
    # Scoped by project metadata — an unscoped delete would wipe every
    # tenant's documents from this shared collection.
    assert collection.delete_calls[0]["where"] == {"project_id": "proj-1"}


@pytest.mark.asyncio
async def test_it_scopes_the_read_too(monkeypatch):
    """Counting must be scoped as well; a global count would report another
    tenant's documents as this project's purge candidates."""
    collection = _Collection(["a"])
    _install(monkeypatch, collection)

    await semantic_search.purge_project_documents("proj-9", execute=False)

    assert collection.get_calls[0]["where"] == {"project_id": "proj-9"}


@pytest.mark.asyncio
async def test_nothing_to_purge_is_not_a_delete(monkeypatch):
    collection = _Collection([])
    _install(monkeypatch, collection)

    assert await semantic_search.purge_project_documents("proj-1", execute=True) == 0
    assert collection.delete_calls == []


@pytest.mark.asyncio
async def test_a_vector_store_outage_does_not_block_the_purge(monkeypatch):
    """The durable stores (Postgres/Mongo/MinIO) must still be purged when
    ChromaDB is down — the same stance analysis_cache_retention takes."""

    async def _boom():
        raise ConnectionError("chromadb unavailable")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)

    assert await semantic_search.purge_project_documents("proj-1", execute=True) == 0


def test_retention_reports_the_search_index_separately_from_the_cache():
    """The two stores must not share a counter.

    ``analysis_cache["semantic"]`` is the ``ai_analysis_cache_*`` collections;
    ``search_index`` is ``test_case_search``. Folding them together is how a
    purge reported complete while never visiting the second one.
    """
    import inspect

    from app.services import retention_service

    source = inspect.getsource(retention_service.run_purge)
    assert "search_index_documents" in source
    assert '"search_index"' in source, "execute result must report the store it purged"
    # Preview must report it too — a preview that under-counts is its own
    # defect, because it is what the operator authorises from.
    assert '"search_index_documents": search_index_documents' in source
