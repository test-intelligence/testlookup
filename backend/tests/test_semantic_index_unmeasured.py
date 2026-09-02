"""Indexing must not report a failure as "indexed nothing".

Sibling to ``test_retention_unmeasured_stores`` — the same defect class on a
different surface. ``index_test_cases`` and ``index_incremental`` returned ``0``
on every failure path, and the reindex Celery task put that straight into
``{"indexed_count": 0}``, so a run where ChromaDB was down was indistinguishable
from a successful run that found nothing new to index.

The third path is the sharpest: the embedder is exercised at upsert, so a
failure there can happen *after* earlier batches have already landed. Zero
there does not merely fail to measure — it contradicts work that persisted.
"""
from __future__ import annotations

import pytest

from app.services import semantic_search


@pytest.mark.asyncio
async def test_full_index_reports_none_when_the_store_is_unreachable(
    monkeypatch, mocker
):
    async def _boom():
        raise ConnectionError("chromadb unavailable")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)

    result = await semantic_search.index_test_cases(mocker.AsyncMock())

    assert result is None, "an unreachable store must not report 'indexed 0'"


@pytest.mark.asyncio
async def test_incremental_index_reports_none_when_the_store_is_unreachable(
    monkeypatch, mocker
):
    async def _boom():
        raise ConnectionError("chromadb unavailable")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)

    result = await semantic_search.index_incremental(mocker.AsyncMock())

    assert result is None


@pytest.mark.asyncio
async def test_incremental_index_reports_none_when_the_embedder_fails_midrun(
    monkeypatch, mocker
):
    """The path where 0 was actively wrong, not merely uninformative.

    Earlier batches may already have been written and the Redis cursor holds
    the real progress. Reporting 0 would contradict work that persisted.
    """

    async def _ok():
        return mocker.MagicMock()

    async def _upsert_boom(*_a, **_kw):
        raise RuntimeError("embedder unavailable")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _ok)
    monkeypatch.setattr(
        semantic_search, "_upsert_rows_to_collection", _upsert_boom
    )
    # A cursor read that yields no starting point keeps the query path simple;
    # the assertion is about the upsert failure, not the cursor.
    monkeypatch.setattr(semantic_search, "_get_redis", lambda: None, raising=False)

    db = mocker.AsyncMock()
    rows_result = mocker.MagicMock()
    rows_result.all.return_value = [(1, "t", "c", "s", "e", "p")]
    db.execute.return_value = rows_result

    result = await semantic_search.index_incremental(db)

    assert result is None


def test_indexing_signatures_admit_the_unmeasured_case():
    """A plain ``-> int`` forces the implementation to invent a number.

    Pinning the annotation keeps a future edit from quietly narrowing it back
    and reintroducing the zero.
    """
    import inspect

    for fn in (semantic_search.index_test_cases, semantic_search.index_incremental):
        annotation = inspect.signature(fn).return_annotation
        assert "None" in str(annotation), (
            f"{fn.__name__} must be able to say 'could not measure'; "
            f"got {annotation!r}"
        )


def test_reindex_task_reports_whether_the_count_is_real():
    """The task result is what the triggering operator reads.

    ``indexed_count: 0`` on a failed run reads as a successful no-op, which is
    the whole defect. The result now carries both the (nullable) count and an
    explicit flag, so a consumer that reads either one gets the truth.
    """
    import inspect

    from app.worker import tasks

    source = inspect.getsource(tasks.reindex_search)
    assert '"indexing_measured": count is not None' in source
    assert '"indexed_count": count,' in source
