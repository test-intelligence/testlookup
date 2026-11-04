"""``semantic_search.purge_runs_documents`` — what the nightly purge of an ACTIVE project now deletes.

Owner decision 2026-11-03: the purge deleted an active project's WHOLE test-case search index every night
("not needed this way"). It now passes the runs it deletes, and only their documents go. The wiring (active
vs deleted project, the floor) is pinned against Postgres in
``tests/integration/test_retention_search_index_scope_postgres.py``; this file pins the function: the
listed runs' documents and nothing else, the count in a preview, chunked ``$in`` filters, and the
"unreachable is not empty" rule (``None``, never 0).
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import semantic_search  # noqa: E402

pytestmark = pytest.mark.regression


class _Collection:
    """A Chroma collection double: ``$and``, ``$in`` and equality, as the purge filters use them."""

    def __init__(self, docs):
        self._docs = dict(docs)   # {id: metadata}
        self.get_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    @staticmethod
    def _match(meta, clause):
        for key, value in (clause or {}).items():
            if key == "$and":
                if not all(_Collection._match(meta, sub) for sub in value):
                    return False
            elif isinstance(value, dict) and "$in" in value:
                if meta.get(key) not in value["$in"]:
                    return False
            elif meta.get(key) != value:
                return False
        return True

    def _matching(self, where):
        return [i for i, meta in self._docs.items() if self._match(meta, where)]

    def get(self, where=None, include=None):
        self.get_calls.append({"where": where, "include": include})
        return {"ids": self._matching(where)}

    def delete(self, where=None):
        self.delete_calls.append({"where": where})
        for i in self._matching(where):
            self._docs.pop(i)

    def ids(self):
        return set(self._docs)


def _index(monkeypatch, collection):
    async def _get():
        return collection

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _get)


@pytest.fixture
def world():
    """Project P with three runs (2 docs each) and project Q holding a document on a run id P also lists."""
    p, q = str(uuid.uuid4()), str(uuid.uuid4())
    aged_1, aged_2, kept = (str(uuid.uuid4()) for _ in range(3))
    docs = {}
    for run in (aged_1, aged_2, kept):
        for n in range(2):
            docs[f"{run[:8]}-{n}"] = {"project_id": p, "test_run_id": run}
    docs["other-project"] = {"project_id": q, "test_run_id": aged_1}
    return p, q, aged_1, aged_2, kept, docs


@pytest.mark.asyncio
async def test_execute_deletes_only_the_listed_runs_documents(monkeypatch, world):
    """The point of the change: the run the purge keeps keeps its documents, and so does every other project."""
    p, _q, aged_1, aged_2, kept, docs = world
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    removed = await semantic_search.purge_runs_documents(p, [aged_1, aged_2], execute=True)

    assert removed == 4
    assert collection.ids() == {f"{kept[:8]}-0", f"{kept[:8]}-1", "other-project"}


@pytest.mark.asyncio
async def test_preview_counts_without_deleting(monkeypatch, world):
    p, _q, aged_1, aged_2, _kept, docs = world
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    assert await semantic_search.purge_runs_documents(p, [aged_1, aged_2], execute=False) == 4
    assert collection.delete_calls == []
    assert len(collection.ids()) == 7


@pytest.mark.asyncio
async def test_no_runs_is_zero_without_visiting_the_store(monkeypatch):
    """Nothing purged = nothing of the purge's in the index; the store is not even opened."""
    async def _boom():
        raise AssertionError("the store must not be visited for an empty run list")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)
    assert await semantic_search.purge_runs_documents("p", [], execute=True) == 0


@pytest.mark.asyncio
async def test_run_ids_are_filtered_in_chunks(monkeypatch, world):
    """``$in`` over at most ``_RUN_PURGE_CHUNK`` ids per filter; the counts add up across chunks."""
    p, _q, aged_1, aged_2, kept, docs = world
    collection = _Collection(docs)
    _index(monkeypatch, collection)
    monkeypatch.setattr(semantic_search, "_RUN_PURGE_CHUNK", 2)

    removed = await semantic_search.purge_runs_documents(p, [aged_1, aged_2, kept], execute=True)

    assert removed == 6
    assert len(collection.get_calls) == 2
    in_lists = [call["where"]["$and"][1]["test_run_id"]["$in"] for call in collection.get_calls]
    assert in_lists == [[aged_1, aged_2], [kept]]
    assert all(call["where"]["$and"][0] == {"project_id": p} for call in collection.get_calls)
    assert collection.ids() == {"other-project"}


@pytest.mark.asyncio
async def test_unreachable_store_is_none_not_zero(monkeypatch):
    async def _down():
        raise ConnectionError("chroma down")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _down)
    assert await semantic_search.purge_runs_documents("p", [str(uuid.uuid4())], execute=True) is None


@pytest.mark.asyncio
async def test_a_failure_part_way_is_none_not_a_partial_count(monkeypatch, world):
    """The first chunk deletes, the second fails: the caller must not be told the first chunk's count as the
    whole."""
    p, _q, aged_1, aged_2, kept, docs = world
    collection = _Collection(docs)
    real_get = collection.get

    def _get(where=None, include=None):
        if len(collection.get_calls) >= 1:
            collection.get_calls.append({"where": where, "include": include})
            raise RuntimeError("timeout")
        return real_get(where=where, include=include)

    collection.get = _get
    _index(monkeypatch, collection)
    monkeypatch.setattr(semantic_search, "_RUN_PURGE_CHUNK", 2)

    assert await semantic_search.purge_runs_documents(p, [aged_1, aged_2, kept], execute=True) is None
