"""Unit tests for project_reset_service.

The service is the heart of the destructive "reset project" feature, so
the tests focus on the safety invariants — confirmation_name must match
exactly, unknown modes are rejected, the deletes go through CASCADE (we
don't enumerate every cascaded table), and the audit log is written on
every successful run.

Integration tests against a real Postgres live separately under
tests/integration/; this file pins the contract with mock sessions so
it can run in the unit suite.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        # ``_next_free_auto_name`` reads every existing release name to pick a
        # free one; after a full reset there are none.
        return SimpleNamespace(all=lambda: list(self._scalars))

    def all(self):
        # W22a (deliberate): the retention-hold read
        # (``retention_hold_service.any_live_hold``, ``.all()``) — no live hold.
        return []


def _project(name: str = "GoogleSearch", *, active: bool = True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        is_active=active,
    )


def _fake_db(*, project, run_count=0, full_table_counts=None):
    """Mock async session: returns canned counts and swallows deletes.

    Sequence of ``execute`` calls inside reset_project:
      1. SELECT project by id   → _Result(scalar=project)
      1b. SELECT live retention holds (W22a, after the confirmation check)
                                → _Result() — ``.all()`` is ``[]``, nothing held
      2. SELECT COUNT test_runs → _Result(scalar=run_count)
      3. DELETE test_runs       → return value not read
      [if mode == "full"]
      4..N: alternating SELECT COUNT + DELETE for each table in
            _FULL_RESET_TABLES — 2 calls per table.
      last: full mode wipes ``releases``, so reset_project re-provisions the
            project's active release in the same transaction (migration 0150).
            That is one more SELECT, returning None so a fresh placeholder is
            created rather than an existing one reused.
    """
    results: list[object] = [
        _Result(scalar=project),
        _Result(),
        _Result(scalar=run_count),
        MagicMock(),
    ]
    if full_table_counts is not None:
        for n in full_table_counts:
            results.append(_Result(scalar=n))
            results.append(MagicMock())
        # _create_auto_release: the reusable-placeholder probe, then the
        # name-collision lookup. Both empty — everything was just deleted.
        results.append(_Result(scalar=None))
        results.append(_Result(scalars=[]))

    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=results),
        add=MagicMock(),
        commit=AsyncMock(),
        flush=AsyncMock(),
        begin_nested=MagicMock(return_value=savepoint),
    )
    return db


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_project_runs_mode_deletes_runs_only():
    from app.services.project_reset_service import reset_project

    project = _project()
    db = _fake_db(project=project, run_count=42)

    result = await reset_project(
        db,
        project_id=project.id,
        mode="runs",
        confirmation_name=project.name,
    )

    # W32 F-7 (deliberate): the result always carries ``documents``; with no
    # Mongo store given it is ``None`` (not measured), never zeros.
    assert result == {"mode": "runs", "deleted": {"test_runs": 42}, "documents": None}
    db.commit.assert_awaited_once()
    # An audit log row is added on success.
    assert db.add.called


@pytest.mark.asyncio
async def test_reset_project_full_mode_wipes_catalog_tables():
    from app.services.project_reset_service import (
        _FULL_RESET_TABLES,
        reset_project,
    )

    project = _project()
    # Synthesise a count per full-reset table so the audit map matches.
    per_table = list(range(1, len(_FULL_RESET_TABLES) + 1))
    db = _fake_db(project=project, run_count=5, full_table_counts=per_table)

    result = await reset_project(
        db,
        project_id=project.id,
        mode="full",
        confirmation_name=project.name,
    )

    assert result["mode"] == "full"
    assert result["deleted"]["test_runs"] == 5
    for (name, _model), expected in zip(_FULL_RESET_TABLES, per_table):
        assert result["deleted"][name] == expected
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_project_confirmation_mismatch_aborts_before_any_delete():
    from app.services.project_reset_service import (
        ConfirmationMismatch,
        reset_project,
    )

    project = _project(name="GoogleSearch")
    db = _fake_db(project=project, run_count=99)

    with pytest.raises(ConfirmationMismatch):
        await reset_project(
            db,
            project_id=project.id,
            mode="runs",
            confirmation_name="googlesearch",  # case mismatch — must reject
        )

    # Nothing was committed; the only execute() call was the project lookup.
    db.commit.assert_not_awaited()
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_reset_project_unknown_project_raises_project_not_found():
    from app.services.project_reset_service import ProjectNotFound, reset_project

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(scalar=None)),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    with pytest.raises(ProjectNotFound):
        await reset_project(
            db,
            project_id=uuid.uuid4(),
            mode="runs",
            confirmation_name="whatever",
        )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_project_inactive_project_is_treated_as_missing():
    from app.services.project_reset_service import ProjectNotFound, reset_project

    project = _project(active=False)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(scalar=project)),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    with pytest.raises(ProjectNotFound):
        await reset_project(
            db,
            project_id=project.id,
            mode="runs",
            confirmation_name=project.name,
        )


@pytest.mark.asyncio
async def test_reset_project_unknown_mode_rejected():
    from app.services.project_reset_service import reset_project

    db = SimpleNamespace(execute=AsyncMock(), add=MagicMock(), commit=AsyncMock())

    with pytest.raises(ValueError, match="Unknown reset mode"):
        await reset_project(
            db,
            project_id=uuid.uuid4(),
            mode="everything",  # not in the Literal type — backend must still guard
            confirmation_name="whatever",
        )


@pytest.mark.asyncio
async def test_reset_project_acquires_for_update_lock_on_project_row():
    """P2-5: the project lookup must use ``SELECT ... FOR UPDATE`` so
    two concurrent admin clicks serialise. Without the lock the second
    call would race the first's cascade and produce a phantom success
    against an already-empty project."""
    from app.services.project_reset_service import reset_project

    project = _project()
    db = _fake_db(project=project, run_count=0)

    await reset_project(
        db,
        project_id=project.id,
        mode="runs",
        confirmation_name=project.name,
    )

    # First execute() is the project lookup. Its compiled SQL must
    # carry the FOR UPDATE clause. We inspect the actual statement
    # object passed to execute().
    first_call = db.execute.await_args_list[0]
    stmt = first_call.args[0]
    compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in compiled_sql.upper(), (
        f"Project lookup must use SELECT ... FOR UPDATE so concurrent "
        f"resets serialise. Got: {compiled_sql}"
    )


# ── W32 F-7: the runs' Mongo documents go first ──────────────────────────────


class _MongoStub:
    """``delete_many`` journals the collection and answers ``len($in)``."""

    def __init__(self, journal: list, *, fail: bool = False):
        self._journal, self._fail = journal, fail

    def __getitem__(self, name):
        async def delete_many(flt):
            if self._fail:
                raise RuntimeError("mongo unavailable")
            self._journal.append(("mongo", name))
            ((_field, cond),) = flt.items()
            return SimpleNamespace(deleted_count=len(cond["$in"]))

        return SimpleNamespace(delete_many=delete_many)


def _mongo_phase_db(*, project, run_ids, journal):
    """The ``_fake_db`` sequence with the Mongo phase's three reads (run ids,
    pipeline ids, live-session slugs) between the count and the delete, and a
    one-page case stream. Every execute / commit is journaled."""
    results = iter([
        _Result(scalar=project),
        _Result(),
        _Result(scalar=len(run_ids)),
        _Result(scalars=run_ids),
        _Result(scalars=[]),
        _Result(scalars=[]),
        MagicMock(),
    ])

    async def execute(stmt):
        journal.append(("delete", "") if getattr(stmt, "is_delete", False) else ("select", ""))
        return next(results)

    class _Stream:
        async def partitions(self, _size):
            yield [uuid.uuid4()]

    async def stream_scalars(_stmt):
        return _Stream()

    return SimpleNamespace(
        execute=execute,
        stream_scalars=stream_scalars,
        add=MagicMock(),
        commit=AsyncMock(side_effect=lambda: journal.append(("commit", ""))),
        flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_a_store_deletes_the_documents_first_and_records_them():
    from app.services.project_reset_service import reset_project
    from app.services.retention_service import _MONGO_PLANS

    project, journal = _project(), []
    db = _mongo_phase_db(project=project, run_ids=[uuid.uuid4() for _ in range(3)], journal=journal)

    result = await reset_project(
        db, project_id=project.id, mode="runs", confirmation_name=project.name,
        mongo=_MongoStub(journal),
    )

    documents = result["documents"]
    assert set(documents) == {c for c, _f, _a in _MONGO_PLANS}
    assert documents["ai_analysis_payloads"] == 1 and documents["pipeline_event_log"] == 0
    assert documents["run_summaries"] == 3 and documents["decision_reports"] == 3
    assert db.add.call_args.args[0].changed_fields["documents"] == documents
    kinds = [k for k, _ in journal]
    assert kinds.index("mongo") < kinds.index("delete") < kinds.index("commit"), (
        "the Mongo phase must run while the run → document mapping still exists"
    )


@pytest.mark.asyncio
async def test_no_store_means_documents_not_measured_and_no_runs_means_empty():
    from app.services.project_reset_service import reset_project

    project = _project()
    db = _fake_db(project=project, run_count=3)
    result = await reset_project(db, project_id=project.id, mode="runs", confirmation_name=project.name)
    assert result["documents"] is None
    assert db.add.call_args.args[0].changed_fields["documents"] is None

    journal: list = []
    empty = _fake_db(project=project, run_count=0)
    result = await reset_project(
        empty, project_id=project.id, mode="runs", confirmation_name=project.name,
        mongo=_MongoStub(journal),
    )
    assert result["documents"] == {} and journal == []


@pytest.mark.asyncio
async def test_a_mongo_error_fails_the_reset_before_any_delete_or_commit():
    from app.services.project_reset_service import reset_project

    project, journal = _project(), []
    db = _mongo_phase_db(project=project, run_ids=[uuid.uuid4()], journal=journal)

    with pytest.raises(RuntimeError, match="mongo unavailable"):
        await reset_project(
            db, project_id=project.id, mode="runs", confirmation_name=project.name,
            mongo=_MongoStub(journal, fail=True),
        )

    db.commit.assert_not_awaited()
    assert ("delete", "") not in journal and not db.add.called
