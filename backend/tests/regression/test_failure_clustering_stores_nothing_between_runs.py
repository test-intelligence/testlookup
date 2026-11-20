"""Failure clustering keeps nothing between calls and shares nothing between projects.

The rule: ``embed_and_cluster`` groups one run's error messages. It does that in
an in-memory collection it creates for the call and deletes afterwards. It
never writes the messages to the Chroma server, where they used to sit in one
``failure_clusters`` collection for every project, with no project recorded,
outliving the project they came from and competing for the neighbour slots of
every other project's clustering.

The grouping itself does not change: for the same batch the result is the one
the stored collection gave when it held nothing else.

Real chromadb, in process. The ONNX model is replaced by a small deterministic
embedder, so nothing is downloaded and no network is used.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services import chroma_collections as cc
from app.tools import embed_and_cluster as module

pytestmark = pytest.mark.regression

chromadb = pytest.importorskip("chromadb")

DIMENSIONS = 384


def _embed_one(text: str) -> list[float]:
    """A bag-of-words vector: messages that share most words are close."""
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % DIMENSIONS
        vector[slot] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture(autouse=True)
def deterministic_embedder(monkeypatch):
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    monkeypatch.setattr(ONNXMiniLM_L6_V2, "__call__", lambda self, input: [_embed_one(t) for t in input])

    async def _current():
        return cc.Generations(active=cc.LEGACY_GENERATION, target=None, retiring=())

    async def _ef_for(_generation):
        return None

    monkeypatch.setattr(cc, "current_generations", _current)
    monkeypatch.setattr(cc, "embedding_function_for", _ef_for)


@pytest.fixture
def no_server(monkeypatch):
    """Reaching for the Chroma server is recorded and refused."""
    from app.db import chroma as chroma_db

    reached: list[str] = []

    def _refuse(*_args, **_kwargs):
        reached.append("server")
        raise AssertionError("failure clustering reached for the Chroma server")

    monkeypatch.setattr(chroma_db, "get_chroma_client", _refuse)
    monkeypatch.setattr(chroma_db, "get_configured_chroma_client", _refuse)
    monkeypatch.setattr(chromadb, "HttpClient", _refuse)
    return reached


TIMEOUT = "Connection timeout after 30s calling auth service at login step {n}"
NULLREF = "NullPointerException in CheckoutService.applyDiscount for cart item {n}"

BATCH = (
    [TIMEOUT.format(n=n) for n in range(6)]
    + [NULLREF.format(n=n) for n in range(4)]
    + ["AssertionError: expected 200 but got 503 from /api/orders"]
    + ["element #submit not found on page settings after waiting"]
)


async def _cluster(errors: list[str], prefix: str = "t") -> list[dict]:
    test_ids = [f"{prefix}{i}" for i in range(len(errors))]
    payload = json.dumps({"test_ids": test_ids, "error_messages": errors})
    return json.loads(await module.embed_and_cluster.coroutine(payload))["clusters"]


def _groups(clusters: list[dict]) -> list[list[str]]:
    return sorted(sorted(c["member_test_ids"]) for c in clusters)


def _stored_reference(errors: list[str], prefix: str = "t", *, client=None, name: str | None = None) -> list[list[str]]:
    """What the stored collection gave: the same ids, upsert and query on a
    collection that is opened by name and kept (here on an in-process client
    standing in for the server)."""
    client = client or chromadb.EphemeralClient()
    name = name or f"reference_{uuid.uuid4().hex}"
    collection = client.get_or_create_collection(name, **cc.ResolvedCollection(
        name=name, embedding_function=None, generation=cc.LEGACY_GENERATION,
    ).create_kwargs())
    ids = [hashlib.md5(e.encode()).hexdigest()[:16] for e in errors]
    collection.upsert(ids=ids, documents=errors)
    results = collection.query(
        query_texts=errors, n_results=min(len(errors), module._MAX_NEIGHBOR_QUERY), include=["distances"],
    )
    indices = module._cluster_from_neighbours(ids, results["ids"], results["distances"])
    return sorted(sorted(f"{prefix}{m}" for m in members) for members in indices)


def _collection_names() -> set[str]:
    return {getattr(c, "name", str(c)) for c in chromadb.EphemeralClient().list_collections()}


async def test_clustering_never_touches_the_chroma_server_and_leaves_nothing_behind(no_server):
    before = _collection_names()
    with patch.object(module, "logger", MagicMock()) as log:
        clusters = await _cluster(BATCH)

    assert no_server == []
    # The embedding path ran: no fallback to word-overlap clustering was logged.
    log.warning.assert_not_called()
    assert sum(c["size"] for c in clusters) == len(BATCH)
    # Nothing is kept: no collection outlives the call, in memory or anywhere.
    assert _collection_names() == before
    assert not [name for name in _collection_names() if name.startswith("failure_clusters")]


async def test_a_call_cannot_see_an_earlier_calls_messages(no_server, monkeypatch):
    seen: list[set[str]] = []
    real = module._cluster_from_neighbours

    def _spy(ids, result_ids, result_distances, *args, **kwargs):
        seen.append({neighbour for row in result_ids for neighbour in row})
        return real(ids, result_ids, result_distances, *args, **kwargs)

    monkeypatch.setattr(module, "_cluster_from_neighbours", _spy)

    first = [TIMEOUT.format(n=n) for n in range(40, 80)]      # another project's near-copies
    second = [TIMEOUT.format(n=n) for n in range(5)]
    alone = _groups(await _cluster(second, prefix="b"))
    await _cluster(first, prefix="a")
    after = _groups(await _cluster(second, prefix="b"))

    first_ids = {hashlib.md5(e.encode()).hexdigest()[:16] for e in first}
    second_ids = {hashlib.md5(e.encode()).hexdigest()[:16] for e in second}
    assert len(seen) == 3 and seen[1] == first_ids
    # The neighbour search of the later call returns only its own batch.
    assert seen[2] == second_ids and not (seen[2] & first_ids)
    assert after == alone == [["b0", "b1", "b2", "b3", "b4"]]
    assert no_server == []


async def test_errors_with_the_same_cause_still_land_in_one_cluster(no_server):
    errors = [TIMEOUT.format(n=n) for n in range(6)] + [NULLREF.format(n=n) for n in range(4)]
    clusters = await _cluster(errors)

    assert _groups(clusters) == [["t0", "t1", "t2", "t3", "t4", "t5"], ["t6", "t7", "t8", "t9"]]
    assert [c["size"] for c in clusters] == [6, 4]                      # largest first
    assert clusters[0]["cluster_id"] == "cl_001" and clusters[0]["representative_error"] == errors[0]
    assert clusters[0]["label"] == errors[0][:80]


async def test_the_grouping_is_the_one_the_stored_collection_gave(no_server):
    """Same batch, same ids, same upsert and query: the in-memory collection
    groups exactly as a stored collection that holds nothing else."""
    assert _groups(await _cluster(BATCH)) == _stored_reference(BATCH)
    shuffled = BATCH[5:] + BATCH[:5]
    assert _groups(await _cluster(shuffled)) == _stored_reference(shuffled)


async def test_an_exact_repeat_still_takes_the_word_overlap_fallback_as_before(no_server):
    """Unchanged behaviour, pinned so the move to memory is seen not to alter it:
    two tests with the identical message give two identical ids, chromadb
    refuses the upsert, and the batch is grouped by word overlap instead."""
    errors = BATCH + [BATCH[0]]
    with pytest.raises(chromadb.errors.DuplicateIDError):
        _stored_reference(errors)

    with patch.object(module, "logger", MagicMock()) as log:
        clusters = await _cluster(errors)

    log.warning.assert_called_once()
    expected = sorted(sorted(f"t{m}" for m in members) for members in module._simple_cluster(errors))
    assert _groups(clusters) == expected
    assert not [name for name in _collection_names() if name.startswith("failure_clusters")]


async def test_a_shared_stored_collection_is_what_let_one_project_split_anothers_cluster(no_server):
    """Why the stored collection went: with another project's near-copies in
    it, the neighbour window of a batch fills with documents that are not in
    the batch, and its one cause splits. In memory it cannot."""
    victim = [TIMEOUT.format(n=n) for n in range(100, 120)]

    shared = chromadb.EphemeralClient()
    name = f"reference_{uuid.uuid4().hex}"
    try:
        _stored_reference([TIMEOUT.format(n=n) for n in range(1000, 1400)], client=shared, name=name)
        polluted = _stored_reference(victim, client=shared, name=name)
    finally:
        shared.delete_collection(name)
    clean = _groups(await _cluster(victim))

    assert len(polluted) > 1
    assert clean == [sorted(f"t{i}" for i in range(20))]


async def test_without_chromadb_the_word_overlap_fallback_still_groups(monkeypatch):
    def _broken(*_args, **_kwargs):
        raise RuntimeError("chromadb is not usable here")

    monkeypatch.setattr(chromadb, "EphemeralClient", _broken)
    with patch.object(module, "logger", MagicMock()) as log:
        clusters = await _cluster(["boom at step one", "boom at step one", "something else entirely"])

    log.warning.assert_called_once()
    assert _groups(clusters) == [["t0", "t1"], ["t2"]]
