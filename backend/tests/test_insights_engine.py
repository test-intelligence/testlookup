"""W23a FAIL-11: the insight detectors (judges, statements, read-only) and the store.

Contract §2.1 R-1 … R-5, D-1 … D-4. No database: the detectors run on a
scripted session (each ``execute`` answers the next scripted result and the
statement is kept), and the store's statements are compiled on the
PostgreSQL dialect. Every timestamp is built inside its test.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.services import insight_detectors as det
from app.services import insights_service as store
from app.services.insight_detectors import DetectorResult, Finding, SnapshotRow
from app.services.insight_vocab import INSIGHT_KINDS, INSIGHT_LINKS, MAX_SUBJECTS_PER_DETECTOR, NOT_MEASURED


class _Result:
    def __init__(self, rows: Any = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def all(self) -> list:
        return list(self._rows)

    def one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> "_Result":
        return self

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _Session:
    """Answers each ``execute`` with the next scripted result; keeps the statements."""

    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.statements: list[Any] = []
        self.params: list[Any] = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(statement)
        self.params.append(params)
        return self.results.pop(0) if self.results else _Result()

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:  # pragma: no cover - a store must never call it
        self.commits += 1


def _sql(statement: Any) -> str:
    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


def _params(statement: Any) -> dict:
    return dict(statement.compile(dialect=postgresql.dialect()).params)


def _now() -> datetime:
    return datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


# ── judge_flake ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prev, cur, expected",
    [
        (2, 3, (True, False, None)),
        (1, 3, (True, True, "medium")),
        (0, 3, (True, True, "medium")),
        (3, 5, (True, False, None)),
        (3, 6, (True, True, "medium")),
        (2, 6, (True, True, "high")),
        (3, 9, (True, True, "high")),
    ],
)
def test_judge_flake_floors(prev: int, cur: int, expected: tuple) -> None:
    assert det.judge_flake(prev, cur, 5, 5) == expected


@pytest.mark.parametrize("runs_prev, runs_cur", [(4, 5), (5, 4), (4, 4)])
def test_judge_flake_needs_five_runs_in_both_weeks(runs_prev: int, runs_cur: int) -> None:
    assert det.judge_flake(0, 9, runs_prev, runs_cur) == (False, False, None)


# ── judge_coverage ────────────────────────────────────────────────────────────


def _snap(day: date, exercised: int, catalogue: int = 200, suites: int = 4) -> SnapshotRow:
    return SnapshotRow(day, catalogue, exercised, suites)


@pytest.mark.parametrize(
    "previous, current, expected",
    [
        (100, 90, (True, None, True, "medium")),     # −10 %
        (50, 45, (True, None, True, "medium")),      # −5 tests, −10 %
        (1000, 901, (True, None, False, None)),      # −9.9 %
        (40, 36, (True, None, False, None)),         # −10 % but −4
        (100, 75, (True, None, True, "high")),       # −25 %
        (100, 76, (True, None, True, "medium")),     # −24 %
        (100, 100, (True, None, False, None)),
        (100, 120, (True, None, False, None)),
    ],
)
def test_judge_coverage_floors(previous: int, current: int, expected: tuple) -> None:
    today = date(2026, 10, 28)
    new = _snap(today - timedelta(days=1), current)
    prev = _snap(today - timedelta(days=8), previous)
    assert det.judge_coverage(new, prev, today=today) == expected


def test_judge_coverage_not_measured_states() -> None:
    today = date(2026, 10, 28)
    fresh = _snap(today - timedelta(days=1), 80)
    assert det.judge_coverage(None, None, today=today)[:2] == (False, "coverage_no_snapshot")
    stale = _snap(today - timedelta(days=3), 80)
    assert det.judge_coverage(stale, _snap(today - timedelta(days=10), 100), today=today)[:2] == (
        False, "coverage_stale_snapshot",
    )
    assert det.judge_coverage(fresh, None, today=today)[:2] == (False, "coverage_no_previous")
    small = _snap(today - timedelta(days=8), 19)
    assert det.judge_coverage(_snap(today - timedelta(days=1), 2), small, today=today)[:2] == (
        False, "coverage_too_small",
    )
    # Two days old is still fresh.
    edge = _snap(today - timedelta(days=2), 80)
    assert det.judge_coverage(edge, _snap(today - timedelta(days=9), 100), today=today)[0] is True


def test_pick_snapshots_takes_the_newest_row_seven_to_nine_days_before() -> None:
    newest = date(2026, 10, 27)
    rows = [_snap(newest - timedelta(days=d), 100 - d) for d in (0, 3, 7, 8, 9)]
    assert det.pick_snapshots(rows) == (rows[0], rows[2])
    only_ten_back = [_snap(newest, 80), _snap(newest - timedelta(days=10), 100)]
    assert det.pick_snapshots(only_ten_back) == (only_ten_back[0], None)
    only_nine_back = [_snap(newest, 80), _snap(newest - timedelta(days=9), 100)]
    assert det.pick_snapshots(only_nine_back)[1] == only_nine_back[1]
    assert det.pick_snapshots([]) == (None, None)


# ── judge_perf_suite / judge_perf_test / judge_cost ───────────────────────────


def test_judge_perf_suite() -> None:
    assert det.judge_perf_suite(True, True, 1.6) == (True, True, "medium")
    assert det.judge_perf_suite(True, True, 2.0) == (True, True, "high")
    assert det.judge_perf_suite(False, True, None) == (True, False, None)    # measured, silent
    assert det.judge_perf_suite(False, False, None) == (False, False, None)  # not judged


def test_judge_perf_test_window_and_floors() -> None:
    now = _now()
    assert det.judge_perf_test(3, now - timedelta(hours=47), 4.0, now=now) == (True, "medium")
    assert det.judge_perf_test(3, now - timedelta(hours=48), 4.0, now=now) == (True, "medium")
    assert det.judge_perf_test(3, now - timedelta(hours=49), 4.0, now=now) == (False, None)
    assert det.judge_perf_test(2, now - timedelta(hours=1), 9.0, now=now) == (False, None)
    assert det.judge_perf_test(3, now - timedelta(hours=1), 6.0, now=now) == (True, "high")
    assert det.judge_perf_test(5, now - timedelta(hours=1), 3.1, now=now) == (True, "high")


@pytest.mark.parametrize(
    "status, expected",
    [
        ("UNLIMITED", (False, False, None)),
        ("OK", (True, False, None)),
        ("SOFT_WARN", (True, True, "medium")),
        ("CAPPED", (True, True, "high")),
    ],
)
def test_judge_cost(status: str, expected: tuple) -> None:
    assert det.judge_cost(status) == expected


# ── detect_perf_drift ─────────────────────────────────────────────────────────


def _run(created_at: datetime, *, suites: Any = (), spikes: Any = (), suite_measured: bool = True,
         test_measured: bool = True, count: Any = None) -> SimpleNamespace:
    suites = list(suites)
    return SimpleNamespace(
        id=uuid.uuid4(), build_number="b1", branch="main", created_at=created_at,
        duration_findings={
            "suites": suites, "suite_regression_count": len(suites) if count is None else count,
            "spikes": list(spikes),
            "suite_check": {"measured": suite_measured}, "test_check": {"measured": test_measured},
        },
    )


def _suite(key: str, ratio: float = 1.8) -> dict:
    return {"suite_key": key, "suite_name": key.title(), "ratio": ratio, "metric": "wall",
            "recent_median_ms": 1800.0, "baseline_median_ms": 1000.0, "recent_runs": 5, "baseline_runs": 20}


def _spike(fp: str = "fp_alpha", sigma: float = 4.0) -> dict:
    return {"test_fingerprint": fp, "test_name": "checkout_sentinel_test", "suite_name": "Payments sentinel",
            "observed_ms": 1500, "baseline_mean_ms": 1000.0, "sigma": sigma}


def _newest(key: str, run: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(suite_key=key, suite_name=key.title(), test_run_id=run.id, run_created_at=run.created_at)


@pytest.fixture
def perf_spies(monkeypatch: pytest.MonkeyPatch) -> dict:
    """EXEC-05's statistics must never run from the detector; its flag read is scripted."""
    from app.services import perf_regression_service as perf

    calls: list[str] = []
    state = {"flag": True, "calls": calls}

    async def _flag(db: Any = None, **_: Any) -> bool:
        return state["flag"]

    def _spy(name: str):
        async def _called(*_: Any, **__: Any) -> None:
            calls.append(name)
            raise AssertionError(f"{name} must not be called by the detector")
        return _called

    monkeypatch.setattr(perf, "_feature_enabled", _flag)
    for name in ("assess_run_durations", "feed_run_observations", "detect_spikes_for_run"):
        monkeypatch.setattr(perf, name, _spy(name))
    return state


async def test_perf_drift_suite_fires_on_its_newest_run_and_never_runs_statistics(perf_spies: dict) -> None:
    now = _now()
    newest = _run(now - timedelta(hours=2), suites=[_suite("payments", 2.4)])
    older = _run(now - timedelta(days=2), suites=[_suite("payments", 1.6)])
    quiet = _run(now - timedelta(hours=3))
    db = _Session(
        _Result([newest, quiet, older]),
        _Result([_newest("payments", newest), _newest("search", quiet)]),
    )
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    assert perf_spies["calls"] == []
    assert result.measured is True and result.reason_code is None
    assert result.measured_subjects == frozenset({"suite:payments", "suite:search"})
    [finding] = result.findings
    assert finding.subject_key == "suite:payments" and finding.severity == "high"
    assert finding.evidence["flagged_runs"] == 2 and finding.evidence["newest_run_id"] == str(newest.id)
    assert finding.evidence["runs_read"] == 3 and finding.evidence["truncated"] is False
    # Statement 1 reads only stored findings of the window, newest first, bounded.
    first = _sql(db.statements[0])
    assert "duration_findings IS NOT NULL" in first and "ORDER BY test_runs.created_at DESC" in first
    assert "LIMIT" in first
    assert "DISTINCT ON (suite_executions.suite_key)" in _sql(db.statements[1])


async def test_perf_drift_suite_whose_newest_run_has_no_findings_is_not_judged(perf_spies: dict) -> None:
    now = _now()
    older = _run(now - timedelta(days=2), suites=[_suite("payments")])
    unmeasured = _run(now - timedelta(hours=5), suite_measured=False)
    newest_elsewhere = SimpleNamespace(suite_key="payments", suite_name="Payments", test_run_id=uuid.uuid4(),
                                       run_created_at=now - timedelta(hours=1))
    db = _Session(
        _Result([unmeasured, older]),
        _Result([newest_elsewhere, _newest("search", unmeasured)]),
    )
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    # Neither suite was judged; tests were measured (test_check) -> measured, no subject to clear.
    assert result.findings == () and result.measured_subjects == frozenset()
    assert result.measured is True


async def test_perf_drift_a_truncated_suites_list_cannot_call_a_suite_quiet(perf_spies: dict) -> None:
    now = _now()
    run = _run(now - timedelta(hours=1), suites=[_suite("alpha")], count=60, test_measured=False)
    db = _Session(_Result([run]), _Result([_newest("omega", run)]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    assert result.measured is False and result.reason_code == "perf_no_findings"


async def test_perf_drift_tests_fire_on_three_runs_within_48h(perf_spies: dict) -> None:
    now = _now()
    runs = [_run(now - timedelta(hours=h), spikes=[_spike(sigma=s)]) for h, s in ((47, 4.5), (60, 3.2), (90, 3.4))]
    db = _Session(_Result(runs), _Result([]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    [finding] = result.findings
    assert finding.subject_key == "test:fp_alpha" and finding.severity == "medium"
    assert finding.evidence["spike_runs"] == 3 and finding.evidence["max_sigma"] == 4.5
    assert finding.evidence["pct"] == 50 and finding.evidence["newest_run_id"] == str(runs[0].id)
    # Tests are never judged quiet: a test drift ends only stale.
    assert result.measured_subjects == frozenset()


async def test_perf_drift_tests_quiet_when_the_newest_spike_is_49h_old_or_two_runs(perf_spies: dict) -> None:
    now = _now()
    old = [_run(now - timedelta(hours=h), spikes=[_spike()]) for h in (49, 60, 90)]
    result = await det.detect_perf_drift(_Session(_Result(old), _Result([])), uuid.uuid4(), now=now)
    assert result.findings == ()
    two = [_run(now - timedelta(hours=h), spikes=[_spike()]) for h in (1, 2)]
    result = await det.detect_perf_drift(_Session(_Result(two), _Result([])), uuid.uuid4(), now=now)
    assert result.findings == ()


async def test_perf_drift_flag_off_skips_tests_and_says_so(perf_spies: dict) -> None:
    perf_spies["flag"] = False
    now = _now()
    runs = [_run(now - timedelta(hours=h), suites=[_suite("payments")], spikes=[_spike()]) for h in (1, 2, 3)]
    db = _Session(_Result(runs), _Result([_newest("payments", runs[0])]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    assert result.measured is True and result.reason_code == "perf_tests_flag_off"
    assert [f.subject_key for f in result.findings] == ["suite:payments"]
    # Nothing judged at all, flag off: not measured, with the flag sentence.
    db = _Session(_Result([_run(now, suite_measured=False)]), _Result([]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    assert (result.measured, result.reason_code) == (False, "perf_tests_flag_off")


async def test_perf_drift_with_no_stored_findings_is_not_measured(perf_spies: dict) -> None:
    db = _Session(_Result([]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=_now())
    assert (result.measured, result.reason_code, result.findings) == (False, "perf_no_findings", ())
    assert len(db.statements) == 1


async def test_perf_drift_caps_subjects_and_never_clears_the_ones_it_cut(perf_spies: dict) -> None:
    now = _now()
    keys = [f"suite_{chr(97 + i)}{chr(97 + j)}" for i in range(5) for j in range(5)]   # 25 suites
    run = _run(now - timedelta(hours=1), suites=[_suite(k, 1.5 + i / 100) for i, k in enumerate(keys)])
    db = _Session(_Result([run]), _Result([_newest(k, run) for k in keys]))
    result = await det.detect_perf_drift(db, uuid.uuid4(), now=now)
    assert len(result.findings) == MAX_SUBJECTS_PER_DETECTOR and result.firing == 25
    kept = {f.subject_key for f in result.findings}
    assert result.measured_subjects == frozenset(kept)


# ── detect_flake_rising ───────────────────────────────────────────────────────


def _flake_row(key: str, w: int, runs: int, flaky: int, examples: Any = ()) -> SimpleNamespace:
    return SimpleNamespace(suite_key=key, w=w, runs=runs, suite_name=key.title(), flaky=flaky,
                           examples=list(examples))


async def test_flake_statement_is_the_flaky_tests_rule_over_two_windows() -> None:
    now = _now()
    db = _Session(_Result([
        _flake_row("payments sentinel", 0, 5, 4, ["checkout_sentinel_test"]),
        _flake_row("payments sentinel", 1, 5, 1),
        _flake_row("search", 0, 5, 1),
        _flake_row("search", 1, 5, 1),
        _flake_row("rare", 0, 2, 3),
        _flake_row("rare", 1, 9, 0),
    ]))
    project_id = uuid.uuid4()
    result = await det.detect_flake_rising(db, project_id, now=now)
    [statement] = db.statements
    sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
    assert "PARTITION BY w, suite_key, fp" in sql
    assert "n >= 3" in sql and "BETWEEN 0.05 AND 0.95" in sql and "flips >= 2" in sql
    for bind in (":start", ":mid", ":now", ":p"):
        assert bind in str(statement)
    params = db.params[0]
    assert params["now"] == now and params["mid"] == now - timedelta(days=7)
    assert params["start"] == now - timedelta(days=14) and params["p"] == project_id
    # Judged: payments + search (5 runs both weeks); rare is not.
    assert result.measured_subjects == frozenset({"suite:payments sentinel", "suite:search"})
    [finding] = result.findings
    assert (finding.subject_key, finding.severity) == ("suite:payments sentinel", "medium")
    assert finding.evidence["previous"] == 1 and finding.evidence["current"] == 4
    assert finding.evidence["examples"] == ["checkout_sentinel_test"]
    assert set(finding.evidence["window"]) == {"start", "mid", "end"}


def test_flake_statement_uses_the_shared_constants_and_effective_suite() -> None:
    from app.services import analytics_scope
    from app.services.flaky_signals import MIN_FLIPS_FOR_INTERMITTENCY

    sql = det.flake_statement()
    assert analytics_scope.effective_suite_sql("tr", "tc") in sql
    assert f"flips >= {MIN_FLIPS_FOR_INTERMITTENCY}" in sql


async def test_flake_rising_without_a_judged_suite_is_not_measured() -> None:
    db = _Session(_Result([_flake_row("rare", 0, 4, 3), _flake_row("rare", 1, 9, 0)]))
    result = await det.detect_flake_rising(db, uuid.uuid4(), now=_now())
    assert (result.measured, result.reason_code) == (False, "flake_few_runs")


# ── detect_coverage_drop ──────────────────────────────────────────────────────


async def test_coverage_drop_reads_one_statement_and_fires() -> None:
    now = _now()
    today = now.date()
    rows = [
        SimpleNamespace(snapshot_day=today - timedelta(days=1), total_tests=100, automated_count=80, total_suites=3),
        SimpleNamespace(snapshot_day=today - timedelta(days=8), total_tests=100, automated_count=100, total_suites=4),
    ]
    db = _Session(_Result(rows))
    result = await det.detect_coverage_drop(db, uuid.uuid4(), now=now)
    assert len(db.statements) == 1 and "coverage_snapshots" in _sql(db.statements[0])
    [finding] = result.findings
    assert (finding.subject_key, finding.severity) == ("project", "medium")
    assert finding.evidence["pct"] == 20 and finding.evidence["previous_pct"] == 100.0
    assert finding.evidence["current_pct"] == 80.0
    assert result.measured_subjects == frozenset({"project"})


async def test_coverage_drop_not_measured_without_snapshots() -> None:
    result = await det.detect_coverage_drop(_Session(_Result([])), uuid.uuid4(), now=_now())
    assert (result.measured, result.reason_code) == (False, "coverage_no_snapshot")


# ── detect_cost_budget ────────────────────────────────────────────────────────


@pytest.fixture
def cost_world(monkeypatch: pytest.MonkeyPatch) -> dict:
    from app.services import feature_flags, llm_cost_budget

    state: dict[str, Any] = {"flag": True, "usage": {}, "capped_calls": 0}

    async def _flag(key: str, **_: Any) -> bool:
        assert key == "llm_cost_budget"
        return state["flag"]

    async def _usage(db: Any, project_id: Any, *, now: Any = None) -> dict:
        return dict(state["usage"])

    async def _cap(*_: Any, **__: Any) -> None:
        state["capped_calls"] += 1

    monkeypatch.setattr(feature_flags, "is_enabled", _flag)
    monkeypatch.setattr(llm_cost_budget, "get_current_usage", _usage)
    monkeypatch.setattr(llm_cost_budget, "check_and_apply_cap", _cap)
    return state


@pytest.mark.parametrize(
    "status, cap, measured, severity",
    [
        ("UNLIMITED", None, False, None),
        ("OK", 10.0, True, None),
        ("OK", 0.0, False, None),          # a quota without a cap: nothing to judge against
        ("SOFT_WARN", 10.0, True, "medium"),
        ("CAPPED", 10.0, True, "high"),
    ],
)
async def test_cost_budget_reuses_the_budget_state(
    cost_world: dict, status: str, cap: Any, measured: bool, severity: Any,
) -> None:
    now = _now()
    cost_world["usage"] = {"status": status, "hard_cap_usd": cap, "total_cost_usd": 8.5, "utilization_pct": 85.0,
                           "period_start": datetime(2026, 10, 1, tzinfo=timezone.utc),
                           "period_end": datetime(2026, 11, 1, tzinfo=timezone.utc)}
    result = await det.detect_cost_budget(_Session(), uuid.uuid4(), now=now)
    assert cost_world["capped_calls"] == 0
    assert result.window_key == "month:2026-10" and result.measured is measured
    if not measured:
        assert result.reason_code == "cost_no_budget"
        return
    assert result.clear_earlier_windows is True and result.measured_subjects == frozenset({"budget"})
    assert [f.severity for f in result.findings] == ([severity] if severity else [])


async def test_cost_budget_flag_off(cost_world: dict) -> None:
    cost_world["flag"] = False
    result = await det.detect_cost_budget(_Session(), uuid.uuid4(), now=_now())
    assert (result.measured, result.reason_code) == (False, "cost_flag_off")


def test_detectors_are_the_four_kinds_in_order() -> None:
    assert tuple(kind for kind, _ in det.DETECTORS) == INSIGHT_KINDS


# ── The store: apply_result ───────────────────────────────────────────────────


def _result(kind: str = "flake_rising", *, findings: Any = (), measured_subjects: Any = (), measured: bool = True,
            reason: Any = None, window_key: str = "7d", clear_earlier: bool = False) -> DetectorResult:
    findings = tuple(findings)
    return DetectorResult(
        kind=kind, window_key=window_key, measured=measured, reason_code=reason, findings=findings,
        measured_subjects=frozenset(measured_subjects), firing=len(findings), clear_earlier_windows=clear_earlier,
    )


def _finding(key: str = "suite:payments sentinel", severity: str = "medium") -> Finding:
    return Finding(key, "Payments sentinel", severity, {"suite_name": "Payments sentinel", "previous": 1, "current": 4})


def test_upsert_is_the_open_row_on_conflict_and_never_sets_dismissal() -> None:
    now = _now()
    sql = _sql(store._upsert_statement(uuid.uuid4(), _result(), _finding(), now))
    assert (
        "ON CONFLICT (project_id, kind, subject_key, window_key) WHERE resolved_at IS NULL DO UPDATE SET" in sql
    )
    set_clause = sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "dismissed" not in set_clause
    for column in ("last_seen_at", "severity", "evidence", "subject_label", "detection_count"):
        assert column in set_clause
    assert "detection_count = (project_insights.detection_count +" in set_clause
    assert "RETURNING project_insights.id, (xmax = 0) AS inserted" in sql


async def test_a_not_measured_result_writes_only_the_state_row() -> None:
    db = _Session()
    counts = await store.apply_result(
        db, uuid.uuid4(), _result(measured=False, reason="flake_few_runs"), now=_now(),
    )
    assert counts == {"inserted": 0, "confirmed": 0, "cleared": 0}
    [statement] = db.statements
    sql = _sql(statement)
    assert sql.startswith("INSERT INTO insight_detector_states") and "project_insights" not in sql
    assert _params(statement)["measured"] is False
    assert _params(statement)["reason_code"] == "flake_few_runs"
    assert db.commits == 0


async def test_apply_clears_only_the_measured_subjects_that_did_not_fire() -> None:
    db = _Session(_Result([SimpleNamespace(id=uuid.uuid4(), inserted=True)]), _Result(rowcount=1))
    result = _result(findings=[_finding("suite:a")], measured_subjects={"suite:a", "suite:b", "suite:c"})
    counts = await store.apply_result(db, uuid.uuid4(), result, now=_now())
    assert counts == {"inserted": 1, "confirmed": 0, "cleared": 1}
    upsert, clear, state = db.statements
    assert _sql(upsert).startswith("INSERT INTO project_insights")
    compiled = clear.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    assert sql.startswith("UPDATE project_insights SET resolved_at=") and "resolved_at IS NULL" in sql
    assert "window_key !=" not in sql
    quiet = [v for k, v in compiled.params.items() if k.startswith("subject_key")]
    assert quiet == [["suite:b", "suite:c"]]
    assert compiled.params["resolved_reason"] == "cleared"
    assert _params(state)["subjects_measured"] == 3 and _params(state)["firing"] == 1
    assert db.commits == 0


async def test_apply_with_nothing_quiet_issues_no_clear() -> None:
    db = _Session(_Result([SimpleNamespace(id=uuid.uuid4(), inserted=False)]))
    counts = await store.apply_result(
        db, uuid.uuid4(), _result(findings=[_finding("suite:a")], measured_subjects={"suite:a"}), now=_now(),
    )
    assert counts == {"inserted": 0, "confirmed": 1, "cleared": 0}
    assert len(db.statements) == 2     # the upsert and the state row


async def test_cost_clears_the_earlier_billing_periods() -> None:
    db = _Session(_Result([SimpleNamespace(id=uuid.uuid4(), inserted=True)]), _Result(rowcount=1))
    result = _result("cost_budget", findings=[_finding("budget", "high")], measured_subjects={"budget"},
                     window_key="month:2026-11", clear_earlier=True)
    await store.apply_result(db, uuid.uuid4(), result, now=_now())
    compiled = db.statements[1].compile(dialect=postgresql.dialect())
    assert "project_insights.window_key != %(window_key_1)s" in str(compiled)
    assert compiled.params["window_key_1"] == "month:2026-11"
    assert "subject_key IN" not in str(compiled)


async def test_resolve_stale_and_record_failure() -> None:
    now = _now()
    db = _Session(_Result(rowcount=2))
    assert await store.resolve_stale(db, uuid.uuid4(), now=now) == 2
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    assert "project_insights.last_seen_at <" in str(compiled)
    assert compiled.params["last_seen_at_1"] == now - timedelta(hours=72)
    assert compiled.params["resolved_reason"] == "stale"
    db = _Session()
    await store.record_detector_failure(db, uuid.uuid4(), "coverage_drop", now=now)
    [statement] = db.statements
    params = _params(statement)
    assert (params["measured"], params["reason_code"], params["kind"]) == (False, "detector_failed", "coverage_drop")
    assert "project_insights" not in _sql(statement)


# ── Titles and links ──────────────────────────────────────────────────────────

_SCRIPT = "<script>alert(1)</script> Payments sentinel"


@pytest.mark.parametrize(
    "kind, evidence, expected",
    [
        ("perf_drift", {"ratio": 2.345, "baseline_runs": 20},
         f"Suite {_SCRIPT} is 2.3× slower than its previous 20 runs"),
        ("perf_drift", {"test_fingerprint": "fp_alpha", "pct": 49.6, "spike_runs": 4},
         f"{_SCRIPT} is slower: +50 % vs its baseline in 4 runs this week"),
        ("flake_rising", {"previous": 1, "current": 4}, f"Flaky tests rising in suite {_SCRIPT} (1 → 4)"),
        ("coverage_drop", {"previous": 100, "current": 80, "pct": 20},
         "Tests exercised fell from 100 to 80 (−20 %) in a week"),
        ("cost_budget", {"status": "SOFT_WARN", "utilization_pct": 85.4, "hard_cap_usd": 10.0},
         "AI spend is at 85 % of the $10 monthly cap"),
        ("cost_budget", {"status": "CAPPED", "utilization_pct": 101.0, "hard_cap_usd": 12.5},
         "AI spend reached the $12.50 monthly cap"),
    ],
)
def test_render_title_per_kind_keeps_the_label_verbatim(kind: str, evidence: dict, expected: str) -> None:
    assert store.render_title(kind, _SCRIPT, evidence) == expected


def test_insight_link_stays_inside_the_allow_list() -> None:
    run_id = uuid.uuid4()
    for kind in INSIGHT_KINDS:
        path, label, run_path = store.insight_link(kind, {"newest_run_id": str(run_id)})
        assert (path, label) == INSIGHT_LINKS[kind]
        assert run_path == (f"/runs/{run_id}?tab=changes" if kind == "perf_drift" else None)
    for bad in ("javascript:alert(1)", "../../admin", None, 7, "not-a-uuid"):
        assert store.insight_link("perf_drift", {"newest_run_id": bad})[2] is None


# ── list_insights and dismiss_insight ─────────────────────────────────────────


async def test_list_insights_refuses_an_unknown_range() -> None:
    db = _Session()
    with pytest.raises(store.UnknownRange) as raised:
        await store.list_insights(db, uuid.uuid4(), range_key="1h", include_dismissed=False, limit=50, now=_now())
    assert raised.value.status_code == 422
    assert raised.value.detail() == {"code": "unknown_range", "message": "Unknown range. Valid: 24h, 7d."}
    assert db.statements == []


def _row(**over: Any) -> SimpleNamespace:
    now = _now()
    base = dict(id=uuid.uuid4(), kind="flake_rising", severity="medium", resolved_at=None, resolved_reason=None,
                subject_label="Payments sentinel", window_key="7d", evidence={"previous": 1, "current": 4},
                first_seen_at=now, last_seen_at=now, detection_count=2, dismissed_at=None, dismissed_by_name=None)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch) -> dict:
    from app.services import feature_flags

    state = {"on": True, "asked": []}

    async def _is_enabled(key: str, **kwargs: Any) -> bool:
        state["asked"].append((key, kwargs.get("project_id")))
        return state["on"]

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    return state


async def test_list_insights_is_three_selects_and_maps_the_detectors(flag: dict) -> None:
    now = _now()
    project_id = uuid.uuid4()
    state = SimpleNamespace(kind="perf_drift", measured=True, reason_code="perf_tests_flag_off",
                            evaluated_at=now, subjects_measured=3, firing=1)
    failed = SimpleNamespace(kind="coverage_drop", measured=False, reason_code="detector_failed",
                             evaluated_at=now, subjects_measured=0, firing=0)
    db = _Session(_Result([_row()]), _Result([SimpleNamespace(total=7, dismissed_total=2)]), _Result([state, failed]))
    body = await store.list_insights(db, project_id, range_key="24h", include_dismissed=False, limit=5, now=now)
    assert flag["asked"] == [("cross_run_insights", project_id)]
    assert len(db.statements) == 3 and all(_sql(s).startswith("SELECT") for s in db.statements)
    assert (body["enabled"], body["range"], body["total"], body["dismissed_total"]) == (True, "24h", 7, 2)
    [item] = body["items"]
    assert item["title"] == "Flaky tests rising in suite Payments sentinel (1 → 4)"
    assert item["status"] == "open" and item["link"] == {"path": "/flaky", "label": "Flaky tests"}
    detectors = {d["kind"]: d for d in body["detectors"]}
    assert [d["kind"] for d in body["detectors"]] == list(INSIGHT_KINDS)
    assert detectors["perf_drift"]["measured"] is True
    assert detectors["perf_drift"]["reason"] == NOT_MEASURED["perf_tests_flag_off"]
    assert detectors["coverage_drop"]["reason"] == NOT_MEASURED["detector_failed"]
    assert detectors["flake_rising"] == {"kind": "flake_rising", "measured": False,
                                         "reason": NOT_MEASURED["not_evaluated"], "evaluated_at": None,
                                         "subjects_measured": 0, "firing": 0}
    rows_sql = _sql(db.statements[0])
    assert "project_insights.dismissed_at IS NULL" in rows_sql and "LIMIT" in rows_sql
    assert "count(*) FILTER (WHERE project_insights.dismissed_at IS NOT NULL)" in _sql(db.statements[1])


async def test_list_insights_flag_off_keeps_the_rows_and_says_so(flag: dict) -> None:
    flag["on"] = False
    db = _Session(_Result([_row(resolved_at=_now(), resolved_reason="cleared")]),
                  _Result([SimpleNamespace(total=1, dismissed_total=0)]), _Result([]))
    body = await store.list_insights(db, uuid.uuid4(), range_key="7d", include_dismissed=True, limit=50, now=_now())
    assert body["enabled"] is False and body["items"][0]["status"] == "resolved"
    assert {d["reason"] for d in body["detectors"]} == {NOT_MEASURED["flag_off"]}
    assert "dismissed_at IS NULL" not in _sql(db.statements[0])


async def test_dismiss_insight() -> None:
    now = _now()
    user = SimpleNamespace(id=uuid.uuid4(), full_name="", username="alice_lead", email="alice@example.invalid")
    row = _row()
    db = _Session(_Result([row]))
    got, changed = await store.dismiss_insight(db, uuid.uuid4(), row.id, user=user, now=now)
    assert changed is True and got is row and db.flushes == 1
    assert (row.dismissed_at, row.dismissed_by_id, row.dismissed_by_name) == (now, user.id, "alice_lead")
    sql = _sql(db.statements[0])
    assert "project_insights.id = " in sql and "project_insights.project_id = " in sql and "FOR UPDATE" in sql
    # A second dismissal writes nothing.
    db = _Session(_Result([row]))
    assert await store.dismiss_insight(db, uuid.uuid4(), row.id, user=user, now=now) == (row, False)
    assert db.flushes == 0
    with pytest.raises(store.InsightNotFound) as raised:
        await store.dismiss_insight(_Session(_Result([])), uuid.uuid4(), uuid.uuid4(), user=user, now=now)
    assert raised.value.status_code == 404
    assert raised.value.detail() == {"code": "insight_not_found", "message": "Insight not found."}


