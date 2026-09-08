from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _EmptyPrefetch:
    def scalars(self):
        return self

    def all(self):
        return []


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("requested, expected_queries", [(32766, 4), (32767, 4), (50000, 5)])
async def test_real_ingestion_prefetch_chunks_large_batches(monkeypatch, requested, expected_queries):
    from app.services import ingestion_pipeline as pipeline

    run = SimpleNamespace(id=__import__('uuid').uuid4(), project_id=__import__('uuid').uuid4(), framework=None)
    results = [
        {"test_name": f"test_{i}", "class_name": "S", "status": "passed", "suite_name": "smoke"}
        for i in range(requested)
    ]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_EmptyPrefetch()),
        add=MagicMock(),
        flush=AsyncMock(),
        begin_nested=MagicMock(side_effect=_Savepoint),
    )
    monkeypatch.setattr(pipeline, "_upsert_test_case", AsyncMock())

    assert await pipeline.ingest_test_results(db, run, results) == requested
    assert db.execute.await_count == expected_queries


def test_quarantine_path_uses_bounded_chunks():
    import inspect
    from app.services import ingestion_pipeline as pipeline
    source = inspect.getsource(pipeline.finalize_run)
    assert "_chunked(list(dict.fromkeys(fingerprints)))" in source
    assert "TestCase.test_fingerprint.in_(fingerprint_chunk)" in source
