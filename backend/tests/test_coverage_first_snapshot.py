"""D-34: Coverage > History has a point once a project's first run finishes.

Snapshots were written only by the 00:05 UTC beat, so a project created or
imported today read "No coverage snapshot yet" until the next night.
``finalize_run`` now stages the project's FIRST snapshot (for today) when the
project has none; every later run costs one indexed probe.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import coverage_snapshot_service as svc
from app.services import ingestion_pipeline

PROJECT = uuid.UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 11, 23)


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _Probe:
    """Answers the existence probe; records statements."""

    def __init__(self, exists):
        self.exists = exists
        self.calls: list = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return _Result(1 if self.exists else None)


@pytest.mark.asyncio
async def test_a_project_with_no_snapshot_gets_one_for_today(monkeypatch):
    values = {"catalogue_tests": 12, "exercised_tests": 12, "suites": 2, "suite_coverage": []}
    computed = AsyncMock(return_value=values)
    staged = AsyncMock()
    monkeypatch.setattr(svc, "snapshot_values", computed)
    monkeypatch.setattr(svc, "stage_snapshot", staged)
    db = _Probe(exists=False)

    assert await svc.stage_first_snapshot(db, PROJECT, today=TODAY) is True

    computed.assert_awaited_once_with(db, PROJECT, TODAY)
    staged.assert_awaited_once_with(db, PROJECT, TODAY, values)
    (statement, params) = db.calls[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FROM coverage_snapshots WHERE project_id" in sql and "LIMIT 1" in sql
    # Review of D-34: only a finished day ends the first day; today's point is
    # rewritten by each run until then (a bulk upload is not frozen at run one).
    assert "snapshot_day <" in sql
    assert params == {"snap_project": PROJECT, "snap_today": TODAY}


@pytest.mark.asyncio
async def test_a_project_with_any_snapshot_costs_only_the_probe(monkeypatch):
    computed = AsyncMock()
    staged = AsyncMock()
    monkeypatch.setattr(svc, "snapshot_values", computed)
    monkeypatch.setattr(svc, "stage_snapshot", staged)
    db = _Probe(exists=True)

    assert await svc.stage_first_snapshot(db, PROJECT, today=TODAY) is False

    assert len(db.calls) == 1
    computed.assert_not_awaited()
    staged.assert_not_awaited()


class _Session:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1


def _first_snapshot_step(tree: ast.AST) -> ast.Call:
    steps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") in {"_run_isolated", "_run_isolated_step"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "coverage_first_snapshot"
    ]
    assert len(steps) == 1, "finalize_run must take the first snapshot exactly once"
    return steps[0]


def test_finalize_run_takes_the_first_snapshot_as_an_isolated_step_before_its_last_cache_clear():
    source = inspect.getsource(ingestion_pipeline.finalize_run)
    step = _first_snapshot_step(ast.parse(source))
    (factory,) = step.args[1:]
    assert isinstance(factory, ast.Lambda)
    call = factory.body
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "stage_first_snapshot"
    assert [a.id for a in call.args] == [factory.args.args[0].arg, "pid"]
    # Before the final cache clear, so the History read refills with the new point.
    assert source.index('"coverage_first_snapshot"') < source.rindex("invalidate_analytics_cache(str(pid))")


@pytest.mark.asyncio
async def test_the_step_commits_the_first_snapshot_and_a_failure_is_counted_not_raised(monkeypatch):
    from app.core.metrics import finalize_step_failures_total

    monkeypatch.setattr("app.services.test_management_metrics_service.emit_staged_test_management_metrics", AsyncMock())
    session = _Session()
    monkeypatch.setattr(ingestion_pipeline, "AsyncSessionLocal", lambda: session)
    stage = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "stage_first_snapshot", stage)

    ok = await ingestion_pipeline._run_isolated_step(
        "coverage_first_snapshot", lambda d: svc.stage_first_snapshot(d, PROJECT),
    )
    assert ok is True and session.committed == 1
    stage.assert_awaited_once_with(session, PROJECT)

    before = finalize_step_failures_total.labels(step="coverage_first_snapshot")._value.get()
    monkeypatch.setattr(svc, "stage_first_snapshot", AsyncMock(side_effect=RuntimeError("db down")))
    failed = _Session()
    monkeypatch.setattr(ingestion_pipeline, "AsyncSessionLocal", lambda: failed)
    ok = await ingestion_pipeline._run_isolated_step(
        "coverage_first_snapshot", lambda d: svc.stage_first_snapshot(d, PROJECT),
    )
    assert ok is False and (failed.committed, failed.rolled_back) == (0, 1)
    assert finalize_step_failures_total.labels(step="coverage_first_snapshot")._value.get() == before + 1


def test_the_empty_state_says_when_the_first_point_appears():
    assert svc.NO_SNAPSHOT_REASON == (
        "No coverage snapshot yet: the first is taken when a run of this project finishes, or at 00:05 UTC."
    )
