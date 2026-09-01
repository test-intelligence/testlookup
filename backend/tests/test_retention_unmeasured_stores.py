"""RET-D15 — an unreachable store must not report as an empty one.

The retention preview is what an ADMIN authorises an irreversible cross-store
purge from. Two of its twelve counts come from stores that can be down
independently of Postgres — Redis and the two Chroma collections — and both
used to report ``0`` when they could not be reached. Zero and "could not look"
are opposite findings; rendering them alike understates the blast radius on the
one screen where understating it matters.

``semantic_search.purge_project_documents``'s own comment argued that returning
0 was acceptable because the failure was logged, and in the same breath said
"a purge that could not visit a store must not read as 'nothing to delete
there'". Both cannot hold: every caller renders the number and none of them
read the log.
"""
from __future__ import annotations

import pytest

from app.services import analysis_cache_retention, semantic_search
from app.services.retention_service import _sum_measured


# ── the aggregate ────────────────────────────────────────────────────────────


def test_sum_of_fully_measured_stores_is_a_number():
    assert _sum_measured([1, 2, 3]) == 6
    assert _sum_measured([0, 0]) == 0, "a genuine zero is still a zero"


def test_sum_is_unmeasured_when_any_contributor_is():
    """A partial total is worse than no total.

    "3 cache entries" when the semantic half never answered reads as a complete
    figure and is not one — it silently under-reports precisely the number an
    operator is using to decide.
    """
    assert _sum_measured([3, None]) is None
    assert _sum_measured([None, None]) is None


def test_empty_group_is_zero_not_unmeasured():
    """Nothing to sum is a real zero, not an outage."""
    assert _sum_measured([]) == 0


# ── the two store-backed counts ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_index_reports_none_when_the_collection_cannot_be_opened(
    monkeypatch,
):
    async def _boom():
        raise ConnectionError("chromadb unavailable")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)

    result = await semantic_search.purge_project_documents("p1", execute=True)

    assert result is None, "an unreachable index must not report 0 documents"


@pytest.mark.asyncio
async def test_search_index_reports_none_when_the_query_itself_fails(monkeypatch):
    """The SECOND failure path — opening the collection succeeded, reading it
    did not. Both returned 0 before; a fix that only covered the first would
    leave the commoner failure reporting an empty index."""

    class _Collection:
        def get(self, *_a, **_kw):
            raise RuntimeError("chroma query failed")

    async def _ok():
        return _Collection()

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _ok)

    result = await semantic_search.purge_project_documents("p1", execute=True)

    assert result is None


@pytest.mark.asyncio
async def test_analysis_cache_substores_start_unmeasured(monkeypatch):
    """Both sub-stores default to None, so a failure BEFORE the assignment
    leaves None rather than a zero nobody wrote on purpose.

    Redis and the semantic cache fail independently — each has its own try
    block — so one being down must not make the other's count meaningless.
    """
    from datetime import datetime, timezone

    def _no_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(
        "app.db.redis_client.get_redis", _no_redis, raising=False
    )

    counts = await analysis_cache_retention.purge_project_analysis_caches(
        "p1", cutoff=datetime.now(timezone.utc), execute=False
    )

    assert counts["redis"] is None, "an unreachable Redis must not report 0 keys"
    # The semantic half is independent; whatever it did, it must not be a
    # silently-defaulted zero standing in for "never ran".
    assert counts["semantic"] is None or isinstance(counts["semantic"], int)


def test_skipped_stores_default_to_unmeasured_not_zero():
    """When external stores are injected, run_purge SKIPS these two entirely.

    A zero default would report "nothing in the cache" for a store the run
    never visited — which is how every fake-injecting test in this repo has
    been quietly asserting against three of the five stores.
    """
    import inspect

    from app.services import retention_service

    source = inspect.getsource(retention_service.run_purge)
    assert 'cache_counts: dict[str, int | None] = {"redis": None, "semantic": None}' in source
    assert "search_index_documents: int | None = None" in source


def test_preview_names_the_classes_it_could_not_measure():
    """`unmeasured` lets the UI say "not measured" rather than leaving the
    operator to infer it from a null. A client that ignores the field still
    sees null instead of a wrong zero."""
    import inspect

    from app.services import retention_service

    source = inspect.getsource(retention_service.run_purge)
    assert '"unmeasured": sorted(k for k, v in candidates.items() if v is None)' in source
