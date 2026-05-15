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
    def __init__(self, *, scalar=None):
        self._scalar = scalar

    def scalar(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


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
      2. SELECT COUNT test_runs → _Result(scalar=run_count)
      3. DELETE test_runs       → return value not read
      [if mode == "full"]
      4..N: alternating SELECT COUNT + DELETE for each table in
            _FULL_RESET_TABLES — 2 calls per table.
    """
    results: list[object] = [
        _Result(scalar=project),
        _Result(scalar=run_count),
        MagicMock(),
    ]
    if full_table_counts is not None:
        for n in full_table_counts:
            results.append(_Result(scalar=n))
            results.append(MagicMock())

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=results),
        add=MagicMock(),
        commit=AsyncMock(),
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

    assert result == {"mode": "runs", "deleted": {"test_runs": 42}}
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
