"""S3 — per-project storage footprint.

The product could report how many rows a purge would remove and never how many
bytes it would reclaim. These tests pin the two properties that make the answer
trustworthy: an unreached store is reported as unreached (never as zero), and
an estimated byte figure is never presented as an exact one.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import storage_accounting_service as svc


class _FailingStorage:
    """A store that is down — not a store that is empty.

    The distinction is the entire point of the ``measured`` flag, and a mock
    that returns ``[]`` cannot test it: an empty list and an outage produce the
    same zero unless the code actually distinguishes them.
    """

    async def list_objects(self, prefix, bucket=None):
        raise ConnectionError("minio unreachable")


class _FakeStorage:
    def __init__(self, objects_by_prefix):
        self._objects = objects_by_prefix
        self.calls: list[str] = []

    async def list_objects(self, prefix, bucket=None):
        self.calls.append(prefix)
        return self._objects.get(prefix, [])


class _FailingMongoCollection:
    async def count_documents(self, *_a, **_kw):
        raise ConnectionError("mongo unreachable")


class _FailingMongo:
    def __getitem__(self, _name):
        return _FailingMongoCollection()

    async def command(self, *_a, **_kw):
        raise ConnectionError("mongo unreachable")


class _FakeMongoCollection:
    def __init__(self, count):
        self._count = count

    async def count_documents(self, *_a, **_kw):
        return self._count


class _FakeMongo:
    def __init__(self, per_collection_count, avg_obj_size):
        self._count = per_collection_count
        self._avg = avg_obj_size

    def __getitem__(self, _name):
        return _FakeMongoCollection(self._count)

    async def command(self, *_a, **_kw):
        return {"avgObjSize": self._avg}


def _db_with_runs(mocker, rows, run_count=0, case_count=0):
    """Session stub: one ``execute`` for the run rows, two ``scalar`` counts."""
    db = mocker.AsyncMock()
    result = mocker.MagicMock()
    result.all.return_value = rows
    db.execute.return_value = result
    db.scalar.side_effect = [run_count, case_count]
    return db


# ── object storage: exact bytes, and outage ≠ empty ──────────────────────────


@pytest.mark.asyncio
async def test_object_storage_bytes_are_summed_from_object_metadata(mocker):
    """Bytes are actual, not estimated — every listed object carries ``Size``."""
    pid = uuid.uuid4()
    storage = _FakeStorage(
        {
            f"uploads/{pid}/": [
                {"Key": "a", "Size": 100},
                {"Key": "b", "Size": 250},
            ]
        }
    )
    db = _db_with_runs(mocker, rows=[])

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=storage
    )
    obj = next(s for s in out.stores if s.store == "object_storage")

    assert obj.measured is True
    assert obj.exact is True
    assert obj.bytes_ == 350
    assert obj.items == 2


@pytest.mark.asyncio
async def test_uploads_tree_is_listed_once_not_per_run(mocker):
    """A per-run loop over the uploads tree is O(runs) LIST calls.

    On a 100k-run project that is ~100k paginated round-trips for a number
    shown on a settings page.
    """
    pid = uuid.uuid4()
    rows = [(uuid.uuid4(), None) for _ in range(50)]
    storage = _FakeStorage({})
    db = _db_with_runs(mocker, rows=rows)

    await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=storage
    )

    assert storage.calls.count(f"uploads/{pid}/") == 1


@pytest.mark.asyncio
async def test_an_object_shared_by_two_prefixes_is_counted_once(mocker):
    """The uploads tree and a run prefix can overlap.

    Double-counting would inflate the headline reclaimable figure, which is
    the number an operator uses to decide whether to enable a destructive job.
    """
    pid = uuid.uuid4()
    run_id = uuid.uuid4()
    shared = {"Key": "uploads/x/dup", "Size": 500}
    storage = _FakeStorage(
        {f"uploads/{pid}/": [shared], "runs/abc/": [shared]}
    )
    db = _db_with_runs(mocker, rows=[(run_id, "runs/abc/")])

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=storage
    )
    obj = next(s for s in out.stores if s.store == "object_storage")

    assert obj.bytes_ == 500
    assert obj.items == 1


@pytest.mark.asyncio
async def test_unreachable_object_store_reports_not_measured_never_zero(mocker):
    """The RET-D15 rule: an outage must not render as an empty project.

    Asserted against a client that RAISES. A mock returning ``[]`` would pass
    against code that reports 0 for both cases, which is the bug.
    """
    pid = uuid.uuid4()
    db = _db_with_runs(mocker, rows=[])

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=_FailingStorage()
    )
    obj = next(s for s in out.stores if s.store == "object_storage")

    assert obj.measured is False
    assert obj.bytes_ is None, "an unreachable store must not report 0 bytes"
    assert obj.items is None
    assert obj.unreachable_reason == "ConnectionError"
    assert out.fully_measured is False


@pytest.mark.asyncio
async def test_one_store_failing_does_not_lose_the_others(mocker):
    """Degrade per store. A reporting page is more useful partly right."""
    pid = uuid.uuid4()
    db = _db_with_runs(mocker, rows=[], run_count=7, case_count=3)

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=_FailingStorage()
    )

    assert {s.store for s in out.stores} == {"object_storage", "postgres", "mongo"}
    assert next(s for s in out.stores if s.store == "postgres").measured is True
    assert next(s for s in out.stores if s.store == "object_storage").measured is False


# ── totals: an estimate must never masquerade as exact ───────────────────────


@pytest.mark.asyncio
async def test_total_is_flagged_an_estimate_when_any_part_is(mocker):
    """Mongo bytes are derived from avgObjSize, so the total is an estimate.

    Summing an exact figure and an estimate yields an estimate; presenting it
    as measured is the confident-but-invented number this page has shipped
    before.
    """
    pid = uuid.uuid4()
    storage = _FakeStorage({f"uploads/{pid}/": [{"Key": "a", "Size": 100}]})
    db = _db_with_runs(mocker, rows=[(uuid.uuid4(), None)])

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(2, 50), storage=storage
    )

    mongo = next(s for s in out.stores if s.store == "mongo")
    assert mongo.exact is False
    assert mongo.estimate_basis
    assert out.total_is_estimate is True


@pytest.mark.asyncio
async def test_total_is_none_when_nothing_could_be_measured(mocker):
    """An unreachable everything must not total to zero bytes."""
    pid = uuid.uuid4()
    db = mocker.AsyncMock()
    db.execute.side_effect = ConnectionError("postgres unreachable")

    with pytest.raises(ConnectionError):
        # The run-id query is the one failure this service does NOT swallow:
        # without it there is no project scope at all, and reporting an
        # unscoped footprint would be worse than failing.
        await svc.project_storage_footprint(
            db, pid, mongo=_FailingMongo(), storage=_FailingStorage()
        )


@pytest.mark.asyncio
async def test_postgres_reports_exact_rows_and_no_invented_bytes(mocker):
    """Postgres bytes are deliberately null.

    Rows share tables across projects, and a bulk DELETE does not return disk
    to the OS without VACUUM FULL — so a per-project byte figure would be an
    invention twice over. Counts are exact; bytes are absent and explained.
    """
    pid = uuid.uuid4()
    db = _db_with_runs(mocker, rows=[], run_count=4, case_count=96)

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )
    pg = next(s for s in out.stores if s.store == "postgres")

    assert pg.measured is True
    assert pg.items == 100
    assert pg.bytes_ is None
    assert "VACUUM FULL" in (pg.estimate_basis or "")


@pytest.mark.asyncio
async def test_unreachable_mongo_reports_not_measured(mocker):
    pid = uuid.uuid4()
    db = _db_with_runs(mocker, rows=[(uuid.uuid4(), None)])

    out = await svc.project_storage_footprint(
        db, pid, mongo=_FailingMongo(), storage=_FakeStorage({})
    )
    mongo = next(s for s in out.stores if s.store == "mongo")

    assert mongo.measured is False
    assert mongo.bytes_ is None
    assert mongo.items is None


@pytest.mark.asyncio
async def test_per_run_prefix_listing_is_capped(mocker):
    """An unbounded LIST-per-prefix loop is the failure mode being avoided.

    ``TestRun.minio_prefix`` is uploader-derived and unbounded in shape, so it
    cannot be collapsed into one prefix the way the uploads tree can.
    """
    pid = uuid.uuid4()
    over = svc.MAX_RUN_PREFIXES + 25
    rows = [(uuid.uuid4(), f"runs/{i}/") for i in range(over)]
    storage = _FakeStorage({})
    db = _db_with_runs(mocker, rows=rows)

    await svc.project_storage_footprint(
        db, pid, mongo=_FakeMongo(0, 0), storage=storage
    )

    # uploads tree + at most MAX_RUN_PREFIXES run prefixes
    assert len(storage.calls) <= svc.MAX_RUN_PREFIXES + 1


def test_payload_renames_bytes_field_for_the_wire():
    """``bytes_`` avoids shadowing the builtin in Python; the wire says ``bytes``."""
    fp = svc.StoreFootprint(store="s", measured=True, exact=True, bytes_=5, items=1)
    payload = fp.as_payload()

    assert payload["bytes"] == 5
    assert "bytes_" not in payload


# ── deleted projects: the data nothing will ever reclaim ─────────────────────


def _deleted_rows_db(mocker, deleted_rows, run_rows=None):
    """Session stub: first execute returns the deleted-project rows, then each
    per-project footprint call returns the run rows and its two scalar counts."""
    db = mocker.AsyncMock()
    deleted_result = mocker.MagicMock()
    deleted_result.all.return_value = deleted_rows
    run_result = mocker.MagicMock()
    run_result.all.return_value = run_rows or []
    db.execute.side_effect = [deleted_result] + [run_result] * len(deleted_rows)
    db.scalar.side_effect = [0, 0] * len(deleted_rows)
    return db


@pytest.mark.asyncio
async def test_a_project_that_never_opted_in_is_flagged_unreachable(mocker):
    """The default case, and the whole point of the endpoint.

    `enabled` defaults False, and the nightly beat selects on `enabled` alone.
    So a project deleted without retention on is purged by nothing, ever —
    while one that opted in before deletion is still swept, because the beat
    does NOT filter `is_active`.
    """
    opted_in, never = uuid.uuid4(), uuid.uuid4()
    db = _deleted_rows_db(
        mocker,
        [(opted_in, "Swept", True), (never, "Stranded", None)],
    )

    report = await svc.deleted_project_footprints(
        db, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )

    by_name = {p.name: p for p in report.projects}
    assert by_name["Swept"].reachable_by_retention is True
    assert by_name["Stranded"].reachable_by_retention is False
    assert report.unreachable_by_retention == 1


@pytest.mark.asyncio
async def test_a_disabled_policy_row_is_still_unreachable(mocker):
    """A policy row that exists but is switched off reaches nothing.

    `enabled=False` and "no row at all" are different states in the project
    list, but identical to the beat — neither is ever swept.
    """
    db = _deleted_rows_db(mocker, [(uuid.uuid4(), "Disabled", False)])

    report = await svc.deleted_project_footprints(
        db, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )

    assert report.projects[0].reachable_by_retention is False
    assert report.unreachable_by_retention == 1


@pytest.mark.asyncio
async def test_a_capped_scan_says_so_rather_than_understating(mocker):
    """A silently truncated total would understate the number this endpoint
    exists to surface — which is the failure it is meant to prevent."""
    rows = [(uuid.uuid4(), f"P{i}", None) for i in range(5)]
    db = _deleted_rows_db(mocker, rows[:2])
    # The service sees 5 total but is asked to measure 2.
    deleted_result = mocker.MagicMock()
    deleted_result.all.return_value = rows
    run_result = mocker.MagicMock()
    run_result.all.return_value = []
    db.execute.side_effect = [deleted_result] + [run_result] * 2
    db.scalar.side_effect = [0, 0] * 2

    report = await svc.deleted_project_footprints(
        db, limit=2, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )

    assert report.projects_total == 5
    assert len(report.projects) == 2
    assert report.truncated is True


@pytest.mark.asyncio
async def test_no_deleted_projects_is_not_truncated(mocker):
    db = _deleted_rows_db(mocker, [])

    report = await svc.deleted_project_footprints(
        db, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )

    assert report.projects_total == 0
    assert report.truncated is False
    assert report.unreachable_by_retention == 0
    assert report.total_bytes is None, "nothing measured must not total to zero"


@pytest.mark.asyncio
async def test_the_scan_actually_filters_on_deleted_projects(mocker):
    """Assert the predicate, not just the rows the mock hands back.

    Caught by mutation: flipping ``is_active.is_(False)`` to ``is_(True)`` —
    scanning ACTIVE projects and calling them deleted — passed every other test
    in this file. A mocked session returns its canned rows whatever the WHERE
    clause says, so nothing above can see the filter at all.

    Compiling the statement is the cheap way to see it. The alternative is a
    real-database integration test, which is worth having and is not this.
    """
    db = _deleted_rows_db(mocker, [])

    await svc.deleted_project_footprints(
        db, mongo=_FakeMongo(0, 0), storage=_FakeStorage({})
    )

    stmt = db.execute.await_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "is_active" in compiled
    assert "is_active = false" in compiled or "is_active is false" in compiled, (
        f"the scan must select DELETED projects; compiled WHERE was: {compiled}"
    )
