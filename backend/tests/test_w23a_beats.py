"""W23a — the two beats' glue (FAIL-11 B-1 ``detect_cross_run_insights``, TRND-03
B-2 ``check_run_cadences``) without Redis, Postgres or Celery.

The module-level sweeps run on scripted sessions (the factory counts how many
are open at once), a scripted dedup lock and recorder services, so each rule of
the glue is asserted on its own: the global lock held / unavailable (fail
closed, nothing opened), the flag, the isolation per project and per detector,
"never two sessions open at once", the summary log (ids, counts and a
duration -- never a name), and the two schedule entries with their queue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from celery.schedules import crontab
from sqlalchemy.dialects import postgresql

from app.services.insight_detectors import DetectorResult, Finding
from app.worker import tasks


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list:
        return list(self._rows)


class _World:
    """The scripted environment: sessions, the lock, the project ids."""

    def __init__(self, project_ids: list) -> None:
        self.project_ids = project_ids
        self.open = 0
        self.max_open = 0
        self.sessions = 0
        self.commits = 0
        self.sql: list[str] = []
        self.lock = "free"          # "free" | "held" | "raise"
        self.released: list[tuple[str, str]] = []

    def factory(self) -> "_Session":
        return _Session(self)


class _Session:
    def __init__(self, world: _World) -> None:
        self.world = world

    async def __aenter__(self) -> "_Session":
        self.world.open += 1
        self.world.sessions += 1
        self.world.max_open = max(self.world.max_open, self.world.open)
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.world.open -= 1
        return False

    async def execute(self, statement: Any, *_: Any, **__: Any) -> _Result:
        self.world.sql.append(" ".join(str(statement.compile(dialect=postgresql.dialect())).split()))
        return _Result(list(self.world.project_ids))

    async def commit(self) -> None:
        self.world.commits += 1


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append(("warning", event, kw))

    def named(self, event: str) -> list[dict]:
        return [kw for _, name, kw in self.events if name == event]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World([uuid.uuid4(), uuid.uuid4()])
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", w.factory, raising=False)

    async def _is_duplicate(key: str, ttl: int = 3600, owner: str | None = None) -> bool:
        assert owner, "the sweep lock must always pass owner="
        if w.lock == "raise":
            raise ConnectionError("redis down")
        return w.lock == "held"

    async def _release(key: str, owner: str) -> None:
        w.released.append((key, owner))

    monkeypatch.setattr(tasks, "_is_duplicate", _is_duplicate)
    monkeypatch.setattr(tasks, "_release_duplicate_lock", _release)
    return w


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> _Log:
    recorder = _Log()
    monkeypatch.setattr(tasks, "_slog", recorder)
    return recorder


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch) -> dict:
    """``cross_run_insights``: ``any`` for the deployment, ``off`` = projects with it off."""
    from app.services import feature_flags

    state: dict[str, Any] = {"any": True, "off": set(), "asked": []}

    async def _is_enabled(key: str, *, db: Any = None, project_id: Any = None, any_scope: bool = False,
                          **_: Any) -> bool:
        assert key == "cross_run_insights"
        state["asked"].append((project_id, any_scope))
        if any_scope:
            return state["any"]
        return project_id not in state["off"]

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    return state


def _now() -> datetime:
    return datetime(2026, 10, 28, 12, 40, tzinfo=timezone.utc)


# ── The insights sweep ────────────────────────────────────────────────────────


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, world: _World) -> dict:
    """Four scripted detectors and a recorder store; ``raise_kind`` makes one raise."""
    from app.services import insight_detectors, insights_service

    state: dict[str, Any] = {"raise_kind": None, "detected": [], "applied": [], "failures": [], "stale": []}

    def _detector(kind: str):
        async def _detect(db: Any, project_id: Any, *, now: datetime) -> DetectorResult:
            assert world.open == 1
            state["detected"].append((project_id, kind))
            if kind == state["raise_kind"]:
                raise RuntimeError("detector broke")
            return DetectorResult(kind=kind, window_key="7d", measured=True, reason_code=None,
                                  findings=(Finding("suite:x", "X", "medium", {}),),
                                  measured_subjects=frozenset({"suite:x", "suite:y"}), firing=1)
        return _detect

    async def _apply(db: Any, project_id: Any, result: DetectorResult, *, now: datetime) -> dict:
        state["applied"].append((project_id, result.kind))
        return {"inserted": 1, "confirmed": 0, "cleared": 1}

    async def _failure(db: Any, project_id: Any, kind: str, *, now: datetime) -> None:
        assert world.open == 1
        state["failures"].append((project_id, kind))

    async def _stale(db: Any, project_id: Any, *, now: datetime) -> int:
        state["stale"].append(project_id)
        return 2

    monkeypatch.setattr(insight_detectors, "DETECTORS", tuple(
        (kind, _detector(kind)) for kind in ("perf_drift", "flake_rising", "coverage_drop", "cost_budget")
    ))
    monkeypatch.setattr(insights_service, "apply_result", _apply)
    monkeypatch.setattr(insights_service, "record_detector_failure", _failure)
    monkeypatch.setattr(insights_service, "resolve_stale", _stale)
    return state


@pytest.mark.parametrize("lock, skipped", [("held", "already_running"), ("raise", "lock_unavailable")])
async def test_insights_lock_held_or_unavailable_skips_everything(
    world: _World, engine: dict, flags: dict, log: _Log, lock: str, skipped: str,
) -> None:
    world.lock = lock
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    assert out == {"skipped": skipped}
    assert world.sessions == 0 and engine["detected"] == [] and flags["asked"] == []
    assert world.released == []      # never released a lock it does not hold
    if lock == "raise":
        [warning] = log.named("sweep_lock_unavailable")
        assert warning == {"lock": "testlookup:lock:insights-sweep", "error_type": "ConnectionError"}


async def test_insights_any_scope_flag_off_skips_and_releases(world: _World, engine: dict, flags: dict) -> None:
    flags["any"] = False
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    assert out == {"skipped": "flag_off"} and world.sessions == 0 and engine["detected"] == []
    assert world.released == [("testlookup:lock:insights-sweep", "task-a")]


async def test_insights_sweep_isolates_each_detector(world: _World, engine: dict, flags: dict, log: _Log) -> None:
    engine["raise_kind"] = "coverage_drop"
    first, second = world.project_ids
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    # The failing detector rolled back nothing of the other three, on each project.
    for pid in (first, second):
        assert [k for p, k in engine["applied"] if p == pid] == ["perf_drift", "flake_rising", "cost_budget"]
        assert (pid, "coverage_drop") in engine["failures"]
    assert engine["stale"] == [first, second]
    assert out["projects"] == 2 and out["evaluated"] == 6 and out["failed"] == 2
    assert (out["inserted"], out["cleared"], out["stale"]) == (6, 6, 4)
    # Sessions: ids (1) + per project: flag (1), 4 detectors, 1 failure state, stale (1) = 7.
    assert world.sessions == 1 + 2 * 7 and world.max_open == 1
    assert world.commits == 2 * (3 + 1 + 1)
    assert "projects.is_active IS true" in world.sql[0]
    [done] = log.named("insights_sweep_done")
    assert isinstance(done["duration_ms"], int) and done["failed"] == 2
    failed = log.named("insight_detector_failed")
    assert failed == [{"project_id": str(pid), "kind": "coverage_drop", "error_type": "RuntimeError"}
                      for pid in (first, second)]
    assert world.released == [("testlookup:lock:insights-sweep", "task-a")]


async def test_insights_project_flag_off_opens_no_detector_session(world: _World, engine: dict, flags: dict) -> None:
    first, second = world.project_ids
    flags["off"] = {first}
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    assert {p for p, _ in engine["detected"]} == {second}
    assert first not in engine["stale"] and out["skipped_projects"] == 1
    assert world.sessions == 1 + 1 + (1 + 4 + 1)


async def test_insights_a_failing_project_does_not_stop_the_sweep(
    world: _World, engine: dict, flags: dict, monkeypatch: pytest.MonkeyPatch, log: _Log,
) -> None:
    from app.services import insights_service

    first, second = world.project_ids

    async def _stale(db: Any, project_id: Any, *, now: datetime) -> int:
        if project_id == first:
            raise RuntimeError("stale broke")
        return 0

    monkeypatch.setattr(insights_service, "resolve_stale", _stale)
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    assert {p for p, _ in engine["applied"]} == {first, second} and out["failed"] == 1
    assert log.named("insights_project_failed") == [{"project_id": str(first), "error_type": "RuntimeError"}]
    assert world.released


async def test_a_failure_recording_the_failure_is_swallowed(
    world: _World, engine: dict, flags: dict, monkeypatch: pytest.MonkeyPatch, log: _Log,
) -> None:
    from app.services import insights_service

    async def _failure(*_: Any, **__: Any) -> None:
        raise RuntimeError("state broke")

    engine["raise_kind"] = "perf_drift"
    monkeypatch.setattr(insights_service, "record_detector_failure", _failure)
    out = await tasks._cross_run_insights_sweep(owner="task-a", now=_now())
    assert out["evaluated"] == 6 and len(log.named("insight_detector_state_failed")) == 2


def test_the_insights_beat_entry_and_its_queue() -> None:
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["hourly-cross-run-insights"]
    assert entry["task"] == "app.worker.tasks.detect_cross_run_insights"
    assert entry["schedule"] == crontab(minute=40)
    assert entry["options"]["queue"] == "default" and entry["options"]["ignore_result"] is True
    task = celery_app.tasks["app.worker.tasks.detect_cross_run_insights"]
    assert task.queue == "default" and task.soft_time_limit == 1500 and task.time_limit == 1800


def test_the_insights_task_runs_the_sweep_with_its_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def _sweep(*, owner: str, now: Any = None) -> dict:
        seen.append(owner)
        return {"projects": 0, "failed": 0}

    monkeypatch.setattr(tasks, "_cross_run_insights_sweep", _sweep)
    out = tasks.detect_cross_run_insights.apply(task_id="beat-tick-a").get()
    assert out == {"projects": 0, "failed": 0} and seen == ["beat-tick-a"]


# ── The cadence check ─────────────────────────────────────────────────────────


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch, world: _World) -> dict:
    from app.services import run_cadence_service

    state: dict[str, Any] = {"raise_for": None, "checked": []}

    async def _check(db: Any, project_id: Any, *, now: datetime) -> dict:
        assert world.open == 1
        state["checked"].append(project_id)
        if project_id == state["raise_for"]:
            raise RuntimeError("check broke")
        return {"cadences": 2, "decided": 3, "missed": 1, "alerts": 1, "skipped_locked": 0}

    monkeypatch.setattr(run_cadence_service, "check_project_cadences", _check)
    return state


@pytest.mark.parametrize("lock, skipped", [("held", "already_running"), ("raise", "lock_unavailable")])
async def test_cadence_lock_held_or_unavailable_opens_no_session(
    world: _World, checks: dict, log: _Log, lock: str, skipped: str,
) -> None:
    world.lock = lock
    out = await tasks._run_cadence_check_sweep(owner="task-b", now=_now())
    assert out == {"skipped": skipped}
    assert world.sessions == 0 and checks["checked"] == [] and world.released == []


async def test_cadence_check_visits_active_projects_with_a_cadence_each_on_its_own_session(
    world: _World, checks: dict, log: _Log,
) -> None:
    first, second = world.project_ids
    checks["raise_for"] = first
    out = await tasks._run_cadence_check_sweep(owner="task-b", now=_now())
    # One project raising: the next is still checked and committed.
    assert checks["checked"] == [first, second]
    assert world.commits == 1 and world.max_open == 1 and world.sessions == 3
    assert out == {"projects": 2, "cadences": 2, "decided": 3, "missed": 1, "alerts": 1, "skipped_locked": 0,
                   "failed": 1, "duration_ms": out["duration_ms"]}
    ids_sql = world.sql[0]
    assert ids_sql.startswith("SELECT DISTINCT run_cadences.project_id FROM run_cadences JOIN projects")
    assert "projects.is_active IS true" in ids_sql
    assert log.named("run_cadence_check_failed") == [{"project_id": str(first), "error_type": "RuntimeError"}]
    [done] = log.named("run_cadence_check_done")
    assert isinstance(done["duration_ms"], int) and done["missed"] == 1
    assert world.released == [("testlookup:lock:run-cadence-check", "task-b")]


def test_the_cadence_beat_entry_and_its_queue() -> None:
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["quarter-hourly-run-cadence-check"]
    assert entry["task"] == "app.worker.tasks.check_run_cadences"
    assert entry["schedule"] == crontab(minute="7-59/15")
    assert sorted(entry["schedule"].minute) == [7, 22, 37, 52]
    assert entry["options"]["queue"] == "default" and entry["options"]["ignore_result"] is True
    task = celery_app.tasks["app.worker.tasks.check_run_cadences"]
    assert task.queue == "default" and task.soft_time_limit == 600 and task.time_limit == 780


def test_the_new_minutes_collide_with_no_other_sub_daily_entry() -> None:
    """:40 and 7/22/37/52 are free of every hourly and quarter-hourly entry
    (at most four minutes an hour; the */2, */5 and */10 relays run on most
    minutes by design and are not what the spread is about)."""
    from app.worker.celery_app import celery_app

    ours = {"hourly-cross-run-insights", "quarter-hourly-run-cadence-check"}
    taken: set[int] = set()
    for name, entry in celery_app.conf.beat_schedule.items():
        schedule = entry["schedule"]
        if name in ours or not isinstance(schedule, crontab) or len(schedule.hour) < 24:
            continue
        if len(schedule.minute) <= 4:
            taken |= set(schedule.minute)
    assert not taken & {40, 7, 22, 37, 52}