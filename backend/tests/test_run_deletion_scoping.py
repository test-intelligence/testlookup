"""S2c — the guards that make a single-run delete safe to expose.

The nightly purge is bounded by things a per-run endpoint does not have: it
only ever touches projects that opted in, it only ever selects rows already
past a cutoff, and nobody triggers it against a run they picked by hand. Every
test here pins a boundary that existed only as one of those accidents.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import semantic_search  # noqa: E402


# ── RET-D8: deleting one run must not empty the project's index ──────────────


class _Collection:
    """Stands in for the Chroma collection, recording what it was asked."""

    def __init__(self, docs):
        #: {id: metadata}
        self._docs = dict(docs)
        self.get_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    @staticmethod
    def _match(meta, clause):
        """Evaluate one Chroma-style where clause against one document.

        The first version of this closed over the OUTER clause and recursed on
        itself, so every ``$and`` blew the stack — and the production code
        under test was blamed for it.
        """
        for key, value in (clause or {}).items():
            if key == "$and":
                if not all(_Collection._match(meta, sub) for sub in value):
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


def _index(monkeypatch, collection):
    async def _get():
        return collection

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _get)


@pytest.fixture
def two_runs():
    """One project, two runs: three documents on the doomed run, two on the
    survivor. The survivor is the entire point of the fixture."""
    project = str(uuid.uuid4())
    doomed, survivor = str(uuid.uuid4()), str(uuid.uuid4())
    docs = {
        f"tc{i}": {"project_id": project, "test_run_id": doomed}
        for i in range(3)
    }
    docs.update({
        f"keep{i}": {"project_id": project, "test_run_id": survivor}
        for i in range(2)
    })
    return project, doomed, survivor, docs


@pytest.mark.asyncio
async def test_purging_one_run_leaves_the_other_runs_indexed(
    monkeypatch, two_runs
):
    """RET-D8, the whole reason this function exists.

    ``purge_project_documents`` deletes on ``{"project_id": ...}``. Calling it
    from a per-run delete would wipe every embedding the project owns, and
    nothing would report it: the caller asked to delete one run and would be
    told a number that looked plausible.
    """
    project, doomed, survivor, docs = two_runs
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    removed = await semantic_search.purge_run_documents(
        project, doomed, execute=True
    )

    assert removed == 3
    remaining = set(collection._docs)
    assert remaining == {"keep0", "keep1"}, (
        "the other run's embeddings were destroyed by a single-run delete"
    )


@pytest.mark.asyncio
async def test_the_scoped_purge_filters_on_the_project_too(
    monkeypatch, two_runs
):
    """Run ids are UUIDs so a cross-project collision is not the worry.

    The project filter is defence in depth for the case the collection is ever
    shared: a where-clause naming only the run would be correct today and
    silently wrong after that change.
    """
    project, doomed, _survivor, docs = two_runs
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    await semantic_search.purge_run_documents(project, doomed, execute=True)

    where = collection.delete_calls[0]["where"]
    flat = repr(where)
    assert project in flat and doomed in flat, (
        "the delete must name both the project and the run"
    )


@pytest.mark.asyncio
async def test_preview_counts_without_deleting(monkeypatch, two_runs):
    """execute=False is what the DELETE endpoint's confirmation screen reads."""
    project, doomed, _survivor, docs = two_runs
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    count = await semantic_search.purge_run_documents(
        project, doomed, execute=False
    )

    assert count == 3
    assert collection.delete_calls == [], "preview deleted documents"
    assert len(collection._docs) == 5


@pytest.mark.asyncio
async def test_an_unreachable_index_reports_none_not_zero(monkeypatch):
    """0 and "could not look" are opposite findings.

    A run delete that could not reach the vector store must not report having
    tidily removed nothing from it.
    """
    async def _boom():
        raise ConnectionError("chroma down")

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", _boom)

    assert await semantic_search.purge_run_documents(
        str(uuid.uuid4()), str(uuid.uuid4()), execute=True
    ) is None


@pytest.mark.asyncio
async def test_a_failure_midway_reports_none_not_a_partial_count(
    monkeypatch, two_runs
):
    """The collection answered `get` and then failed the delete.

    Returning the pre-delete count here would report documents as removed that
    are still in the store.
    """
    project, doomed, _survivor, docs = two_runs

    class _HalfBroken(_Collection):
        def delete(self, where=None):
            raise RuntimeError("delete rejected")

    _index(monkeypatch, _HalfBroken(docs))

    assert await semantic_search.purge_run_documents(
        project, doomed, execute=True
    ) is None


@pytest.mark.asyncio
async def test_a_run_with_no_documents_reports_zero_not_none(
    monkeypatch, two_runs
):
    """The other half: genuinely empty is a measurement, and must not be
    reported as unmeasured or the caller learns nothing from either value."""
    project, _doomed, _survivor, docs = two_runs
    collection = _Collection(docs)
    _index(monkeypatch, collection)

    count = await semantic_search.purge_run_documents(
        project, str(uuid.uuid4()), execute=True
    )

    assert count == 0
    assert len(collection._docs) == 5


def test_the_project_wide_purge_still_exists_for_the_nightly_sweep():
    """Scoping the run case must not remove the project case.

    The nightly purge deletes whole projects' worth of embeddings and is the
    only thing that reclaims the 98.8% measured on the live deployment.
    """
    assert callable(getattr(semantic_search, "purge_project_documents", None))
    assert callable(getattr(semantic_search, "purge_run_documents", None))


# ── refuse to resurrect ──────────────────────────────────────────────────────


def test_every_run_creating_site_is_classified():
    """The EPIC named two resurrection sites. There are five.

    ``persist_live_session`` and the stream stub were the two anyone had
    noticed. The live-session drainer, the live-persist Celery task and
    ``ingestion_pipeline`` all take a CALLER-SUPPLIED run id and create the row
    on a SELECT miss, which is the same hazard by the same mechanism: delete a
    run while any of them is mid-flight and it reappears seconds later, its
    Mongo events, MinIO objects and event archive already gone.

    This test exists so a sixth site cannot be added silently. It is an
    inventory, not a behaviour check — the behaviour is asserted per site
    below — and it fails when the inventory drifts from the source.
    """
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"

    # (module, why it is or is not a resurrection hazard)
    RESURRECTS = {
        "services/stream_service.py": "stub write keyed on the session id",
        "services/live_session_drainer.py": "creates on SELECT miss by run id",
        "services/ingestion_pipeline.py": "id=uuid.UUID(run_id) when supplied",
        "worker/tasks.py": "live-persist creates on SELECT miss by run id",
    }
    EXEMPT = {
        # Keyed on (project, build_number, jenkins_job) with no caller id — a
        # re-upload of the same build is a legitimate new ingest, not the
        # return of a row someone deleted.
        "services/ingestion.py": "no caller-supplied id",
        # Transient: built for display when a live run has no row yet and
        # returned without db.add. Deleting the LiveSession stops it firing.
        "services/runs_service.py": "never persisted",
    }

    found = set()
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?<!class )\bTestRun\(", text):
            found.add(path.relative_to(app_dir).as_posix())

    classified = set(RESURRECTS) | set(EXEMPT)
    assert found == classified, (
        "the set of modules constructing a TestRun changed.\n"
        f"  unclassified: {sorted(found - classified)}\n"
        f"  gone:         {sorted(classified - found)}\n"
        "Classify each: does it accept a caller-supplied run id and create on "
        "a SELECT miss? Then it can resurrect a deleted run and needs the "
        "tombstone check."
    )


@pytest.mark.parametrize(
    "module",
    [
        "app.services.stream_service",
        "app.services.live_session_drainer",
        "app.services.ingestion_pipeline",
        "app.worker.tasks",
    ],
)
def test_each_resurrecting_module_consults_the_tombstone(module):
    """A guard on one site is not a guard.

    Each of these can independently bring a deleted run back; missing any one
    leaves the delete only mostly irreversible, which is worse than not
    offering it — the operator was told the run was gone.
    """
    import importlib
    import inspect as _inspect

    mod = importlib.import_module(module)
    source = _inspect.getsource(mod)
    assert "run_is_tombstoned" in source, (
        f"{module} creates a TestRun from a caller-supplied id but never "
        "checks whether that id was deliberately deleted"
    )


@pytest.mark.asyncio
async def test_the_drainer_creates_nothing_for_a_tombstoned_run(mocker):
    """Behavioural, not a grep.

    The parametrised test above proves the NAME appears in each module. It
    would pass just as happily against a call whose result is discarded, so
    the load-bearing site is exercised directly: with the run tombstoned and
    absent from Postgres, no TestRun may be constructed.

    Deliberately not skippable. An earlier version guessed a private helper
    name and skipped when it did not exist, which reported success for a guard
    it had never reached.
    """
    from app.models.postgres import TestRun
    from app.services import live_session_drainer as drainer

    assert hasattr(drainer, "drain_run_buffer"), (
        "the drainer entry point was renamed — repoint this test rather than "
        "letting it skip"
    )

    added: list = []

    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return None  # the run is gone

        @staticmethod
        def scalar():
            return None

        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

        @staticmethod
        def all():
            return []

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *_a, **_kw):
            return _Result()

        def add(self, obj):
            added.append(obj)

        async def commit(self):
            pass

        async def flush(self):
            pass

        async def rollback(self):
            pass

    class _Redis:
        async def get(self, *_a, **_kw):
            return None

        async def set(self, *_a, **_kw):
            return True

        async def delete(self, *_a, **_kw):
            return 1

        async def hgetall(self, *_a, **_kw):
            return {}

        async def lrange(self, *_a, **_kw):
            return []

        async def llen(self, *_a, **_kw):
            return 0

        async def ltrim(self, *_a, **_kw):
            return True

    mocker.patch.object(drainer, "AsyncSessionLocal", lambda: _DB())
    # get_redis() is called WITHOUT await here — a plain callable, not async.
    mocker.patch.object(drainer, "get_redis", lambda: _Redis())
    tombstoned = mocker.patch.object(
        drainer, "run_is_tombstoned", mocker.AsyncMock(return_value=True)
    )

    await drainer.drain_run_buffer(str(uuid.uuid4()), str(uuid.uuid4()))

    assert not [o for o in added if isinstance(o, TestRun)], (
        "the drainer recreated a run that was deliberately deleted"
    )
    assert tombstoned.await_count or not added, (
        "the tombstone was never consulted on the path that creates the run"
    )


@pytest.mark.asyncio
async def test_the_tombstone_lookup_fails_open(mocker):
    """A bookkeeping query that errors must not discard live test results.

    The failure modes are asymmetric: a resurrected row is visible and an
    operator can delete it again, whereas silently dropping a CI run's results
    is unrecoverable and looks like the run never happened.
    """
    from app.services import run_tombstone_service as svc

    db = mocker.AsyncMock()
    db.execute.side_effect = ConnectionError("pg down")

    assert await svc.run_is_tombstoned(db, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_a_non_uuid_slug_is_not_tombstoned(mocker):
    """LiveSession.run_id is a client slug like ``local-abc12345``.

    It was never a TestRun.id, so it cannot name a tombstone. Passing it
    through ``uuid.UUID()`` unguarded would raise inside the ingest path.
    """
    from app.services import run_tombstone_service as svc

    db = mocker.AsyncMock()
    assert await svc.run_is_tombstoned(db, "local-abc12345") is False
    db.execute.assert_not_awaited()


def test_the_tombstone_is_staged_not_committed():
    """It must land in the SAME transaction as the row deletion.

    Committed separately it either blocks live ingestion into a run that still
    exists, or leaves a window where the run is gone and nothing stops it
    coming back.
    """
    import inspect as _inspect

    from app.services import run_tombstone_service as svc

    source = _inspect.getsource(svc.stage_tombstone)
    assert "db.add(" in source
    assert ".commit(" not in source, (
        "the tombstone writer must not own a commit"
    )


def test_the_tombstone_has_no_foreign_key_to_test_runs():
    """A FK would make the row impossible to write — the run is deleted."""
    from app.models.postgres import RunTombstone

    assert not RunTombstone.__table__.c.run_id.foreign_keys, (
        "run_tombstones.run_id must not reference test_runs; the row it names "
        "has been deleted, which is the entire point of the record"
    )


@pytest.mark.asyncio
async def test_the_tombstone_lookup_rejects_an_answer_it_did_not_ask_for(mocker):
    """``found is not None`` is not the same question as ``found == run_uuid``.

    Any session that returns a value for every query — a bare ``AsyncMock`` is
    the common case — makes the presence check report every run as tombstoned,
    silently disabling the live-stream stub write everywhere. The lookup
    selects the primary key it filtered on, so a genuine hit returns exactly
    the id that was asked for.
    """
    from app.services import run_tombstone_service as svc

    asked = uuid.uuid4()

    class _Result:
        """scalar_one_or_none is SYNC.

        Nesting AsyncMocks made it return a coroutine, which equals nothing —
        so the two negative assertions below passed without exercising the
        comparison at all, and only the positive one noticed.
        """

        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _DB:
        def __init__(self, value):
            self._value = value

        async def execute(self, *_a, **_kw):
            return _Result(self._value)

    assert await svc.run_is_tombstoned(_DB(mocker.Mock()), asked) is False, (
        "a bare mock answer must not read as tombstoned — every session stub "
        "in the suite would silently disable the live-stream stub write"
    )
    assert await svc.run_is_tombstoned(_DB(uuid.uuid4()), asked) is False, (
        "a DIFFERENT run's id must not read as this run being tombstoned"
    )
    assert await svc.run_is_tombstoned(_DB(None), asked) is False
    assert await svc.run_is_tombstoned(_DB(asked), asked) is True


@pytest.mark.asyncio
async def test_create_session_skips_the_stub_for_a_tombstoned_run(mocker):
    """Behavioural, because the module grep cannot see this.

    ``stream_service`` has two resurrection sites and an import line, so a
    source grep for ``run_is_tombstoned`` passes with this call removed — the
    same import-line blind spot that let a mutant survive in S2b.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models.postgres import TestRun
    from app.services.stream_service import create_session

    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()
    db.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    project = SimpleNamespace(id=uuid.uuid4(), name="P")
    payload = SimpleNamespace(
        project_id=str(project.id), client_name="java-sdk", machine_id="ci-1",
        build_number="b-1", framework="testng", branch="main", commit_hash=None,
        total_tests=3, release_name=None, launch_name=None, suite_name=None,
        metadata={},
    )
    redis = AsyncMock()
    redis.setex = AsyncMock()

    with patch("app.services.stream_service.resolve_project",
               AsyncMock(return_value=project)), \
         patch("app.services.stream_service.get_redis", return_value=redis), \
         patch("app.services.stream_service.run_is_tombstoned",
               AsyncMock(return_value=True)), \
         patch("app.streams.live_run_state.RedisLiveRunState.start",
               AsyncMock(return_value=None)):
        response = await create_session(db, payload)

    assert not [r for r in added if isinstance(r, TestRun)], (
        "create_session recreated a TestRun for a deliberately deleted run"
    )
    assert response is not None and response.run_id, (
        "skipping the stub must not abort session creation — refusing the "
        "session outright would drop live results on the floor"
    )
