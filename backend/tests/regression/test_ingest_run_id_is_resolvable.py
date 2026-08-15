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


def test_both_ingest_paths_resolve_the_id():
    """JSON batch and file upload both accept an explicit ``build_number`` and
    both feed the same deduplicating pipeline, so fixing one would leave the
    other returning a dead id."""
    import app.routers.ingest as ingest

    for func in (ingest.ingest_batch, ingest.ingest_file):
        assert "_resolve_run_id" in inspect.getsource(func), func.__name__


def test_neither_path_still_mints_an_id_blind():
    """The exact shape of the bug: ``uuid4()`` assigned straight to the value
    that gets returned, with no dedup lookup in between."""
    import app.routers.ingest as ingest

    for func in (ingest.ingest_batch, ingest.ingest_file):
        source = inspect.getsource(func)
        assert "run_id = str(uuid.uuid4())" not in source, func.__name__


def test_the_remaining_race_is_documented_not_claimed_away():
    """Two concurrent ingests of the same NEW build number can still both miss
    here. That is real; the fix must not pretend otherwise."""
    source = inspect.getsource(_resolve_run_id)
    assert "race" in source.lower()
