"""FLK-P6 slice 5 — run-level step-flip roll-up read.

``runs_service.step_flip_report_for_run`` is the read backing
``GET /api/v1/runs/{run_id}/step-flips``. It resolves the run's ``project_id``
from the provided ``run_id``, gathers the run's fingerprint-anchored tests, and
defers to the batched, project-scoped ``step_flip_report_by_fingerprint`` to
report WHICH TESTS in the run have a flickering step.

These tests stub the DB with a fake that dispatches on the rendered SQL (same
shape as ``test_step_flip_for_test``): the run resolve (test_runs), the run's
test-case list (test_cases), the anchor resolve (canonical_test_cases), and the
per-run step query (test_step_runs JOIN test_runs) are honoured so the read is
exercised end-to-end without live services.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.runs_service import step_flip_report_for_run

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

    ``run`` is ``(project_id,)`` returned by the run resolve, or ``None`` to
    simulate a missing run. ``test_cases`` is a list of
    ``(id, test_name, test_fingerprint, status)`` tuples for the run. ``canon`` /
    ``step_runs`` feed the downstream batched read exactly as the
    ``test_step_flip_for_test`` fake does.
    """

    def __init__(self, *, run=None, test_cases=None, canon=None, step_runs=None):
        self.run = run
        self.test_cases = test_cases or []
        self.canon = canon or {}
        self.step_runs = step_runs or []
        self.execute_calls: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        self.execute_calls.append(sql)
        params_map = stmt.compile().params

        # Run resolve: SELECT project_id FROM test_runs (no join).
        if "test_runs" in sql and "test_cases" not in sql and "test_step_runs" not in sql:
            return _Result([self.run] if self.run is not None else [])

        # Run's test-case list: SELECT from test_cases (no joins to the others).
        if "test_cases" in sql and "test_step_runs" not in sql and "canonical_test_cases" not in sql:
            rows = list(self.test_cases)
            # Honour the LIMIT so truncation is exercised against the real cap.
            limit = params_map.get("param_1")
            if isinstance(limit, int):
                rows = rows[:limit]
            return _Result(rows)

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


def _oscillating(cid, name="login"):
    return [
        _sr(cid, "r1", _T0, 0, "PASSED", name),
        _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "FAILED", name),
        _sr(cid, "r3", _T0 + timedelta(hours=2), 0, "PASSED", name),
    ]


async def test_missing_run_returns_none():
    db = _FakeDB(run=None)
    out = await step_flip_report_for_run(db, "run-1")
    assert out is None
    assert len(db.execute_calls) == 1  # resolve only, no downstream queries


async def test_run_with_no_anchored_tests_reports_empty_rollup():
    db = _FakeDB(run=("proj",), test_cases=[])
    out = await step_flip_report_for_run(db, "run-1")
    assert out["tests_analyzed"] == 0
    assert out["tests_with_flips"] == 0
    assert out["total_flips"] == 0
    assert out["truncated"] is False
    assert out["tests"] == []
    # resolve + test-case list, but short-circuits before the batched read.
    assert len(db.execute_calls) == 2


async def test_only_flipping_tests_surfaced_sorted_by_flips():
    db = _FakeDB(
        run=("proj",),
        test_cases=[
            ("t-flaky", "test_login", "fp-flaky", "FAILED"),
            ("t-stable", "test_checkout", "fp-stable", "PASSED"),
        ],
        canon={("proj", "fp-flaky"): "c-flaky", ("proj", "fp-stable"): "c-stable"},
        step_runs=[
            *_oscillating("c-flaky"),
            _sr("c-stable", "r1", _T0, 0, "PASSED"),
            _sr("c-stable", "r2", _T0 + timedelta(hours=1), 0, "PASSED"),
        ],
    )
    out = await step_flip_report_for_run(db, "run-1")
    assert out["run_id"] == "run-1"
    assert out["project_id"] == "proj"
    assert out["tests_analyzed"] == 2  # both tests considered
    assert out["tests_with_flips"] == 1  # only the flaky one surfaced
    assert out["total_flips"] == 2
    assert [t["test_fingerprint"] for t in out["tests"]] == ["fp-flaky"]
    surfaced = out["tests"][0]
    assert surfaced["test_id"] == "t-flaky"
    assert surfaced["test_name"] == "test_login"
    assert surfaced["status"] == "FAILED"
    assert surfaced["report"]["has_step_flip"] is True
    assert surfaced["report"]["total_flips"] == 2


async def test_tests_sorted_by_total_flips_desc():
    # One flip vs two flips — the busier test sorts first.
    db = _FakeDB(
        run=("proj",),
        test_cases=[
            ("t-one", "aaa_one_flip", "fp-one", "FAILED"),
            ("t-two", "zzz_two_flips", "fp-two", "FAILED"),
        ],
        canon={("proj", "fp-one"): "c-one", ("proj", "fp-two"): "c-two"},
        step_runs=[
            _sr("c-one", "r1", _T0, 0, "PASSED"),
            _sr("c-one", "r2", _T0 + timedelta(hours=1), 0, "FAILED"),
            *_oscillating("c-two"),
        ],
    )
    out = await step_flip_report_for_run(db, "run-1")
    assert [t["test_fingerprint"] for t in out["tests"]] == ["fp-two", "fp-one"]
    assert out["total_flips"] == 3


async def test_truncation_flagged_when_run_exceeds_cap():
    # max_tests=1 with two anchored tests → only one analysed, truncated=True.
    db = _FakeDB(
        run=("proj",),
        test_cases=[
            ("t-a", "test_a", "fp-a", "FAILED"),
            ("t-b", "test_b", "fp-b", "FAILED"),
        ],
        canon={("proj", "fp-a"): "c-a", ("proj", "fp-b"): "c-b"},
        step_runs=[*_oscillating("c-a"), *_oscillating("c-b")],
    )
    out = await step_flip_report_for_run(db, "run-1", max_tests=1)
    assert out["truncated"] is True
    assert out["tests_analyzed"] == 1  # capped before the batched read
