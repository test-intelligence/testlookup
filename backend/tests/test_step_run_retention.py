"""Per-run step-outcome retention (``test_step_runs``) — foundation slice for
cross-run step-flip analysis.

``test_steps`` is a LATEST-RUN-ONLY snapshot (delete+reinsert per canonical on
every ingest), so step outcomes from prior runs are lost and a step-flip
("PASSED in run N-1, FAILED in run N") cannot be computed. Migration 0097 adds
``test_step_runs``, which RETAINS one compact row per
``(canonical_test_case_id, source_test_run_id, ordinal)``. These tests prove
ingestion:

  * writes a per-run history row for every step alongside the latest snapshot;
  * RETAINS prior runs' rows across ingests (so a flip is observable);
  * is idempotent per ``(canonical, run)`` — re-ingesting the same run
    overwrites only that run's rows, never the cross-run history;
  * leaves the latest-run ``test_steps`` snapshot unchanged (no regression);

plus the model/migration contract (compact columns, CASCADE on the run, real
downgrade).

Reuses the in-memory fake-DB harness from ``test_granular_steps_ingestion`` (the
ORM is Postgres-only — JSONB/UUID), extended to track + run-scope the new table.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import TestCase, TestStepRun  # noqa: E402
from app.services import ingestion as svc  # noqa: E402
from tests.test_granular_steps_ingestion import _FakeDB  # noqa: E402


def _case_with_child_status(child_status: str) -> dict:
    """An Allure-shaped common-step dict: one parent step (PASSED) with one
    nested child whose status we vary to simulate a step-flip across runs."""
    return {
        "test_name": "test_checkout",
        "class_name": "Checkout",
        "suite_name": "Checkout",
        "status": "failed" if child_status in ("FAILED", "BROKEN") else "passed",
        "duration_ms": 500,
        "attachments": [],
        "steps": [
            {
                "name": "open cart", "keyword": None, "status": "PASSED",
                "start_ms": 1000, "duration_ms": 100,
                "assertion_message": None, "assertion_trace": None,
                "expected": None, "actual": None, "parameters": [],
                "attachments": [],
                "steps": [
                    {
                        "name": "click", "keyword": None, "status": child_status,
                        "start_ms": 1100, "duration_ms": 50,
                        "assertion_message": None, "assertion_trace": None,
                        "expected": None, "actual": None, "parameters": [],
                        "attachments": [], "steps": [],
                    },
                ],
            },
        ],
    }


class _RetentionDB(_FakeDB):
    """``_FakeDB`` extended to track ``test_step_runs`` rows and honour the
    run-scoped delete (so cross-run retention vs per-run idempotency is
    observable). The base fake routes unknown adds to ``self.history`` and does
    not understand the new table's delete."""

    def __init__(self, project_id, **kw):
        super().__init__(project_id, **kw)
        self.step_runs: list[TestStepRun] = []

    def add(self, obj):
        if isinstance(obj, TestStepRun):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            self.step_runs.append(obj)
            return
        super().add(obj)

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if sql.startswith("delete") and "test_step_runs" in sql:
            # Scope to the run in the WHERE clause — retention means we must NOT
            # drop other runs' history.
            params = stmt.compile().params
            run_val = next(
                (v for k, v in params.items() if "source_test_run_id" in k), None
            )
            self.step_runs = [
                r for r in self.step_runs if r.source_test_run_id != run_val
            ]
            return MagicMock()
        return await super().execute(stmt)


async def _ingest(db, case, run):
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="test_checkout")
    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        await svc._upsert_test_case(db, case, run, existing=tc, fingerprint="fp1")
    return tc


@pytest.mark.asyncio
async def test_step_runs_retained_across_runs_enables_step_flip():
    """Two runs of the SAME logical test accumulate per-run step history, so the
    child step's PASSED→FAILED flip is observable across runs — which the
    latest-run ``test_steps`` snapshot alone could never show."""
    project_id = uuid.uuid4()
    db = _RetentionDB(project_id)

    run1 = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    run2 = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)

    await _ingest(db, _case_with_child_status("PASSED"), run1)
    await _ingest(db, _case_with_child_status("FAILED"), run2)

    # Both runs retained: 2 steps × 2 runs.
    assert len(db.step_runs) == 4
    assert {r.source_test_run_id for r in db.step_runs} == {run1.id, run2.id}

    # All rows share the one canonical anchor (cross-run identity).
    assert len({r.canonical_test_case_id for r in db.step_runs}) == 1

    # The "click" step (ordinal 1) flipped PASSED → FAILED between the runs.
    click_by_run = {
        r.source_test_run_id: r.status
        for r in db.step_runs
        if r.name == "click"
    }
    assert click_by_run == {run1.id: "PASSED", run2.id: "FAILED"}

    # The latest-run snapshot stays latest-run-only (no regression): 2 rows.
    assert len(db.steps) == 2

    # Compact retention — no heavy/PII columns leaked onto the history rows.
    for r in db.step_runs:
        assert not hasattr(r, "assertion_message") or r.assertion_message is None
        assert r.duration_ms is not None  # the per-run signal is carried


@pytest.mark.asyncio
async def test_step_runs_idempotent_per_run_on_reingest():
    """Re-ingesting the SAME run overwrites only that run's history rows — no
    accumulation — while a second run's rows remain untouched."""
    project_id = uuid.uuid4()
    db = _RetentionDB(project_id)

    run1 = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    run2 = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)

    await _ingest(db, _case_with_child_status("PASSED"), run1)
    await _ingest(db, _case_with_child_status("FAILED"), run2)
    assert len(db.step_runs) == 4

    # Re-ingest run1 (same id) — its 2 rows are replaced, not appended; run2's
    # 2 rows are left intact.
    await _ingest(db, _case_with_child_status("PASSED"), run1)
    assert len(db.step_runs) == 4
    assert sum(1 for r in db.step_runs if r.source_test_run_id == run1.id) == 2
    assert sum(1 for r in db.step_runs if r.source_test_run_id == run2.id) == 2


def test_test_step_runs_model_contract():
    """Compact, per-run-keyed, run-CASCADE schema — distinct from the SET NULL
    provenance snapshot."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(TestStepRun)
    cols = {c.name for c in insp.columns}
    # Identity + per-run signal only.
    assert {
        "canonical_test_case_id", "source_test_run_id", "ordinal", "depth",
        "name", "keyword", "status", "duration_ms",
    } <= cols
    # Heavy / PII columns from the snapshot must NOT be duplicated per run.
    assert not ({"assertion_message", "assertion_trace", "expected_value",
                 "actual_value", "parameters"} & cols)

    # source_test_run_id is NOT NULL (it is per-run history) and CASCADEs on run
    # deletion (the row is about that run).
    run_fk = next(
        fk for c in insp.columns if c.name == "source_test_run_id"
        for fk in c.foreign_keys
    )
    assert run_fk.ondelete == "CASCADE"
    assert insp.columns["source_test_run_id"].nullable is False


def test_migration_0097_present_and_reversible():
    import importlib
    import inspect

    mod = importlib.import_module("migrations.versions.0097_test_step_runs_retention")
    assert mod.revision == "0097"
    assert mod.down_revision == "0096"
    assert callable(mod.upgrade) and callable(mod.downgrade)

    up_src = inspect.getsource(mod.upgrade)
    down_src = inspect.getsource(mod.downgrade)
    assert "test_step_runs" in up_src
    assert 'ondelete="CASCADE"' in up_src
    # Real downgrade drops the table + its unique constraint + index.
    assert "drop_table" in down_src
    assert "test_step_runs" in down_src
    assert "uq_test_step_runs_canonical_run_ordinal" in down_src
