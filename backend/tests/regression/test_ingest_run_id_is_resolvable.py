"""The ``run_id`` an ingest returns must be one the caller can actually fetch.

Found by re-ingesting the same build number against the live deployment and
following the id the API handed back:

```
POST /api/v1/ingest  ->  202 {"run_id": "3168f853-…", "total_results": 10}
GET  /api/v1/runs/3168f853-…  ->  404 {"detail":"Test run not found"}
select count(*) from test_runs where id='3168f853-…'  ->  0
```

The row never existed. Ingest is asynchronous — 202 plus a Celery task — so the
router minted a fresh ``uuid4()``, handed it to the worker and returned it. The
pipeline then deduped on ``(project_id, build_number)``, **reused** the existing
run and discarded the minted id.

The deduplication itself is correct and worth keeping: re-ingesting did not
double a single count, and the project still held exactly one run. Only the
response was wrong.

It matters because of *when* duplicate build numbers happen: **CI retries**. The
callers most likely to hit this are the automated ones that POST results and
then poll or link ``/runs/{run_id}`` — and they were handed a dead id behind an
HTTP 202 that said everything succeeded.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.routers.ingest import _resolve_run_id


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DB:
    def __init__(self, existing=None):
        self.existing = existing
        self.queries = 0

    async def execute(self, *_args, **_kwargs):
        self.queries += 1
        return _Result(self.existing)


class _ManualAwareDB:
    def __init__(self, manual_id):
        self.manual_id = manual_id
        self.statements = []

    async def execute(self, statement, *_args, **_kwargs):
        sql = str(statement)
        self.statements.append(sql)
        if "test_runs.ingestion_source !=" in sql:
            return _Result(None)
        if len(self.statements) == 1:
            return _Result(None)
        return _Result(self.manual_id)


@pytest.mark.asyncio
async def test_an_existing_build_number_returns_the_run_it_will_land_on():
    """The regression. A CI retry must get back the id that exists."""
    existing = uuid.uuid4()
    db = _DB(existing=existing)
    resolved = await _resolve_run_id(db, uuid.uuid4(), "build-42")
    assert resolved == str(existing)


@pytest.mark.asyncio
async def test_a_new_build_number_gets_a_fresh_id():
    db = _DB(existing=None)
    resolved = await _resolve_run_id(db, uuid.uuid4(), "build-new")
    assert uuid.UUID(resolved)  # parses — a real uuid, not an echo of the input


@pytest.mark.asyncio
async def test_context_free_batch_does_not_resolve_to_manual_upload():
    project_id = uuid.uuid4()
    manual_id = uuid.uuid4()
    db = _ManualAwareDB(manual_id)

    resolved = await _resolve_run_id(db, project_id, "build-42")

    assert resolved != str(manual_id)
    assert uuid.UUID(resolved)
    assert len(db.statements) == 2
    assert "test_runs.ingestion_source !=" in db.statements[1]


@pytest.mark.asyncio
async def test_the_returned_id_is_always_a_uuid():
    """Callers put this straight into a URL. Anything unparseable would 422 at
    the path validator rather than 404, which is a worse failure to diagnose."""
    for existing in (uuid.uuid4(), None):
        resolved = await _resolve_run_id(_DB(existing=existing), uuid.uuid4(), "b")
        assert uuid.UUID(resolved)


@pytest.mark.asyncio
async def test_no_build_number_skips_the_lookup_entirely():
    """Nothing to dedupe against — do not spend a query proving it."""
    db = _DB(existing=None)
    resolved = await _resolve_run_id(db, uuid.uuid4(), None)
    assert uuid.UUID(resolved)
    assert db.queries == 0


@pytest.mark.asyncio
async def test_an_empty_build_number_also_skips_the_lookup():
    db = _DB(existing=None)
    await _resolve_run_id(db, uuid.uuid4(), "")
    assert db.queries == 0


@pytest.mark.asyncio
async def test_the_lookup_is_scoped_to_the_project():
    """Build numbers are only unique within a project — ``build-1`` exists in
    plenty of them. An unscoped match would hand one tenant another tenant's
    run id, which is far worse than a 404."""
    source = inspect.getsource(_resolve_run_id)
    assert "TestRun.project_id == project_id" in source
    assert "TestRun.build_number == build_number" in source


@pytest.mark.asyncio
async def test_it_costs_at_most_one_query():
    """This sits on the ingest hot path, which is the highest-volume endpoint
    in the product."""
    db = _DB(existing=uuid.uuid4())
    await _resolve_run_id(db, uuid.uuid4(), "build-42")
    assert db.queries == 1


def test_only_ci_batch_resolves_existing_build_ids():
    """CI/JSON retries deduplicate by build label; manual uploads are isolated.
    """
    import app.routers.ingest as ingest

    assert "_resolve_run_id" in inspect.getsource(ingest.ingest_batch)
    assert "_resolve_run_id" not in inspect.getsource(ingest.ingest_file)


def test_manual_path_mints_an_id_blind_by_design():
    """Manual submissions intentionally mint a fresh ID before enqueueing."""
    import app.routers.ingest as ingest

    assert "run_id = str(uuid.uuid4())" in inspect.getsource(ingest.ingest_file)
    assert "run_id = str(uuid.uuid4())" not in inspect.getsource(ingest.ingest_batch)


def test_new_identity_is_deterministic_for_concurrent_first_deliveries():
    """The router derives one provisional UUID for one source identity."""
    source = inspect.getsource(_resolve_run_id)
    assert "uuid.uuid5" in source
    assert "ingestion_identity" in source
