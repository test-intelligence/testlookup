"""Re-sending a build whose run was deleted must be refused at upload time.

Seen on a running install (product finding D-05):

```
DELETE /api/v1/runs/{id}          -> tombstone written
POST   /api/v1/ingest (same build) -> 202 {"run_id": "<the same id>"}
```

The run id is derived from the build's identity, so the re-send resolves to the
deleted run's id. The router answered 202 and queued the batch; the worker then
met the tombstone inside ``ingestion_pipeline``, raised, retried and gave up.
The CI job that sent the build was told it had succeeded and the run never
appeared.

The refusal belongs where the caller can still read it: a 409 before anything
is stored or queued.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.routers import ingest as ingest_router


@pytest.mark.asyncio
async def test_a_tombstoned_run_id_is_refused_with_409(monkeypatch):
    run_id = str(uuid.uuid4())

    async def _tombstoned(_db, candidate):
        return str(candidate) == run_id

    monkeypatch.setattr(ingest_router, "run_is_tombstoned", _tombstoned)

    with pytest.raises(HTTPException) as refused:
        await ingest_router._refuse_deleted_build(object(), run_id, "nightly-1858")

    assert refused.value.status_code == 409
    # The message has to tell a CI author what happened and what to do.
    assert "nightly-1858" in refused.value.detail
    assert "deleted" in refused.value.detail
    assert "build" in refused.value.detail


@pytest.mark.asyncio
async def test_a_live_run_id_is_let_through(monkeypatch):
    async def _not_tombstoned(_db, _candidate):
        return False

    monkeypatch.setattr(ingest_router, "run_is_tombstoned", _not_tombstoned)

    assert (
        await ingest_router._refuse_deleted_build(object(), str(uuid.uuid4()), "b-1")
        is None
    )


def test_the_batch_route_refuses_before_it_stores_or_queues():
    """The check is only worth having ahead of the 202.

    Placed after ``put_object`` or ``.delay`` it would leave a stored payload
    and a doomed task behind the refusal.
    """
    source = inspect.getsource(ingest_router.ingest_batch)
    refusal = source.index("_refuse_deleted_build(")
    assert refusal > source.index("_resolve_run_id(")
    assert refusal < source.index("put_object(")
    assert refusal < source.index("ingest_uploaded_results.delay(")
