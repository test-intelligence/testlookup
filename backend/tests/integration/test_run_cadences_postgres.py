"""W23a TRND-03 — expected run cadences against a real PostgreSQL (contract §2.2, §6.3 G-C).

What a statement-routing fake cannot show:

1. **The run-level suite rule.** A Mon–Fri 06:00 Europe/Berlin cadence for
   ``nightly-regression``: Mon a ``live_stream`` run labelled with the suite and
   NO rows, Tue a run whose ROWS carry the suite under another label, Wed none,
   Thu only a run of another suite, Fri a run 30 min after 06:00 (inside the
   grace) → Mon / Tue / Fri ``met``, Wed / Thu ``missed``.
2. **Decided once, alerted once.** ``check_project_cadences`` at fixed instants:
   one ``ON CONFLICT DO NOTHING`` insert per cadence; exactly ONE pending
   ``notification_logs`` row (``run.missed`` / ``email``) per check that found a
   NEW miss, for the subscriber only; the same instant again adds nothing; a
   second ``(cadence_id, window_start)`` row raises ``IntegrityError``.
3. **Pause / resume / delete.** A pause decides ``paused`` (no alert); a resume
   makes the next Monday expected again while the decided ``paused`` rows stay;
   deleting the cadence removes its windows.
4. **DST.** A daily 02:30 cadence has exactly ONE window on 2026-10-25 (the
   fall-back day), ending ``01:30Z + grace``.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and
after disposing it. It binds NO Redis (no flag read; the activity live publish
is off for the session) and no Mongo, so it calls neither ``close_redis()`` nor
``close_mongo()``. Every instant is built inside its test; every row lives
under a fresh project removed at the end. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` (= ``DATABASE_URL``) at head (0244). To be
listed in the ``postgres-integration`` job of ``.github/workflows/ci.yml``
(contract §6.4).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

SUITE = "nightly-regression"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _berlin(*args) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _world(sessions) -> SimpleNamespace:
    """A project, a QA lead, a member subscribed to run.missed by e-mail, a member who is not."""
    from app.models.postgres import NotificationPreference, Project, ProjectMember, User

    pid = uuid.uuid4()
    tag = pid.hex[:8]
    ids = {k: uuid.uuid4() for k in ("lead", "subscriber", "other")}
    async with sessions() as db:
        db.add(Project(id=pid, name=f"gc-cad-{tag}", slug=f"gc-cad-{tag}", is_active=True))
        for key, uid in ids.items():
            db.add(User(id=uid, email=f"gc-cad-{key}-{tag}@example.com", username=f"gc_cad_{key}_{tag}",
                        hashed_password="!unusable", role="QA_LEAD" if key == "lead" else "QA_ENGINEER",
                        full_name=f"Cadence {key}", is_active=True))
        await db.flush()
        for uid in ids.values():
            db.add(ProjectMember(project_id=pid, user_id=uid, role="QA_ENGINEER"))
        db.add(NotificationPreference(user_id=ids["subscriber"], project_id=pid, channel="email",
                                      enabled=True, events=["run.missed"]))
        db.add(NotificationPreference(user_id=ids["other"], project_id=pid, channel="email",
                                      enabled=True, events=["run.failed", "quality_gate.failed"]))
        await db.commit()
    return SimpleNamespace(pid=pid, tag=tag, lead=SimpleNamespace(id=ids["lead"]),
                           subscriber=ids["subscriber"], other=ids["other"])


async def _drop(sessions, w) -> None:
    async with sessions() as db:
        await db.execute(text("DELETE FROM notification_logs WHERE project_id = :p"), {"p": w.pid})
        await db.execute(text("DELETE FROM project_activity_events WHERE project_id = :p"), {"p": w.pid})
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": w.pid})
        await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": [w.lead.id, w.subscriber, w.other]})
        await db.commit()


async def _week_of_runs(sessions, w) -> None:
    """Mon a label-only live_stream run; Tue rows under another label; Wed none; Thu another suite; Fri late."""
    from app.models.postgres import TestCase, TestRun

    def run(i, at, label, source="upload"):
        r = TestRun(id=uuid.uuid4(), project_id=w.pid, build_number=f"gc-cad-{w.tag}-{i}", jenkins_job="gc",
                    status="PASSED", ingestion_source=source, branch="main", primary_suite_name=label)
        r.created_at = at
        return r

    async with sessions() as db:
        mon = run(1, _berlin(2026, 10, 19, 5, 0), SUITE, source="live_stream")
        tue = run(2, _berlin(2026, 10, 20, 5, 0), "legacy-label")
        thu = run(4, _berlin(2026, 10, 22, 5, 0), "smoke")
        fri = run(5, _berlin(2026, 10, 23, 6, 30), SUITE)
        for r in (mon, tue, thu, fri):
            db.add(r)
        await db.flush()
        db.add(TestCase(id=uuid.uuid4(), test_run_id=tue.id, test_name="checkout_sentinel_test",
                        test_fingerprint=f"fp-{w.tag}-tue", status="PASSED", suite_name=SUITE))
        db.add(TestCase(id=uuid.uuid4(), test_run_id=thu.id, test_name="smoke_test",
                        test_fingerprint=f"fp-{w.tag}-thu", status="PASSED", suite_name="smoke"))
        await db.commit()


async def _create(sessions, w, *, now, **body):
    from app.models.schemas import RunCadenceCreate
    from app.services import run_cadence_service as svc

    values = {"suite_name": SUITE, "weekdays": ["mon", "tue", "wed", "thu", "fri"], "expected_by": "06:00",
              "timezone": "Europe/Berlin", "grace_minutes": 60, **body}
    async with sessions() as db:
        row = await svc.create_cadence(db, w.pid, RunCadenceCreate(**values), user=w.lead, now=now)
        await db.commit()
    return row.id


async def _check(sessions, w, now) -> dict:
    from app.services import run_cadence_service as svc

    async with sessions() as db:
        summary = await svc.check_project_cadences(db, w.pid, now=now)
        await db.commit()
    return summary


async def _windows(sessions, cadence_id) -> list[tuple[str, str]]:
    from app.services.run_cadence_schedule import local_label

    async with sessions() as db:
        rows = (await db.execute(text(
            "SELECT deadline, outcome FROM run_cadence_windows WHERE cadence_id = :c ORDER BY deadline"),
            {"c": cadence_id})).all()
    return [(local_label(r[0], ZoneInfo("Europe/Berlin")), r[1]) for r in rows]


async def _logs(sessions, w) -> list[tuple]:
    async with sessions() as db:
        return [tuple(r) for r in (await db.execute(text(
            "SELECT user_id, event_type, channel, status FROM notification_logs WHERE project_id = :p"),
            {"p": w.pid})).all()]


async def test_the_week_is_decided_once_alerted_once_and_reported(env):
    from app.models.postgres import RunCadenceWindow
    from app.services import run_cadence_service as svc

    sessions = env
    w = await _world(sessions)
    try:
        await _week_of_runs(sessions, w)
        cadence_id = await _create(sessions, w, now=_berlin(2026, 10, 19, 1, 0))

        first = await _check(sessions, w, _berlin(2026, 10, 21, 8, 0))       # Wed after the grace
        assert first == {"cadences": 1, "decided": 3, "missed": 1, "alerts": 1, "skipped_locked": 0}
        assert await _windows(sessions, cadence_id) == [
            ("Mon 19 Oct", "met"), ("Tue 20 Oct", "met"), ("Wed 21 Oct", "missed")]
        pending = ("run.missed", "email", "pending")
        assert await _logs(sessions, w) == [(w.subscriber, *pending)], "the subscriber only"

        again = await _check(sessions, w, _berlin(2026, 10, 21, 8, 0))       # a redelivered tick
        assert again["decided"] == 0 and again["alerts"] == 0
        assert len(await _windows(sessions, cadence_id)) == 3 and len(await _logs(sessions, w)) == 1

        second = await _check(sessions, w, _berlin(2026, 10, 23, 8, 0))      # Fri after the grace
        assert (second["decided"], second["missed"], second["alerts"]) == (2, 1, 1)
        assert await _windows(sessions, cadence_id) == [
            ("Mon 19 Oct", "met"), ("Tue 20 Oct", "met"), ("Wed 21 Oct", "missed"),
            ("Thu 22 Oct", "missed"), ("Fri 23 Oct", "met")]
        assert await _logs(sessions, w) == [(w.subscriber, *pending)] * 2

        async with sessions() as db:
            summaries = [r[0] for r in (await db.execute(text(
                "SELECT summary FROM project_activity_events WHERE project_id = :p "
                "AND event_type = 'run_cadence.missed' ORDER BY summary"), {"p": w.pid})).all()]
        assert summaries == [f"1 expected run(s) of {SUITE} missed (Thu 22 Oct)",
                             f"1 expected run(s) of {SUITE} missed (Wed 21 Oct)"]

        async with sessions() as db:
            status = await svc.cadence_status(db, w.pid, days=7, now=_berlin(2026, 10, 23, 8, 0))
            assert not db.new and not db.dirty, "a status read stages nothing"
        assert (status["configured"], status["expected"], status["met"], status["missed"]) == (True, 5, 3, 2)
        [item] = status["items"]
        assert [m["local_label"] for m in item["missed_windows"]] == ["Thu 22 Oct", "Wed 21 Oct"]
        assert all(m["recorded"] for m in item["missed_windows"])
        assert item["last_run_at"] == _berlin(2026, 10, 23, 6, 30)

        async with sessions() as db:
            mon = (await db.execute(text(
                "SELECT window_start FROM run_cadence_windows WHERE cadence_id = :c ORDER BY deadline LIMIT 1"),
                {"c": cadence_id})).scalar()
            db.add(RunCadenceWindow(id=uuid.uuid4(), cadence_id=cadence_id, project_id=w.pid, window_start=mon,
                                    window_end=mon + timedelta(hours=1), deadline=mon, outcome="met"))
            with pytest.raises(IntegrityError):
                await db.flush()
    finally:
        await _drop(sessions, w)


async def test_a_pause_decides_paused_a_resume_expects_monday_and_delete_takes_the_windows(env):
    from app.services import run_cadence_service as svc

    sessions = env
    w = await _world(sessions)
    try:
        await _week_of_runs(sessions, w)
        cadence_id = await _create(sessions, w, now=_berlin(2026, 10, 19, 1, 0))
        await _check(sessions, w, _berlin(2026, 10, 21, 8, 0))               # Mon, Tue met; Wed missed
        async with sessions() as db:
            await svc.pause_cadence(db, w.pid, cadence_id, until=_berlin(2026, 10, 23, 23, 59),
                                    reason="Release freeze sentinel", user=w.lead, now=_berlin(2026, 10, 21, 8, 0))
            await db.commit()
        paused_tick = await _check(sessions, w, _berlin(2026, 10, 23, 8, 0))
        assert (paused_tick["decided"], paused_tick["missed"], paused_tick["alerts"]) == (2, 0, 0)
        assert (await _windows(sessions, cadence_id))[3:] == [("Thu 22 Oct", "paused"), ("Fri 23 Oct", "paused")]
        assert len(await _logs(sessions, w)) == 1, "the Wednesday alert only"

        async with sessions() as db:
            await svc.resume_cadence(db, w.pid, cadence_id, now=_berlin(2026, 10, 23, 9, 0))
            await db.commit()
        monday = await _check(sessions, w, _berlin(2026, 10, 26, 8, 0))
        assert (monday["decided"], monday["missed"], monday["alerts"]) == (1, 1, 1)
        assert (await _windows(sessions, cadence_id))[3:] == [
            ("Thu 22 Oct", "paused"), ("Fri 23 Oct", "paused"), ("Mon 26 Oct", "missed")]

        async with sessions() as db:
            status = await svc.cadence_status(db, w.pid, days=7, now=_berlin(2026, 10, 26, 8, 0))
        [item] = status["items"]
        assert (item["paused"], item["missed"], item["cadence"]["paused"]) == (2, 2, False)   # Wed + Mon 26

        async with sessions() as db:
            await svc.delete_cadence(db, w.pid, cadence_id)
            await db.commit()
        assert await _windows(sessions, cadence_id) == []
    finally:
        await _drop(sessions, w)


async def test_a_daily_0230_cadence_has_one_window_on_the_fall_back_day(env):
    sessions = env
    w = await _world(sessions)
    try:
        cadence_id = await _create(sessions, w, now=_berlin(2026, 10, 24, 12, 0), suite_name="dst-daily",
                                   weekdays=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                   expected_by="02:30", grace_minutes=30)
        await _check(sessions, w, _berlin(2026, 10, 25, 12, 0))
        async with sessions() as db:
            rows = (await db.execute(text(
                "SELECT deadline, window_start, window_end FROM run_cadence_windows WHERE cadence_id = :c"),
                {"c": cadence_id})).all()
        assert len(rows) == 1
        deadline, start, end = rows[0]
        fall_back = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)      # 02:30 CET, the SECOND 02:30
        assert deadline == fall_back and end == fall_back + timedelta(minutes=30)
        assert end - start == timedelta(hours=25)
    finally:
        await _drop(sessions, w)


async def test_another_project_sees_no_cadence(env):
    from app.services import run_cadence_service as svc

    sessions = env
    w = await _world(sessions)
    other = await _world(sessions)
    try:
        await _create(sessions, w, now=_berlin(2026, 10, 19, 1, 0))
        async with sessions() as db:
            status = await svc.cadence_status(db, other.pid, days=14, now=_berlin(2026, 10, 23, 8, 0))
            listed = await svc.list_cadences(db, other.pid)
        assert status["configured"] is False and listed == []
    finally:
        await _drop(sessions, w)
        await _drop(sessions, other)
