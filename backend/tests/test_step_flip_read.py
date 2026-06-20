"""FLK-P6 slice 3 — DB read that assembles the per-run step window.

``runs_service.step_flip_report_by_fingerprint`` is the read FLK-P6 slice 2
(the pure ``compute_step_flips``) deferred: it pulls the retained per-run step
outcomes from ``test_step_runs``, groups them into the oldest->newest per-run
window ``compute_step_flips`` expects, and returns the report per fingerprint.

These tests stub the DB with a fake that dispatches on the rendered SQL (the
same shape as ``test_granular_reports``) so the read is exercised without live
services: the anchor resolve (canonical_test_cases) and the per-run step query
(test_step_runs JOIN test_runs) are honoured, project-scoped and chronologically
ordered, and the assembled window is fed through the real computation.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.runs_service import step_flip_report_by_fingerprint

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


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

    ``canon`` maps ``(project_id_str, fingerprint) -> canonical_id`` so the
    anchor resolve is honoured project-scoped. ``step_runs`` are the retained
    per-run rows; the fake sorts them exactly as the helper's ``order_by`` does
    (canonical, run created_at, run id, ordinal) and applies the optional
    ``created_at >= since`` filter, so the helper receives a faithful window.
    """

    def __init__(self, *, canon=None, step_runs=None):
        self.canon = canon or {}
        self.step_runs = step_runs or []
        self.execute_calls: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        self.execute_calls.append(sql)
        params_map = stmt.compile().params

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
            since = None
            for k, v in params_map.items():
                if k.startswith("created_at"):
                    since = v
            rows = [
                s for s in self.step_runs
                if s.canonical_test_case_id in wanted
                and (since is None or s.created_at >= since)
            ]
            rows.sort(key=lambda s: (
                str(s.canonical_test_case_id),
                s.created_at,
                str(s.source_test_run_id),
                s.ordinal,
            ))
            # Real DB Rows expose columns as attributes (the helper reads
            # ``r.canonical_test_case_id`` etc.), so yield attribute-accessible rows.
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


async def test_empty_fingerprints_short_circuits():
    db = _FakeDB()
    out = await step_flip_report_by_fingerprint(db, "proj", [])
    assert out == {}
    assert db.execute_calls == []  # no DB round-trip


async def test_no_anchor_returns_empty_after_one_query():
    db = _FakeDB(canon={})  # fingerprint resolves to no canonical
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-x"])
    assert out == {}
    assert len(db.execute_calls) == 1  # anchor query only, no step query


async def test_oscillating_step_flagged_as_flip():
    cid = "c1"
    db = _FakeDB(
        canon={("proj", "fp-1"): cid},
        step_runs=[
            # ordinal 0 oscillates PASSED -> FAILED -> PASSED across 3 runs.
            _sr(cid, "r1", _T0, 0, "PASSED", "login"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "FAILED", "login"),
            _sr(cid, "r3", _T0 + timedelta(hours=2), 0, "PASSED", "login"),
        ],
    )
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-1"])
    report = out["fp-1"]
    assert report.has_step_flip is True
    assert report.runs_analyzed == 3
    assert report.total_flips == 2
    assert report.flipping_steps[0].step_name == "login"
    # Batched: anchor + step-runs == exactly two queries (never N+1).
    assert len(db.execute_calls) == 2


async def test_stable_step_reports_no_flip():
    cid = "c2"
    db = _FakeDB(
        canon={("proj", "fp-2"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "PASSED"),
        ],
    )
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-2"])
    report = out["fp-2"]
    assert report.has_step_flip is False
    assert report.runs_analyzed == 2
    assert report.total_flips == 0


async def test_single_run_reports_insufficient_history():
    cid = "c3"
    db = _FakeDB(
        canon={("proj", "fp-3"): cid},
        step_runs=[_sr(cid, "r1", _T0, 0, "FAILED")],
    )
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-3"])
    report = out["fp-3"]
    assert report.has_step_flip is False
    assert report.runs_analyzed == 1
    assert "Not enough run history" in report.summary


async def test_resolved_canonical_without_history_reports_empty_window():
    cid = "c4"
    db = _FakeDB(canon={("proj", "fp-4"): cid}, step_runs=[])
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-4"])
    assert "fp-4" in out
    assert out["fp-4"].runs_analyzed == 0
    assert out["fp-4"].has_step_flip is False


async def test_project_scoping_excludes_other_project_canonical():
    db = _FakeDB(
        canon={("other", "fp-5"): "c5"},  # only exists under a different project
        step_runs=[_sr("c5", "r1", _T0, 0, "FAILED")],
    )
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-5"])
    assert out == {}  # anchor resolve is project-scoped


async def test_max_runs_caps_the_window_to_most_recent_runs():
    cid = "c6"
    # 4 runs: PASSED, FAILED, PASSED, PASSED. With max_runs=2 only the last two
    # (PASSED, PASSED) are compared -> no flip; the early oscillation is dropped.
    db = _FakeDB(
        canon={("proj", "fp-6"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "FAILED"),
            _sr(cid, "r3", _T0 + timedelta(hours=2), 0, "PASSED"),
            _sr(cid, "r4", _T0 + timedelta(hours=3), 0, "PASSED"),
        ],
    )
    capped = await step_flip_report_by_fingerprint(db, "proj", ["fp-6"], max_runs=2)
    assert capped["fp-6"].runs_analyzed == 2
    assert capped["fp-6"].has_step_flip is False

    full = await step_flip_report_by_fingerprint(db, "proj", ["fp-6"], max_runs=25)
    assert full["fp-6"].runs_analyzed == 4
    assert full["fp-6"].has_step_flip is True


async def test_since_filters_out_older_runs():
    cid = "c7"
    db = _FakeDB(
        canon={("proj", "fp-7"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "FAILED"),
            _sr(cid, "r3", _T0 + timedelta(hours=2), 0, "PASSED"),
        ],
    )
    # since drops r1; only r2(FAILED) + r3(PASSED) remain -> a single flip.
    out = await step_flip_report_by_fingerprint(
        db, "proj", ["fp-7"], since=_T0 + timedelta(minutes=30)
    )
    report = out["fp-7"]
    assert report.runs_analyzed == 2
    assert report.total_flips == 1


async def test_two_runs_per_run_window_groups_steps_by_ordinal():
    cid = "c8"
    # Two steps per run; ordinal 1 flips, ordinal 0 is stable.
    db = _FakeDB(
        canon={("proj", "fp-8"): cid},
        step_runs=[
            _sr(cid, "r1", _T0, 0, "PASSED", "setup"),
            _sr(cid, "r1", _T0, 1, "PASSED", "assert"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 0, "PASSED", "setup"),
            _sr(cid, "r2", _T0 + timedelta(hours=1), 1, "FAILED", "assert"),
        ],
    )
    out = await step_flip_report_by_fingerprint(db, "proj", ["fp-8"])
    report = out["fp-8"]
    assert report.has_step_flip is True
    assert report.total_flips == 1
    assert len(report.flipping_steps) == 1
    assert report.flipping_steps[0].ordinal == 1
    assert report.flipping_steps[0].step_name == "assert"
