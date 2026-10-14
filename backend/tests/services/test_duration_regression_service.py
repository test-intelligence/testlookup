"""EXEC-05: a run's duration regressions -- per-test spikes, suite regressions,
the once-only baseline feed, and the "became slower" notification.

The fake session below answers by the SQL each statement compiles to (Postgres
dialect), so the service runs unmodified against it: the real Welford update,
the real ``assess_run_durations`` / ``feed_run_observations`` /
``refresh_baselines`` and the real notification staging
(``notification.manager.stage_scoped_preference_deliveries``).

Fixture (story): a baseline of mean 100 ms +/- 10 ms over 20 runs, and a run
where ``checkout::test_pay`` took 400 ms -> a spike of ~29 sigma.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import AppSetting, NotificationChannel, PerfBaseline
from app.services import duration_regression_service as svc
from app.services import perf_regression_service as perf

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
FP_PAY = "fp-pay"
FP_CART = "fp-cart"


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _params(stmt) -> dict[str, Any]:
    return dict(stmt.compile(dialect=postgresql.dialect()).params)


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


def _baseline(fp: str, samples: list[int]) -> PerfBaseline:
    baseline = PerfBaseline(project_id=PROJECT, test_fingerprint=fp, test_name=fp, suite_name="checkout")
    for value in samples:
        perf._welford_update(baseline, value)
    return baseline


def _steady(n: int = 20) -> list[int]:
    """n samples alternating 90/110 ms: mean 100, stddev ~10."""
    return [90 if i % 2 else 110 for i in range(n)]


class FakeDB:
    """One project's tables, answered by SQL shape."""

    def __init__(
        self,
        *,
        runs: dict[uuid.UUID, SimpleNamespace],
        cases: list[SimpleNamespace],
        baselines: Optional[dict[str, PerfBaseline]] = None,
        cursor: Optional[datetime] = None,
        suite_rows: Optional[list[SimpleNamespace]] = None,
        logs: Optional[list[dict]] = None,
        prefs: Optional[list[tuple[Any, str]]] = None,
    ):
        self.runs = runs
        self.cases = cases
        self.baselines = baselines if baselines is not None else {}
        self.cursor = cursor
        self.suite_rows = suite_rows or []
        self.logs = logs or []
        self.prefs = prefs or []
        self.inserted_logs: list[dict[str, Any]] = []
        self.sql: list[str] = []
        self.commit = AsyncMock()

    # session protocol -----------------------------------------------------
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        if isinstance(obj, PerfBaseline):
            self.baselines[obj.test_fingerprint] = obj
        elif isinstance(obj, AppSetting):
            self._setting = obj

    async def flush(self):
        return None

    # helpers ----------------------------------------------------------------
    def _cases_of(self, run_id):
        return [c for c in self.cases if c.test_run_id == run_id]

    def _setting_row(self):
        setting = getattr(self, "_setting", None)
        if setting is not None:
            return setting
        if self.cursor is None:
            return None
        return AppSetting(key=perf._REFRESH_CURSOR_KEY, value={"last_created_at": self.cursor.isoformat()})

    async def execute(self, stmt):
        sql = _sql(stmt)
        self.sql.append(sql)
        params = _params(stmt)
        if sql.startswith("UPDATE test_runs"):
            run_id = next(v for k, v in params.items() if k.startswith("id_"))
            self.runs[run_id].duration_findings = params["duration_findings"]
            return _Result(rowcount=1)
        if sql.startswith("INSERT INTO app_settings"):  # ON CONFLICT DO NOTHING
            if self._setting_row() is None:
                self._setting = AppSetting(key=perf._REFRESH_CURSOR_KEY, value=params["value"])
            self.created_cursor_row = True
            return _Result()
        if sql.startswith("INSERT INTO notification_logs"):
            rows: dict[str, dict[str, Any]] = {}
            for key, value in params.items():
                m = re.match(r"(.+)_m(\d+)$", key)
                if m:
                    rows.setdefault(m.group(2), {})[m.group(1)] = value
            if not rows:
                rows["0"] = params
            self.inserted_logs.extend(rows.values())
            return _Result()
        if "FROM app_settings" in sql:
            row = self._setting_row()
            return _Result([row] if row else [])
        if "FROM notification_logs" in sql:
            return _Result([(log,) for log in self.logs])
        if "FROM notification_preferences" in sql:
            return _Result(self.prefs)
        if "FROM projects" in sql:
            return _Result(["Checkout Web"])
        if "FROM perf_baselines" in sql:
            return _Result(list(self.baselines.values()))
        if "FROM suite_executions" in sql:
            return self._suites(sql, params)
        if "FROM test_runs" in sql and "->>" in sql:
            rows = []
            for run in self.runs.values():
                marker = (run.duration_findings or {}).get("baseline_fed_through")
                if marker:
                    rows.append(SimpleNamespace(id=run.id, fed_through=marker))
            return _Result(rows)
        if "FROM test_runs" in sql:
            run_id = next(v for k, v in params.items() if k.startswith("id_"))
            run = self.runs.get(run_id)
            if "test_runs.duration_findings" in sql and "test_runs.project_id" not in sql:
                return _Result([run.duration_findings] if run else [])
            return _Result([run] if run else [])
        if "FROM test_cases" in sql:
            return self._test_cases(sql, params)
        raise AssertionError(f"unexpected statement: {sql}")

    def _test_cases(self, sql: str, params: dict[str, Any]):
        run_id = next((v for k, v in params.items() if k.startswith("test_run_id_")), None)
        rows = self._cases_of(run_id) if run_id else list(self.cases)
        rows = [c for c in rows if c.duration_ms and c.duration_ms > 0]
        cursor = next((v for k, v in params.items() if k.startswith("created_at_")), None)
        if cursor is not None:
            rows = [c for c in rows if c.created_at > cursor]
        if "test_cases.status = " in sql:  # the assessment: PASSED only
            rows = [c for c in rows if c.status == "PASSED" and c.test_fingerprint]
        else:  # the feeds: PASSED or FAILED
            rows = [c for c in rows if c.status in ("PASSED", "FAILED")]
        return _Result(sorted(rows, key=lambda c: c.created_at))

    def _suites(self, sql: str, params: dict[str, Any]):
        run_id = next((v for k, v in params.items() if k.startswith("test_run_id_")), None)
        if "row_number()" not in sql:
            return _Result([r for r in self.suite_rows if r.test_run_id == run_id])
        earlier = sorted(
            (r for r in self.suite_rows if r.test_run_id != run_id),
            key=lambda r: r.run_created_at,
            reverse=True,
        )
        return _Result(earlier[: svc.SUITE_RECENT_RUNS + svc.SUITE_BASELINE_RUNS])


def _run(run_id=None, **kw) -> SimpleNamespace:
    run_id = run_id or uuid.uuid4()
    return SimpleNamespace(
        id=run_id, project_id=PROJECT, branch=kw.get("branch", "main"),
        build_number=kw.get("build_number", "142"), duration_findings=kw.get("findings"),
    )


def _case(run_id, fp, ms, *, status="PASSED", at=None, name=None):
    return SimpleNamespace(
        test_run_id=run_id, project_id=PROJECT, test_fingerprint=fp,
        test_name=name or f"checkout::{fp}", suite_name="checkout", duration_ms=ms,
        status=status, created_at=at or BASE + timedelta(hours=1),
    )


def _flag(on: bool):
    return patch.object(perf, "_feature_enabled", AsyncMock(return_value=on))


def _no_stage():
    """Notification staging is asserted in its own tests."""
    return patch(
        "app.services.notification.manager.stage_scoped_preference_deliveries", AsyncMock(),
    )


# ── Per-test spikes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_4x_run_against_a_100_ms_baseline_is_a_spike_and_feeds_the_baseline_once():
    run = _run()
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400), _case(run.id, FP_CART, 101)],
        baselines={FP_PAY: _baseline(FP_PAY, _steady()), FP_CART: _baseline(FP_CART, _steady())},
    )
    with _flag(True), _no_stage():
        out = await svc.evaluate_duration_regressions(db, run.id)

    assert out["measured"] is True and out["reason"] is None
    [spike] = out["spikes"]
    assert spike["test_fingerprint"] == FP_PAY
    assert spike["observed_ms"] == 400
    assert spike["baseline_mean_ms"] == 100.0
    assert spike["delta_ms"] == 300
    assert spike["sigma"] > 25  # judged against the baseline WITHOUT the outlier
    assert spike["new"] is True and spike["was_slow_before"] is False
    assert out["test_check"] == {
        "measured": True, "reason": None, "considered": 2, "judged": 2,
        "history": 20, "required_history": 10, "sigma": 3.0,
    }
    # Fed once: both tests gained exactly one sample.
    assert db.baselines[FP_PAY].sample_count == 21
    assert db.baselines[FP_CART].sample_count == 21
    stored = run.duration_findings
    assert stored["baseline_fed_through"] == (BASE + timedelta(hours=1)).isoformat()
    assert "baseline_fed_through" not in out, "bookkeeping is not served"
    # The judgement came first: the assessment query precedes the feed's.
    assessment = next(i for i, s in enumerate(db.sql) if "test_cases.status = " in s)
    feed = next(i for i, s in enumerate(db.sql) if "test_cases.status IN" in s)
    assert assessment < feed


@pytest.mark.asyncio
async def test_without_ten_samples_the_run_is_not_measured_and_says_how_much_history_there_is():
    run = _run()
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400)],
        baselines={FP_PAY: _baseline(FP_PAY, [100, 105, 95, 100])},
    )
    with _flag(True), _no_stage():
        out = await svc.evaluate_duration_regressions(db, run.id)

    assert out["measured"] is False
    assert out["reason"] == "not enough history (4 of 10)"
    assert out["spikes"] == [] and out["spike_count"] == 0
    assert out["test_check"]["measured"] is False
    assert out["suite_check"]["reason"] == "no suite timing was recorded for this run"


@pytest.mark.asyncio
async def test_with_the_flag_off_nothing_is_judged_or_fed_and_the_reason_says_so():
    run = _run()
    baseline = _baseline(FP_PAY, _steady())
    db = FakeDB(runs={run.id: run}, cases=[_case(run.id, FP_PAY, 400)], baselines={FP_PAY: baseline})
    with _flag(False), _no_stage():
        out = await svc.evaluate_duration_regressions(db, run.id)

    assert out["measured"] is False
    assert "perf_regression_detection" in out["reason"]
    assert out["spikes"] == []
    assert baseline.sample_count == 20, "a project with the flag off is never fed"
    assert not any("FROM app_settings" in s for s in db.sql)
    assert not any("FROM perf_baselines" in s for s in db.sql)
    assert "baseline_fed_through" not in run.duration_findings


@pytest.mark.asyncio
async def test_an_unknown_run_is_none():
    db = FakeDB(runs={}, cases=[])
    with _flag(True), _no_stage():
        assert await svc.evaluate_duration_regressions(db, uuid.uuid4()) is None
        assert await svc.get_duration_regressions(db, uuid.uuid4()) is None


# ── The double-feed guard ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_fed_on_ingest_is_not_fed_again_by_the_nightly_sweep():
    """sample_count +1, not +2 (EXEC-05 task 5)."""
    run = _run()
    other = _run()
    cursor = BASE  # the last sweep stopped before both runs
    cases = [
        _case(run.id, FP_PAY, 120, at=BASE + timedelta(minutes=10)),
        _case(other.id, FP_PAY, 100, at=BASE + timedelta(minutes=20)),  # not fed on ingest
    ]
    db = FakeDB(runs={run.id: run, other.id: other}, cases=cases,
                baselines={FP_PAY: _baseline(FP_PAY, _steady())}, cursor=cursor)
    with _flag(True), _no_stage():
        await svc.evaluate_duration_regressions(db, run.id)
    assert db.baselines[FP_PAY].sample_count == 21
    assert run.duration_findings["baseline_fed_through"] == (BASE + timedelta(minutes=10)).isoformat()

    saved: list[datetime] = []

    async def save(_db, value):
        saved.append(value)

    with _flag(True), patch.object(perf, "AsyncSessionLocal", lambda: db), \
            patch.object(perf, "_save_refresh_cursor", save):
        out = await perf.refresh_baselines()

    # Only the OTHER run's row was swept in; this run's row was skipped ...
    assert db.baselines[FP_PAY].sample_count == 22
    assert out["observed"] == 1
    # ... and the watermark still moved past both.
    assert saved == [BASE + timedelta(minutes=20)]


@pytest.mark.asyncio
async def test_before_the_first_sweep_the_feed_makes_a_cursor_row_to_lock_and_the_sweep_still_skips_the_run():
    run, older = _run(), _run()
    cases = [
        _case(older.id, FP_PAY, 100, at=BASE + timedelta(minutes=5)),  # never swept, never fed
        _case(run.id, FP_PAY, 120, at=BASE + timedelta(minutes=10)),
    ]
    db = FakeDB(runs={run.id: run, older.id: older}, cases=cases,
                baselines={FP_PAY: _baseline(FP_PAY, _steady())}, cursor=None)
    with _flag(True), _no_stage():
        await svc.evaluate_duration_regressions(db, run.id)
    assert getattr(db, "created_cursor_row", False), "the lock needs a row"
    assert db._setting.value == {}, "an empty cursor: the first sweep still starts from the beginning"
    assert db.baselines[FP_PAY].sample_count == 21

    with _flag(True), patch.object(perf, "AsyncSessionLocal", lambda: db):
        out = await perf.refresh_baselines()
    assert out["observed"] == 1, "the older run's row, not this run's"
    assert db.baselines[FP_PAY].sample_count == 22
    assert db._setting.value == {"last_created_at": (BASE + timedelta(minutes=10)).isoformat()}


@pytest.mark.asyncio
async def test_an_on_ingest_feed_skips_rows_the_sweep_already_swept():
    run = _run()
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 120, at=BASE + timedelta(minutes=10))],
        baselines={FP_PAY: _baseline(FP_PAY, _steady())},
        cursor=BASE + timedelta(hours=2),  # the sweep already passed this run
    )
    with _flag(True), _no_stage():
        await svc.evaluate_duration_regressions(db, run.id)
    assert db.baselines[FP_PAY].sample_count == 20
    assert "baseline_fed_through" not in run.duration_findings
    # The feed takes the cursor row's lock, as the sweep does.
    assert any("FROM app_settings" in s and "FOR UPDATE" in s for s in db.sql)


@pytest.mark.asyncio
async def test_re_evaluating_a_run_neither_feeds_nor_re_judges_nor_notifies():
    run = _run()
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400)],
        baselines={FP_PAY: _baseline(FP_PAY, _steady())},
    )
    stage = AsyncMock()
    with _flag(True), patch(
        "app.services.notification.manager.stage_scoped_preference_deliveries", stage,
    ):
        first = await svc.evaluate_duration_regressions(db, run.id)
        second = await svc.evaluate_duration_regressions(db, run.id)

    assert db.baselines[FP_PAY].sample_count == 21
    assert second["spikes"] == first["spikes"], "the first judgement (pre-feed) is kept"
    assert second["evaluated_at"] == first["evaluated_at"]
    assert second["notified"] == first["notified"] == {"tests": [FP_PAY], "suites": []}
    assert stage.await_count == 1


# ── Transition: the first slow run, not every one ────────────────────────────


@pytest.mark.asyncio
async def test_the_next_slow_run_is_still_a_spike_but_not_new():
    first, second = _run(build_number="142"), _run(build_number="143")
    baseline = _baseline(FP_PAY, _steady(100))
    db = FakeDB(
        runs={first.id: first, second.id: second},
        cases=[
            _case(first.id, FP_PAY, 400, at=BASE + timedelta(minutes=1)),
            _case(second.id, FP_PAY, 400, at=BASE + timedelta(minutes=2)),
        ],
        baselines={FP_PAY: baseline},
    )
    stage = AsyncMock()
    with _flag(True), patch(
        "app.services.notification.manager.stage_scoped_preference_deliveries", stage,
    ):
        one = await svc.evaluate_duration_regressions(db, first.id)
        two = await svc.evaluate_duration_regressions(db, second.id)

    assert [s["new"] for s in one["spikes"]] == [True]
    assert one["notified"]["tests"] == [FP_PAY]
    assert [s["test_fingerprint"] for s in two["spikes"]] == [FP_PAY]
    assert two["spikes"][0]["was_slow_before"] is True
    assert two["spikes"][0]["new"] is False
    assert two["notified"]["tests"] == []
    assert stage.await_count == 1, "one message: the first slow run only"


def test_a_finding_named_in_the_cooldown_is_not_announced_again():
    spikes = [{"test_fingerprint": FP_PAY, "was_slow_before": False}]
    suites = [{"suite_key": "checkout", "was_slow_before": False}]
    assert svc.pick_new_findings(spikes, suites, set()) == (spikes, suites)
    assert svc.pick_new_findings(spikes, suites, {f"test:{FP_PAY}", "suite:checkout"}) == ([], [])


@pytest.mark.asyncio
async def test_the_cooldown_reads_the_keys_of_recent_slow_messages():
    run = _run()
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400)],
        baselines={FP_PAY: _baseline(FP_PAY, _steady())},
        logs=[{"duration_keys": [f"test:{FP_PAY}"]}],
    )
    stage = AsyncMock()
    with _flag(True), patch(
        "app.services.notification.manager.stage_scoped_preference_deliveries", stage,
    ):
        out = await svc.evaluate_duration_regressions(db, run.id)
    assert out["spikes"][0]["new"] is True
    assert out["notified"]["tests"] == []
    stage.assert_not_awaited()
    log_query = next(s for s in db.sql if "FROM notification_logs" in s)
    assert "notification_logs.project_id" in log_query and "created_at >=" in log_query


# ── Suite regressions ────────────────────────────────────────────────────────


def _suite_history(run_id, current_ms: int, earlier: list[int], *, wall=True):
    rows = [SimpleNamespace(
        test_run_id=run_id, suite_key="checkout", suite_name="Checkout",
        wall_ms=current_ms if wall else None, summed_ms=current_ms * 2,
        run_created_at=BASE + timedelta(days=60),
    )]
    for i, ms in enumerate(earlier):
        rows.append(SimpleNamespace(
            test_run_id=uuid.uuid4(), suite_key="checkout", suite_name="Checkout",
            wall_ms=ms if wall else None, summed_ms=ms * 2,
            run_created_at=BASE + timedelta(days=59 - i),
        ))
    return rows


@pytest.mark.asyncio
async def test_a_suite_whose_last_five_runs_are_1_8x_its_previous_twenty_is_a_new_regression():
    run = _run()
    db = FakeDB(
        runs={run.id: run}, cases=[],
        suite_rows=_suite_history(run.id, 1800, [1800, 1800] + [1000] * 22),
    )
    with _flag(False), _no_stage():
        out = await svc.evaluate_duration_regressions(db, run.id)
    assert out["measured"] is True, "suites need no flag"
    [suite] = out["suites"]
    assert suite["suite_name"] == "Checkout"
    assert suite["metric"] == "wall"
    assert suite["ratio"] == 1.8
    assert suite["baseline_runs"] == 20
    assert suite["new"] is True
    assert out["suite_check"]["measured"] is True and out["suite_check"]["evaluated"] == 1
    history_sql = next(s for s in db.sql if "row_number()" in s)
    assert "suite_executions.branch IS NOT DISTINCT FROM" in history_sql
    assert "suite_executions.project_id =" in history_sql


def test_a_suite_already_slow_on_its_previous_run_is_not_new():
    rows = [{"wall_ms": ms, "summed_ms": ms} for ms in [1800] * 5 + [1000] * 21]
    verdict = svc.judge_suite_history(rows)
    assert verdict.regression is True and verdict.was_regression_before is True


def test_a_suite_needs_fifteen_runs_and_one_slow_run_is_not_a_regression():
    short = [{"wall_ms": 1000, "summed_ms": 1000}] * 14
    verdict = svc.judge_suite_history(short)
    assert verdict.measured is False and verdict.history == 14
    one_slow = [{"wall_ms": 4000, "summed_ms": 4000}] + [{"wall_ms": 1000, "summed_ms": 1000}] * 24
    verdict = svc.judge_suite_history(one_slow)
    assert verdict.measured is True and verdict.regression is False  # a median of 5


def test_a_window_with_a_missing_wall_clock_is_judged_on_summed_time_never_a_mix():
    rows = [{"wall_ms": 1800, "summed_ms": 3600}] + [{"wall_ms": None, "summed_ms": 2000}] * 24
    verdict = svc.judge_suite_history(rows)
    assert verdict.metric == "summed"
    assert verdict.observed_ms == 3600


@pytest.mark.asyncio
async def test_thin_suite_history_is_not_measured_with_the_count():
    run = _run()
    db = FakeDB(runs={run.id: run}, cases=[], suite_rows=_suite_history(run.id, 1800, [1000] * 3))
    with _flag(False), _no_stage():
        out = await svc.evaluate_duration_regressions(db, run.id)
    assert out["suite_check"]["reason"] == "not enough history (4 of 15)"
    assert out["measured"] is False


# ── The read ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_read_serves_stored_findings_and_writes_nothing_for_an_unevaluated_run():
    run = _run()
    baseline = _baseline(FP_PAY, _steady())
    db = FakeDB(runs={run.id: run}, cases=[_case(run.id, FP_PAY, 400)], baselines={FP_PAY: baseline})
    with _flag(True):
        live = await svc.get_duration_regressions(db, run.id)
    assert live["stored"] is False and live["evaluated_at"] is None
    assert [s["test_fingerprint"] for s in live["spikes"]] == [FP_PAY]
    assert baseline.sample_count == 20, "a GET must not feed the baseline"
    assert run.duration_findings is None
    assert not any(s.startswith(("UPDATE", "INSERT")) for s in db.sql)

    run.duration_findings = {"measured": False, "reason": "not enough history (3 of 10)",
                             "spikes": [], "suites": [], "baseline_fed_through": "x"}
    stored = await svc.get_duration_regressions(db, run.id)
    assert stored["stored"] is True and stored["reason"] == "not enough history (3 of 10)"
    assert "baseline_fed_through" not in stored


def test_the_policy_summary_is_compact_and_none_without_findings():
    assert svc.policy_summary(None) is None
    summary = svc.policy_summary({
        "measured": True, "reason": None, "spike_count": 2,
        "spikes": [{"test_name": "a", "sigma": 4.1, "observed_ms": 400, "baseline_mean_ms": 100,
                    "baseline_stddev_ms": 10, "test_fingerprint": "x"}],
        "suite_regression_count": 1, "suites": [{"suite_name": "s", "ratio": 1.8, "suite_key": "s"}],
    })
    assert summary == {
        "measured": True, "reason": None, "spike_count": 2,
        "spikes": [{"test_name": "a", "suite_name": None, "sigma": 4.1, "observed_ms": 400,
                    "baseline_mean_ms": 100}],
        "suite_regression_count": 1, "suites": [{"suite_name": "s", "ratio": 1.8}],
    }


# ── Notification emission through the real staging ──────────────────────────


def _pref(events: list[str], channel=NotificationChannel.SLACK):
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=None, channel=channel,
        enabled=True, events=events, failure_rate_threshold=80.0, email_override=None,
        slack_webhook_url="https://hooks.fake.test/services/T000/B000/XXXX", teams_webhook_url=None,
    )


@pytest.mark.asyncio
async def test_a_new_slow_test_stages_one_message_per_subscribed_channel_only():
    run = _run(build_number="142")
    subscribed = _pref(["run_failed", "test.newly_slow", "suite.newly_slow"])
    not_subscribed = _pref(["run_failed", "test.newly_failing"])
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400)],
        baselines={FP_PAY: _baseline(FP_PAY, _steady())},
        prefs=[(subscribed, "lead@example.com"), (not_subscribed, "dev@example.com")],
    )
    with _flag(True):
        await svc.evaluate_duration_regressions(db, run.id)

    [row] = db.inserted_logs
    assert row["user_id"] == subscribed.user_id
    assert row["event_type"] == "test.newly_slow"
    assert row["channel"] == "slack"
    assert row["status"] == "pending"
    assert row["run_id"] == run.id and row["project_id"] == PROJECT
    assert row["title"] == "🐢 1 test became slower — Checkout Web build #142"
    assert "Slower than baseline: checkout::fp-pay +300 ms" in row["body"]
    assert row["delivery_metadata"]["duration_keys"] == [f"test:{FP_PAY}"]
    assert row["delivery_metadata"]["dashboard_url"].endswith(f"/runs/{run.id}?tab=changes")


class _RelayDB:
    """The relay's sessions: claims the staged row, answers the recipient
    checks, and accepts the token-fenced outcome write."""

    def __init__(self, staged, pref, email):
        self.staged = staged
        self.pref = pref
        self.email = email
        self.outcomes: list[dict[str, Any]] = []
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        sql = _sql(stmt)
        if sql.startswith("UPDATE notification_logs"):
            self.outcomes.append(_params(stmt))
            return _Result(rowcount=1)
        if "FROM notification_logs" in sql:
            return _Result(self.staged)
        if "FROM notification_preferences" in sql:
            return _Result([(self.pref, self.email)])
        if "FROM project_members" in sql:
            return _Result([(self.pref.user_id, PROJECT)])
        if "FROM users" in sql:
            return _Result([(self.pref.user_id, True, "QA_LEAD")])
        raise AssertionError(f"unexpected relay statement: {sql}")


@pytest.mark.asyncio
async def test_the_staged_message_is_delivered_by_the_real_relay_to_slack(monkeypatch):
    """Drive the real path end to end: evaluation stages the row, the durable
    relay claims it, converts its event through ``NotificationEventType`` (a
    value missing from the enum fails right there) and posts it to a contract
    fake of Slack that refuses what Slack refuses."""
    import httpx

    from app.core.config import settings
    from app.models.postgres import NotificationLog
    from app.services import integration_config_service
    from app.services.notification import egress, manager
    from tests.fakes.fake_integrations import FakeState, create_app

    run = _run(build_number="142")
    pref = _pref(["test.newly_slow", "suite.newly_slow"])
    db = FakeDB(
        runs={run.id: run},
        cases=[_case(run.id, FP_PAY, 400)],
        baselines={FP_PAY: _baseline(FP_PAY, _steady())},
        prefs=[(pref, "lead@example.com")],
    )
    with _flag(True):
        await svc.evaluate_duration_regressions(db, run.id)
    [values] = db.inserted_logs
    staged = NotificationLog(**{**values, "delivery_attempts": 0})

    state = FakeState(base_url="https://fake-jira.example.test")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    relay_db = _RelayDB([staged], pref, "lead@example.com")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(egress, "delivery_http_client", lambda destination, deployment_wide=False: client)
    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: relay_db)
    monkeypatch.setattr(
        integration_config_service, "resolve_global_notification_webhooks", AsyncMock(return_value=None),
    )

    out = await manager.relay_pending_notification_deliveries()

    assert out == {"claimed": 1, "sent": 1, "retrying": 0, "failed": 0}
    [message] = state.slack_messages
    assert message["path"] == "/services/T000/B000/XXXX"
    assert message["payload"]["text"] == "🐢 1 test became slower — Checkout Web build #142"
    assert message["delivery_id"] == staged.delivery_key
    [outcome] = relay_db.outcomes
    assert outcome["status"] == "sent"
