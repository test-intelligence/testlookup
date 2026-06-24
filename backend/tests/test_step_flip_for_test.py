"""FLK-P6 slice 4 — per-test step-flip surface read.

``runs_service.step_flip_report_for_test`` is the read backing
``GET /api/v1/runs/{run_id}/tests/{test_id}/step-flips``. It resolves the test's
``test_fingerprint`` + ``project_id`` from the provided ``(run_id, test_id)`` and
defers to the batched, project-scoped ``step_flip_report_by_fingerprint``.

These tests stub the DB with a fake that dispatches on the rendered SQL (same
shape as ``test_step_flip_read`` / ``test_granular_reports``): the test-case
resolve (test_cases JOIN test_runs), the anchor resolve (canonical_test_cases),
and the per-run step query (test_step_runs JOIN test_runs) are honoured so the
read is exercised end-to-end without live services.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.runs_service import step_flip_report_for_test

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def _flatten_param_values(params_map: dict, prefix: str) -> set:
    out: set = set()
    for k, v in params_map.items():
        if not k.startswith(prefix):
            continue
        if isinstance(v, (list, tuple, set)):
            out.update(v)
        else:
            out.add(v)
    return out


class _FakeDB:
    """Dispatches on the rendered SQL of each ``select`` statement.

    ``test_case`` is ``(test_fingerprint, project_id)`` returned by the
    ``(run_id, test_id)`` resolve, or ``None`` to simulate a mismatch.
    ``canon`` / ``step_runs`` feed the downstream batched read exactly as the
    ``test_step_flip_read`` fake does.
    """

    def __init__(self, *, test_case=None, canon=None, step_runs=None):
        self.test_case = test_case
        self.canon = canon or {}
        self.step_runs = step_runs or []
        self.execute_calls: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        self.execute_calls.append(sql)
        params_map = stmt.compile().params

        # (run_id, test_id) resolve: test_cases JOIN test_runs.
        if "test_cases" in sql and "test_step_runs" not in sql and "canonical_test_cases" not in sql:
            return _Result([self.test_case] if self.test_case is not None else [])

        # Anchor resolve: SELECT from canonical_test_cases (no test_step_runs).
        if "canonical_test_cases" in sql and "test_step_runs" not in sql:
            pid = str(params_map.get("project_id_1") or params_map.get("project_id"))
            fps = _flatten_param_values(params_map, "test_fingerprint")
            return _Result([
                (cid, fp)
                for (cpid, fp), cid in self.canon.items()
                if cpid == pid and fp in fps
            ])

        # Per-run step window: test_step_runs JOIN test_runs.
        if "test_step_runs" in sql:
            wanted = _flatten_param_values(params_map, "canonical_test_case_id")
            rows = [s for s in self.step_runs if s.canonical_test_case_id in wanted]
            rows.sort(key=lambda s: (
                str(s.canonical_test_case_id),
                s.created_at,
                str(s.source_test_run_id),
                s.ordinal,
            ))
            return _Result([
                SimpleNamespace(
                    canonical_test_case_id=s.canonical_test_case_id,
                    source_test_run_id=s.source_test_run_id,
                    ordinal=s.ordinal,
                    status=s.status,
                    name=s.name,
                )
                for s in rows
            ])

        return _Result([])


_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _sr(cid, run_id, when, ordinal, status, name="step"):
    return SimpleNamespace(
        canonical_test_case_id=cid,
        source_test_run_id=run_id,
        ordinal=ordinal,
        status=status,
        name=name,
        created_at=when,
    )


async def test_missing_test_case_returns_none():
    db = _FakeDB(test_case=None)
    out = await step_flip_report_for_test(db, "run-1", "test-1")
    assert out is None
    assert len(db.execute_calls) == 1  # resolve only, no downstream queries


async def test_no_fingerprint_returns_empty_window_report():
    db = _FakeDB(test_case=(None, "proj"))
    out = await step_flip_report_for_test(db, "run-1", "test-1")
    assert out is not None
    assert out["test_fingerprint"] is None
    assert out["report"]["has_step_flip"] is False
    assert out["report"]["runs_analyzed"] == 0
    assert len(db.execute_calls) == 1  # short-circuits before the batched read


async def test_oscillating_step_surfaced_for_test():
    cid = "c1"
    db = _FakeDB(
        test_case=("fp-1", "proj"),
        canon={("proj", "fp-1"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED", "login"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "FAILED", "login"),
            _sr(cid, "r3", _T0 + timedelta(hours=2), 0, "PASSED", "login"),
        ],
    )
    out = await step_flip_report_for_test(db, "run-1", "test-1")
    assert out["run_id"] == "run-1"
    assert out["test_id"] == "test-1"
    assert out["test_fingerprint"] == "fp-1"
    report = out["report"]
    assert report["has_step_flip"] is True
    assert report["runs_analyzed"] == 3
    assert report["total_flips"] == 2
    assert report["flipping_steps"][0]["step_name"] == "login"
    # resolve + anchor + step-runs == exactly three queries (never N+1).
    assert len(db.execute_calls) == 3


async def test_resolved_test_without_history_reports_empty_window():
    cid = "c4"
    db = _FakeDB(test_case=("fp-4", "proj"), canon={("proj", "fp-4"): cid}, step_runs=[])
    out = await step_flip_report_for_test(db, "run-1", "test-1")
    assert out["test_fingerprint"] == "fp-4"
    assert out["report"]["runs_analyzed"] == 0
    assert out["report"]["has_step_flip"] is False


async def test_stable_step_reports_no_flip():
    cid = "c2"
    db = _FakeDB(
        test_case=("fp-2", "proj"),
        canon={("proj", "fp-2"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "PASSED"),
        ],
    )
    out = await step_flip_report_for_test(db, "run-1", "test-1")
    assert out["report"]["has_step_flip"] is False
    assert out["report"]["runs_analyzed"] == 2
    assert out["report"]["total_flips"] == 0
