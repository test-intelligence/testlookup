"""TRND-03 — ``run_cadence_service`` on a scripted session (W23a contract §2.2 C-1 … C-6).

The session routes each statement by its compiled SQL (postgresql dialect):
the cadence rows, the scope pre-check, the run reads (filtered by the bound
``created_at`` bounds), the ONE ``ON CONFLICT DO NOTHING … RETURNING`` window
insert and the cursor ``UPDATE``. The ledger producer and the notification
staging call are patched with recorders. Every instant is built inside its test.

The same rules over real Postgres: ``tests/integration/test_run_cadences_postgres.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.postgres import RunCadence, RunCadenceWindow
from app.models.schemas import RunCadenceCreate, RunCadencePatch
from app.services import run_cadence_service as svc
from app.services.run_cadence_schedule import CadenceError
from app.services.run_cadence_vocab import CADENCE_ERRORS

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
BERLIN = "Europe/Berlin"


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows), first=lambda: rows[0] if rows else None)


def _multi_rows(params: dict) -> list[dict]:
    rows: dict[int, dict] = {}
    for key, value in params.items():
        name, _, index = key.rpartition("_m")
        rows.setdefault(int(index), {})[name] = value
    return [rows[i] for i in sorted(rows)]


class _Session:
    """Routes statements by their SQL; journals writes so a test can assert their absence."""

    def __init__(self, *, cadences=(), runs=(), windows=(), users=(), locked=0,
                 insert_returns=None, flush_error=None):
        self.cadences = list(cadences)
        self.runs = sorted(runs)
        self.windows = list(windows)
        self.users = list(users)
        self.locked = locked
        self.insert_returns = insert_returns
        self.flush_error = flush_error
        self.sql: list[str] = []
        self.inserted: list[dict] = []
        self.updates: list[dict] = []
        self.run_reads: list[tuple[datetime, datetime]] = []
        self.deleted: list = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def _matching(self, sql, params):
        rows = [c for c in self.cadences if c.project_id == params.get("project_id_1", c.project_id)]
        if "run_cadences.id = " in sql:
            rows = [c for c in rows if c.id == params["id_1"]]
        if "run_cadences.id != " in sql:
            rows = [c for c in rows if c.id != params["id_1"]]
        if "suite_key IS NULL" in sql:
            rows = [c for c in rows if c.suite_key is None]
        elif "run_cadences.suite_key = " in sql:
            rows = [c for c in rows if c.suite_key == params["suite_key_1"]]
        return rows

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        self.sql.append(sql)
        if sql.startswith("INSERT INTO run_cadence_windows"):
            rows = _multi_rows(params)
            self.inserted.extend(rows)
            if self.insert_returns is not None:
                return _Result(self.insert_returns)
            return _Result([SimpleNamespace(**{k: r[k] for k in ("window_start", "deadline", "outcome")})
                            for r in rows])
        if sql.startswith("UPDATE run_cadences"):
            self.updates.append(params)
            return _Result()
        if "FROM test_runs" in sql:
            lo, hi = params["created_at_1"], params["created_at_2"]
            self.run_reads.append((lo, hi))
            return _Result([t for t in self.runs if lo < t <= hi])
        if "FROM run_cadence_windows" in sql:
            return _Result(self.windows)
        if "count(*)" in sql and "FROM run_cadences" in sql:
            return _Result(scalar=len(self._matching(sql, params)) + self.locked)
        if sql.startswith("SELECT run_cadences.id \nFROM run_cadences"):
            return _Result([(c.id,) for c in self._matching(sql, params)])
        if "FROM run_cadences" in sql:
            return _Result(self._matching(sql, params))
        if "FROM users" in sql:
            return _Result(self.users)
        if "FROM projects" in sql:
            return _Result(scalar="Checkout")
        return _Result()

    def add(self, obj):
        self.cadences.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1
        if self.flush_error is not None:
            raise self.flush_error

    async def refresh(self, obj, *_a, **_kw):
        now = datetime.now(timezone.utc)
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    @property
    def writes(self) -> list[str]:
        return [s for s in self.sql if s.lstrip().startswith(("INSERT", "UPDATE", "DELETE"))]


def _cadence(**kw) -> RunCadence:
    created = datetime(2026, 10, 1, tzinfo=timezone.utc)
    values = dict(
        id=uuid.uuid4(), project_id=PROJECT, suite_name="nightly-regression", suite_key="nightly-regression",
        mode="weekly", weekdays=0b0011111, expected_by=time(6, 0), cron=None, timezone=BERLIN,
        grace_minutes=60, effective_from=created, checked_through=created, paused_at=None,
        paused_until=None, paused_reason=None, paused_by_id=None, created_by_id=None,
        created_at=created, updated_at=created,
    )
    values.update(kw)
    return RunCadence(**values)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD", full_name="Alice Lead", username="alice")


def _local(*args, tz=BERLIN) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


@pytest.fixture
def recorded(monkeypatch):
    """The ledger producer and the notification staging call, as recorders."""
    calls: dict[str, list] = {"activity": [], "staged": []}

    async def _record(db, **kwargs):
        calls["activity"].append(kwargs)

    async def _stage(db, **kwargs):
        calls["staged"].append(kwargs)

    monkeypatch.setattr("app.services.activity.service.record", _record)
    monkeypatch.setattr("app.services.notification.manager.stage_scoped_preference_deliveries", _stage)
    return calls


# ── C-1 validation, create, update ────────────────────────────────────────────


def _create_body(**kw) -> RunCadenceCreate:
    values = dict(suite_name="nightly-regression", weekdays=["mon", "tue", "wed", "thu", "fri"],
                  expected_by="06:00", timezone=BERLIN)
    values.update(kw)
    return RunCadenceCreate(**values)


async def _create_code(session, body) -> CadenceError:
    with pytest.raises(CadenceError) as err:
        await svc.create_cadence(session, PROJECT, body, user=_user(), now=datetime.now(timezone.utc))
    return err.value


@pytest.mark.asyncio
async def test_every_create_refusal_reads_its_sentence():
    cases = {
        "unknown_mode": _create_body(mode="daily"),
        "weekly_fields": _create_body(weekdays=[]),
        "cron_fields": _create_body(mode="cron", cron="0 6 * * *"),
        "bad_cron": _create_body(mode="cron", weekdays=None, expected_by=None, cron="nonsense"),
        "cron_day_fields": _create_body(mode="cron", weekdays=None, expected_by=None, cron="0 6 1 * *"),
        "cron_too_frequent": _create_body(mode="cron", weekdays=None, expected_by=None, cron="*/5 * * * *"),
        "unknown_timezone": _create_body(timezone="Mars/Olympus"),
    }
    for code, body in cases.items():
        err = await _create_code(_Session(), body)
        assert (err.code, err.status_code, err.message) == (code, 422, CADENCE_ERRORS[code])
    err = await _create_code(_Session(), _create_body(weekdays=["mon", "funday"]))
    assert err.code == "weekly_fields"


@pytest.mark.asyncio
async def test_create_normalises_the_suite_and_starts_the_cursor_now():
    session = _Session()
    now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
    user = _user()
    row = await svc.create_cadence(session, PROJECT, _create_body(suite_name="  Nightly "), user=user, now=now)
    assert (row.suite_name, row.suite_key) == ("Nightly", "nightly")
    assert row.effective_from == now and row.checked_through == now
    assert (row.mode, row.weekdays, row.expected_by, row.cron) == ("weekly", 0b0011111, time(6, 0), None)
    assert row.created_by_id == user.id and session.flushes == 1
    err = await _create_code(session, _create_body(suite_name="nightly"))
    assert (err.code, err.status_code, err.message) == ("duplicate_scope", 409, CADENCE_ERRORS["duplicate_scope"])


@pytest.mark.asyncio
async def test_create_any_run_twice_and_the_21st():
    session = _Session(cadences=[_cadence(suite_name=None, suite_key=None)])
    err = await _create_code(session, _create_body(suite_name="   "))
    assert err.code == "duplicate_scope"
    assert err.message == "This project already has an expected cadence for any run."
    full = _Session(cadences=[_cadence(suite_name=f"s{i}", suite_key=f"s{i}") for i in range(20)])
    err = await _create_code(full, _create_body(suite_name="another"))
    assert (err.code, err.status_code, err.message) == ("too_many", 409, CADENCE_ERRORS["too_many"])


@pytest.mark.asyncio
async def test_a_unique_index_race_on_flush_is_the_same_409():
    race = IntegrityError("INSERT", {}, Exception('duplicate key value violates "uq_run_cadences_project_suite"'))
    err = await _create_code(_Session(flush_error=race), _create_body())
    assert err.code == "duplicate_scope" and err.status_code == 409
    other = IntegrityError("INSERT", {}, Exception('violates foreign key "run_cadences_project_id_fkey"'))
    with pytest.raises(IntegrityError):
        await svc.create_cadence(_Session(flush_error=other), PROJECT, _create_body(), user=_user(),
                                 now=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_cron_create_stores_the_normalised_expression():
    row = await svc.create_cadence(
        _Session(), PROJECT,
        _create_body(mode="cron", weekdays=None, expected_by=None, cron="0  6 * *  1-5", suite_name=None),
        user=_user(), now=datetime.now(timezone.utc))
    assert (row.cron, row.weekdays, row.expected_by, row.suite_key) == ("0 6 * * 1-5", None, None, None)


@pytest.mark.asyncio
async def test_empty_patch_writes_nothing():
    row = _cadence()
    session = _Session(cadences=[row])
    later = datetime(2026, 10, 20, tzinfo=timezone.utc)
    got, changed, before, after = await svc.update_cadence(session, PROJECT, row.id, RunCadencePatch(), now=later)
    assert (changed, before, after) == ([], {}, {})
    assert got.effective_from != later and session.flushes == 0 and session.writes == []
    same = RunCadencePatch(suite_name="nightly-regression", expected_by="06:00", timezone=BERLIN)
    _got, changed, _b, _a = await svc.update_cadence(session, PROJECT, row.id, same, now=later)
    assert changed == [] and session.flushes == 0
    assert any("FOR UPDATE" in s for s in session.sql)


@pytest.mark.asyncio
async def test_a_definition_change_restarts_the_expectation_from_now():
    row = _cadence()
    session = _Session(cadences=[row])
    later = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    got, changed, before, after = await svc.update_cadence(
        session, PROJECT, row.id, RunCadencePatch(grace_minutes=30, weekdays=["mon"]), now=later)
    assert changed == ["weekdays", "grace_minutes"]
    assert before == {"weekdays": ["mon", "tue", "wed", "thu", "fri"], "grace_minutes": 60}
    assert after == {"weekdays": ["mon"], "grace_minutes": 30}
    assert got.effective_from == later and got.checked_through == later and session.flushes == 1


@pytest.mark.asyncio
async def test_patch_mode_switch_and_suite_rules():
    row = _cadence()
    session = _Session(cadences=[row, _cadence(suite_name="smoke", suite_key="smoke")])
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    got, changed, _b, _a = await svc.update_cadence(
        session, PROJECT, row.id, RunCadencePatch(mode="cron", cron="30 2 * * *"), now=now)
    assert changed == ["mode", "weekdays", "expected_by", "cron"]
    assert (got.weekdays, got.expected_by, got.cron) == (None, None, "30 2 * * *")
    with pytest.raises(CadenceError) as err:
        await svc.update_cadence(session, PROJECT, row.id, RunCadencePatch(suite_name="x", clear_suite=True), now=now)
    assert (err.value.code, err.value.message) == ("suite_conflict", CADENCE_ERRORS["suite_conflict"])
    with pytest.raises(CadenceError) as err:
        await svc.update_cadence(session, PROJECT, row.id, RunCadencePatch(suite_name=" SMOKE"), now=now)
    assert err.value.code == "duplicate_scope"
    got, changed, _b, _a = await svc.update_cadence(session, PROJECT, row.id, RunCadencePatch(clear_suite=True), now=now)
    assert changed == ["suite_name"] and (got.suite_name, got.suite_key) == (None, None)
    with pytest.raises(CadenceError) as err:
        await svc.update_cadence(session, PROJECT, row.id, RunCadencePatch(mode="weekly"), now=now)
    assert err.value.code == "weekly_fields"   # cron -> weekly keeps no stale cron fields to fall back on


@pytest.mark.asyncio
async def test_another_projects_cadence_is_the_unknown_id_404():
    foreign = _cadence(project_id=uuid.uuid4())
    session = _Session(cadences=[foreign])
    now = datetime.now(timezone.utc)
    for call in (
        svc.get_cadence(session, PROJECT, foreign.id),
        svc.update_cadence(session, PROJECT, foreign.id, RunCadencePatch(grace_minutes=5), now=now),
        svc.delete_cadence(session, PROJECT, foreign.id),
        svc.pause_cadence(session, PROJECT, foreign.id, until=now + timedelta(days=1), reason="x",
                          user=_user(), now=now),
        svc.resume_cadence(session, PROJECT, foreign.id, now=now),
    ):
        with pytest.raises(CadenceError) as err:
            await call
        assert (err.value.code, err.value.status_code, err.value.message) == (
            "cadence_not_found", 404, CADENCE_ERRORS["cadence_not_found"])
    assert session.deleted == [] and session.flushes == 0


# ── C-5 pause / resume ────────────────────────────────────────────────────────


async def _pause_code(session, row, *, until, reason, now) -> str:
    with pytest.raises(CadenceError) as err:
        await svc.pause_cadence(session, PROJECT, row.id, until=until, reason=reason, user=_user(), now=now)
    assert err.value.message == CADENCE_ERRORS[err.value.code] and err.value.status_code == 422
    return err.value.code


@pytest.mark.asyncio
async def test_pause_bounds_and_reason():
    row = _cadence()
    session = _Session(cadences=[row])
    now = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    assert await _pause_code(session, row, until=now, reason="x", now=now) == "pause_until_past"
    too_long = now + timedelta(days=90, seconds=1)
    assert await _pause_code(session, row, until=too_long, reason="x", now=now) == "pause_too_long"
    assert await _pause_code(session, row, until=now + timedelta(days=1), reason="   ", now=now) == \
        "pause_reason_required"
    user = _user()
    got = await svc.pause_cadence(session, PROJECT, row.id, until=now + timedelta(days=90),
                                  reason="  Release freeze sentinel ", user=user, now=now)
    assert (got.paused_at, got.paused_until) == (now, now + timedelta(days=90))
    assert (got.paused_reason, got.paused_by_id) == ("Release freeze sentinel", user.id)
    replaced = await svc.pause_cadence(session, PROJECT, row.id, until=now + timedelta(days=2),
                                       reason="shorter", user=user, now=now + timedelta(hours=1))
    assert replaced.paused_until == now + timedelta(days=2) and replaced.paused_reason == "shorter"


@pytest.mark.asyncio
async def test_resume_clears_a_current_pause_and_refuses_otherwise():
    now = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    active = _cadence()
    expired = _cadence(suite_name="b", suite_key="b", paused_at=now - timedelta(days=3),
                       paused_until=now - timedelta(seconds=1), paused_reason="old")
    paused = _cadence(suite_name="c", suite_key="c", paused_at=now - timedelta(days=1),
                      paused_until=now + timedelta(days=1), paused_reason="freeze", paused_by_id=uuid.uuid4())
    session = _Session(cadences=[active, expired, paused])
    for row in (active, expired):
        with pytest.raises(CadenceError) as err:
            await svc.resume_cadence(session, PROJECT, row.id, now=now)
        assert (err.value.code, err.value.status_code, err.value.message) == (
            "not_paused", 409, CADENCE_ERRORS["not_paused"])
    got = await svc.resume_cadence(session, PROJECT, paused.id, now=now)
    assert (got.paused_at, got.paused_until, got.paused_reason, got.paused_by_id) == (None, None, None, None)


def test_is_paused_at_boundaries():
    start = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    row = _cadence(paused_at=start, paused_until=end, paused_reason="x")
    assert svc.is_paused_at(row, start) is True                         # paused_at inclusive
    assert svc.is_paused_at(row, start - timedelta(seconds=1)) is False
    assert svc.is_paused_at(row, end - timedelta(seconds=1)) is True
    assert svc.is_paused_at(row, end) is False                          # paused_until exclusive
    assert svc.is_paused_at(_cadence(), start) is False
    assert svc.is_paused_now(row, end - timedelta(seconds=1)) and not svc.is_paused_now(row, end)


# ── C-4 the check ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_decides_met_missed_paused_and_alerts_once(recorded):
    from app.models.postgres import NotificationEventType

    row = _cadence(checked_through=_local(2026, 10, 19, 0, 0),
                   paused_at=_local(2026, 10, 20, 12, 0), paused_until=_local(2026, 10, 21, 23, 0),
                   paused_reason="Release freeze sentinel")
    runs = [_local(2026, 10, 19, 5, 30), _local(2026, 10, 21, 6, 30), _local(2026, 10, 23, 6, 30)]
    session = _Session(cadences=[row], runs=runs)
    now = _local(2026, 10, 23, 12, 0)
    summary = await svc.check_project_cadences(session, PROJECT, now=now)
    assert summary == {"cadences": 1, "decided": 5, "missed": 2, "alerts": 1, "skipped_locked": 0}
    assert [r["outcome"] for r in session.inserted] == ["met", "missed", "paused", "missed", "met"]
    assert [r["run_count"] for r in session.inserted] == [1, 0, 1, 0, 1]
    assert session.inserted[4]["first_run_at"] == _local(2026, 10, 23, 6, 30)   # 30 min late, inside the grace
    assert session.run_reads == [(_local(2026, 10, 16, 7, 0), _local(2026, 10, 23, 7, 0))]
    assert session.updates[0]["checked_through"] == _local(2026, 10, 23, 7, 0)
    assert any("FOR UPDATE SKIP LOCKED" in s for s in session.sql)
    insert_sql = next(s for s in session.sql if s.startswith("INSERT INTO run_cadence_windows"))
    assert "ON CONFLICT (cadence_id, window_start) DO NOTHING RETURNING" in insert_sql
    [activity] = recorded["activity"]
    assert activity["event_type"] == "run_cadence.missed" and activity["entity_type"] == "run_cadence"
    assert (activity["entity_id"], activity["entity_label"]) == (str(row.id), "nightly-regression")
    assert activity["context"] == {"count": 2, "days": "Tue 20 Oct, Thu 22 Oct", "suite": "nightly-regression"}
    assert activity["actor"].actor_type == "system"
    [staged] = recorded["staged"]
    assert staged["events"] == [NotificationEventType.RUN_MISSED] and staged["run_id"] is None
    assert staged["delivery_scope"] == f"run-missed:{row.id}:{_local(2026, 10, 19, 7, 0).isoformat()}"
    assert staged["metadata"]["missed"] == [_local(2026, 10, 20, 6, 0).isoformat(),
                                            _local(2026, 10, 22, 6, 0).isoformat()]
    title, body = staged["build_title_fn"](NotificationEventType.RUN_MISSED)
    assert title == "2 expected runs missed — Checkout · nightly-regression"
    assert body.splitlines()[:2] == [
        "No run of nightly-regression arrived by 06:00 Europe/Berlin on Tue 20 Oct (grace 60 min).",
        "No run of nightly-regression arrived by 06:00 Europe/Berlin on Thu 22 Oct (grace 60 min).",
    ]
    assert "Paused days are not counted." in body and staged["metadata"]["dashboard_url"].endswith("/trends")


@pytest.mark.asyncio
async def test_check_decides_only_windows_ending_after_the_cursor_up_to_now(recorded):
    row = _cadence(checked_through=_local(2026, 10, 20, 7, 0))          # exactly Tue's window end
    session = _Session(cadences=[row])
    summary = await svc.check_project_cadences(session, PROJECT, now=_local(2026, 10, 22, 7, 0))  # Thu's end
    assert [r["deadline"] for r in session.inserted] == [_local(2026, 10, 21, 6, 0), _local(2026, 10, 22, 6, 0)]
    assert summary["decided"] == 2 and summary["missed"] == 2 and summary["alerts"] == 1


@pytest.mark.asyncio
async def test_check_never_decides_a_window_older_than_the_lookback(recorded):
    now = _local(2026, 10, 28, 12, 0)
    row = _cadence(checked_through=now - timedelta(days=10))
    session = _Session(cadences=[row])
    await svc.check_project_cadences(session, PROJECT, now=now)
    ends = [r["window_end"] for r in session.inserted]
    assert min(ends) > now - timedelta(days=7) and max(ends) <= now
    assert [r["deadline"] for r in session.inserted][0] == _local(2026, 10, 22, 6, 0)
    assert len(ends) == 5                                               # Thu, Fri, Mon, Tue, Wed


@pytest.mark.asyncio
async def test_check_caps_the_windows_per_tick(monkeypatch, recorded):
    monkeypatch.setattr(svc, "CHECK_MAX_WINDOWS_PER_TICK", 3)          # 7 d x 24 / day never reaches 200
    row = _cadence(checked_through=_local(2026, 10, 19, 0, 0))
    session = _Session(cadences=[row])
    await svc.check_project_cadences(session, PROJECT, now=_local(2026, 10, 23, 12, 0))
    assert len(session.inserted) == 3
    assert session.updates[0]["checked_through"] == _local(2026, 10, 21, 7, 0)  # the rest next tick


@pytest.mark.asyncio
async def test_a_redelivered_tick_stages_nothing(recorded):
    row = _cadence(checked_through=_local(2026, 10, 19, 0, 0))
    session = _Session(cadences=[row], insert_returns=[])
    summary = await svc.check_project_cadences(session, PROJECT, now=_local(2026, 10, 23, 12, 0))
    assert summary["decided"] == 0 and summary["missed"] == 0 and summary["alerts"] == 0
    assert recorded["activity"] == [] and recorded["staged"] == []


@pytest.mark.asyncio
async def test_check_counts_locked_cadences_and_skips_quiet_ones(recorded):
    now = _local(2026, 10, 23, 12, 0)
    quiet = _cadence(checked_through=now)
    session = _Session(cadences=[quiet], locked=1)
    summary = await svc.check_project_cadences(session, PROJECT, now=now)
    assert summary == {"cadences": 1, "decided": 0, "missed": 0, "alerts": 0, "skipped_locked": 1}
    assert session.writes == [] and session.run_reads == []


# ── C-6 the status (read-only) ────────────────────────────────────────────────


def _window(row, *, deadline, start, end, outcome, run_count=0) -> RunCadenceWindow:
    return RunCadenceWindow(id=uuid.uuid4(), cadence_id=row.id, project_id=row.project_id, window_start=start,
                            window_end=end, deadline=deadline, outcome=outcome, run_count=run_count)


def _assert_read_only(session):
    assert session.writes == [] and session.commits == 0 and session.flushes == 0
    assert all(s.lstrip().startswith("SELECT") for s in session.sql), session.sql


@pytest.mark.asyncio
async def test_status_without_a_cadence_is_not_configured():
    session = _Session()
    body = await svc.cadence_status(session, PROJECT, days=14, now=datetime.now(timezone.utc))
    assert (body["configured"], body["items"], body["total"], body["expected"]) == (False, [], 0, 0)
    _assert_read_only(session)


@pytest.mark.asyncio
async def test_status_mixes_stored_and_computed_windows_and_writes_nothing():
    author = uuid.uuid4()
    row = _cadence(effective_from=_local(2026, 10, 12, 0, 0), created_by_id=author)
    stored = [
        # a run DID arrive in Monday's window, but the beat decided "missed": the stored row wins
        _window(row, deadline=_local(2026, 10, 19, 6, 0), start=_local(2026, 10, 16, 7, 0),
                end=_local(2026, 10, 19, 7, 0), outcome="missed"),
        # decided while paused; the cadence has been resumed since: it stays paused
        _window(row, deadline=_local(2026, 10, 21, 6, 0), start=_local(2026, 10, 20, 7, 0),
                end=_local(2026, 10, 21, 7, 0), outcome="paused"),
    ]
    runs = [_local(2026, 10, 19, 5, 30), _local(2026, 10, 20, 5, 50)]
    session = _Session(cadences=[row], runs=runs, windows=stored, users=[(author, "Alice Lead", "alice")])
    now = _local(2026, 10, 23, 6, 30)                                    # Friday, inside the grace
    body = await svc.cadence_status(session, PROJECT, days=7, now=now)
    _assert_read_only(session)
    assert (body["configured"], body["total"], body["days"]) == (True, 1, 7)
    assert body["window_start"] == now - timedelta(days=7) and body["window_end"] == now
    [item] = body["items"]
    assert (item["met"], item["missed"], item["paused"], item["pending"], item["expected"]) == (1, 2, 1, 1, 3)
    assert item["measured"] is True and item["reason"] is None
    assert [(w["local_label"], w["recorded"]) for w in item["missed_windows"]] == [
        ("Thu 22 Oct", False), ("Mon 19 Oct", True)]
    assert item["last_run_at"] == _local(2026, 10, 20, 5, 50)
    assert item["cadence"]["created_by_name"] == "Alice Lead"
    assert item["cadence"]["summary"] == "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min"
    assert (body["expected"], body["met"], body["missed"], body["paused"]) == (3, 1, 2, 1)


@pytest.mark.asyncio
async def test_status_caps_missed_windows_but_counts_every_miss():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    row = _cadence(mode="cron", weekdays=None, expected_by=None, cron="0 * * * *", grace_minutes=0,
                   effective_from=now, timezone="UTC")
    stored = [
        _window(row, deadline=now - timedelta(hours=k), start=now - timedelta(hours=k + 1),
                end=now - timedelta(hours=k), outcome="missed")
        for k in range(1, 61)
    ]
    session = _Session(cadences=[row], windows=stored)
    body = await svc.cadence_status(session, PROJECT, days=7, now=now)
    [item] = body["items"]
    assert item["missed"] == 60 and len(item["missed_windows"]) == 50
    assert item["missed_windows"][0]["deadline"] == now - timedelta(hours=1)   # newest first
    _assert_read_only(session)


@pytest.mark.asyncio
async def test_status_never_calls_an_empty_window_met():
    now = _local(2026, 10, 25, 12, 0)                                    # a Sunday
    row = _cadence(effective_from=now - timedelta(hours=1))
    session = _Session(cadences=[row])
    body = await svc.cadence_status(session, PROJECT, days=1, now=now)
    [item] = body["items"]
    assert item["measured"] is False and item["reason"] == "No expected run fell in this window yet."
    assert (item["expected"], item["met"], item["missed"]) == (0, 0, 0)
    assert item["cadence"]["next_deadline"] == _local(2026, 10, 26, 6, 0)


@pytest.mark.asyncio
async def test_status_does_not_judge_deadlines_before_the_current_definition():
    row = _cadence(effective_from=_local(2026, 10, 22, 12, 0))          # edited Thursday noon
    session = _Session(cadences=[row])
    body = await svc.cadence_status(session, PROJECT, days=7, now=_local(2026, 10, 23, 12, 0))
    [item] = body["items"]
    assert (item["missed"], item["expected"]) == (1, 1)                 # Friday only; Mon-Thu were never expected
