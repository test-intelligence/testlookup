"""Tests for ``eval_gate_service``.

Pins the P1-2 commit-responsibility contract: services receiving an
injected ``db: AsyncSession`` must NOT commit; they flush so the caller
can read server-generated values (PK, defaults) immediately, but the
caller (request handler via ``get_db``, or background task) owns the
transaction lifecycle.

See backend/CLAUDE.md "Commit responsibility (single-owner rule)".
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows
    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteResult:
    def __init__(self, *, scalars=None):
        self._scalars = scalars or []
    def scalars(self):
        return _FakeScalarResult(self._scalars)


class _FakeAsyncDB:
    """Mimics enough of AsyncSession for unit tests.

    Tracks add/flush/commit/rollback calls so tests can assert the
    commit contract directly.
    """
    def __init__(self, execute_results=None):
        self._execute_results = list(execute_results or [])
        self.added: list = []
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()

    async def execute(self, _stmt):
        if self._execute_results:
            return self._execute_results.pop(0)
        return _FakeExecuteResult()

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_persist_agent_stack_gate_run_flushes_but_does_not_commit():
    """Pins P1-2: the service flushes so the caller can read row.id, but
    does not commit. The caller (get_db dependency or task) owns the
    transaction."""
    from app.services.eval_gate_service import persist_agent_stack_gate_run

    db = _FakeAsyncDB()
    gate_result = {
        "manifest": {
            "change_id": "change-42",
            "manifest_checksum_sha256": "abc123",
        },
        "status": "PASS",
        "gate_results": [{"gate": "accuracy", "status": "PASS"}],
        "blocking_gates": [],
        "version_changes": [],
    }
    evaluated_by = uuid.uuid4()

    row = await persist_agent_stack_gate_run(
        db,
        gate_result=gate_result,
        evaluated_by=evaluated_by,
    )

    # The service added the row + flushed (so callers can read id) but
    # did NOT commit the injected session.
    assert len(db.added) == 1
    assert db.added[0] is row
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()
    assert row.change_id == "change-42"
    assert row.status == "PASS"


@pytest.mark.asyncio
async def test_set_baseline_from_eval_flushes_but_does_not_commit():
    """Same contract as persist_agent_stack_gate_run — set_baseline_from_eval
    is router-callable (ai_evaluation.py uses Depends(get_db)) and the
    service must respect the single-owner commit rule."""
    from app.services import eval_gate_service

    # Stub out _load_dataset_items and compute_metrics_for_task_type so
    # the test doesn't need a real eval dataset.
    items = [{"prediction": "A", "expected": "A"}]
    metrics = {"accuracy": 0.95, "precision": 0.9, "recall": 0.95, "f1_score": 0.92}

    db = _FakeAsyncDB(execute_results=[_FakeExecuteResult(scalars=[])])

    async def fake_load(_db, _task_type, _dataset_id):
        return items

    def fake_metrics(_task_type, _items):
        return metrics

    # Patch the two helpers without touching the rest of the module so
    # the commit contract is the only thing under test.
    original_load = eval_gate_service._load_dataset_items
    original_metrics = eval_gate_service.compute_metrics_for_task_type
    eval_gate_service._load_dataset_items = fake_load
    eval_gate_service.compute_metrics_for_task_type = fake_metrics

    try:
        result = await eval_gate_service.set_baseline_from_eval(
            db,
            task_type="classification",
            agent_name="triage",
            min_accuracy=0.8,
            min_f1=0.75,
            max_regression_pct=5.0,
            created_by=uuid.uuid4(),
        )
    finally:
        eval_gate_service._load_dataset_items = original_load
        eval_gate_service.compute_metrics_for_task_type = original_metrics

    # New baseline row was added + flushed but NOT committed.
    assert len(db.added) == 1
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()
    # Response carries the baseline_id (UUID set client-side via default=uuid.uuid4).
    assert "baseline_id" in result
    assert result["metrics"] == metrics


@pytest.mark.asyncio
async def test_persist_agent_stack_gate_run_records_gate_metadata():
    """Sanity check: even after the commit change, the row carries the
    fields the caller relies on for audit/reporting."""
    from app.services.eval_gate_service import persist_agent_stack_gate_run

    db = _FakeAsyncDB()
    gate_result = {
        "manifest": {"change_id": "release-42", "manifest_checksum_sha256": "deadbeef"},
        "status": "FAIL",
        "gate_results": [{"gate": "regression", "status": "FAIL"}],
        "blocking_gates": ["regression"],
        "version_changes": [{"old": "v1", "new": "v2"}],
    }

    row = await persist_agent_stack_gate_run(
        db, gate_result=gate_result, evaluated_by=None,
    )

    assert row.change_id == "release-42"
    assert row.manifest_checksum_sha256 == "deadbeef"
    assert row.status == "FAIL"
    assert row.blocking_gates == ["regression"]
    assert row.version_changes == [{"old": "v1", "new": "v2"}]


# ── Regression: re-baseline must not violate uq_aeb_task_agent_prompt ─────────
#
# Bug pinned (review/eval-gate-service, 2026-06-02):
#   set_baseline_from_eval deactivated existing active baselines then INSERTED a
#   new row with the requested prompt_version. AIEvalBaseline has
#   UniqueConstraint(task_type, agent_name, prompt_version) (applies regardless
#   of is_active), and prompt_version defaults to "v1", so re-baselining the
#   same agent at the same prompt_version raised IntegrityError. The router only
#   catches ValueError → HTTP 500; the baseline could never be refreshed.
#   Fix: upsert on the unique key (refresh the existing row in place; deactivate
#   only OTHER active prompt_versions).


@pytest.mark.asyncio
async def test_re_baseline_same_prompt_version_updates_in_place(monkeypatch):
    from app.services import eval_gate_service as svc

    monkeypatch.setattr(svc, "_load_dataset_items", AsyncMock(return_value=[{"x": 1}]))
    monkeypatch.setattr(
        svc, "compute_metrics_for_task_type",
        lambda _t, _i: {"accuracy": 0.95, "precision": 0.9, "recall": 0.95, "f1_score": 0.92},
    )

    # First call — no existing rows → insert path.
    db1 = _FakeAsyncDB(execute_results=[_FakeExecuteResult(scalars=[])])
    await svc.set_baseline_from_eval(db1, task_type="classification", agent_name="AnalysisAgent")
    assert len(db1.added) == 1
    first_row = db1.added[0]
    assert first_row.prompt_version == "v1"

    # Second call — the same (task_type, agent_name, prompt_version="v1") row
    # already exists. Must REFRESH it in place, not add a duplicate (which
    # would hit the unique constraint).
    monkeypatch.setattr(
        svc, "compute_metrics_for_task_type",
        lambda _t, _i: {"accuracy": 0.80, "precision": 0.8, "recall": 0.8, "f1_score": 0.8},
    )
    db2 = _FakeAsyncDB(execute_results=[_FakeExecuteResult(scalars=[first_row])])
    await svc.set_baseline_from_eval(db2, task_type="classification", agent_name="AnalysisAgent")

    assert db2.added == []                       # no NEW row inserted
    assert first_row.baseline_accuracy == 0.80   # metrics refreshed in place
    assert first_row.is_active is True
    db2.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_re_baseline_deactivates_other_prompt_versions(monkeypatch):
    from app.models.postgres import AIEvalBaseline
    from app.services import eval_gate_service as svc

    monkeypatch.setattr(svc, "_load_dataset_items", AsyncMock(return_value=[{"x": 1}]))
    monkeypatch.setattr(
        svc, "compute_metrics_for_task_type",
        lambda _t, _i: {"accuracy": 0.9, "precision": 0.9, "recall": 0.9, "f1_score": 0.9},
    )

    old = AIEvalBaseline(
        task_type="classification", agent_name="AnalysisAgent",
        prompt_version="v1", is_active=True,
    )
    db = _FakeAsyncDB(execute_results=[_FakeExecuteResult(scalars=[old])])

    # New baseline at a DIFFERENT prompt_version → the v1 row is deactivated and
    # a fresh v2 row is inserted (distinct unique key, no conflict).
    await svc.set_baseline_from_eval(
        db, task_type="classification", agent_name="AnalysisAgent", prompt_version="v2",
    )
    assert old.is_active is False
    new_rows = [r for r in db.added if getattr(r, "prompt_version", None) == "v2"]
    assert len(new_rows) == 1
    assert new_rows[0].is_active is True


def test_coerce_dataset_uuid_handles_malformed():
    """A malformed dataset_id must not raise — it coerces to None so the gate
    falls back to the golden dataset instead of 500ing."""
    import uuid as _uuid

    from app.services.eval_gate_service import _coerce_dataset_uuid

    assert _coerce_dataset_uuid(None) is None
    assert _coerce_dataset_uuid("") is None
    assert _coerce_dataset_uuid("not-a-uuid") is None
    good = str(_uuid.uuid4())
    assert str(_coerce_dataset_uuid(good)) == good


@pytest.mark.asyncio
async def test_load_dataset_items_malformed_id_does_not_500():
    from app.services import eval_gate_service as svc

    db = _FakeAsyncDB()
    # Previously raised ValueError("badly formed hexadecimal UUID string") deep
    # in the gate; now falls through to the golden dataset.
    items = await svc._load_dataset_items(db, "classification", "garbage-not-uuid")
    assert isinstance(items, list)
