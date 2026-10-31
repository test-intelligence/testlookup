"""W25a HYG-04 — the parity harness against a real PostgreSQL (contract §2.1, §6.3 G-A).

TWO projects of this module's own (non-hex sentinel names) are seeded with
suites (incl. a default suite), canonical rows, legacy rows (incl. a
``-deleted`` bucket row), two runs + cases for the window and one ``modified``
event; the REAL ``run_parity`` runs on ``create_async_engine(dsn, pool_size=1,
max_overflow=1, pool_timeout=5)`` — the production worker pool (lesson 3).
Proven here:

1. the run row: ``diffs``, the trigger / requester / window, zero-filled
   counts, OUR projects' ``per_project`` lines (never instance-wide totals:
   CI runs every ``*_postgres.py`` module in ONE database, and other projects
   are other modules'); one connection at the deepest point; the only table it
   writes is ``suite_membership_parity_runs`` (the catalog is never written);
   the metric counted once;
2. ``compare_project`` on Postgres gives each project's exact reasons;
3. a second claim while a fresh ``running`` row exists → ``already_running``,
   no row, no metric; a 61-minute-old ``running`` row → ``failed`` /
   ``interrupted`` by the next claim;
4. a failure in the comparison → ``failed`` + ``error_code`` (the exception
   TYPE) recorded, the task's dict returned, nothing raised.

Teardown deletes EVERYTHING this module wrote: every parity-run row it made,
its projects and every child row (canonical cases first — RESTRICT on suites —
then the projects, which cascade), its users. ``dispose_engine_for_loop()``
before the engine is built and after it is disposed. Redis and Mongo are never
bound. Run through ``pg_pytest_w25a.sh a …`` (``df_w25a_a`` at 0248).
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WRITE = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?([a-z_]+)\"?", re.IGNORECASE)
_NON_HEX = "ghijkmnpqrstuvwxyz"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _tag() -> str:
    return "".join(secrets.choice(_NON_HEX) for _ in range(8))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        return lambda event_name, **kw: self.calls.append((level, event_name, kw))

    def named(self, event_name: str) -> list[dict[str, Any]]:
        return [kw for _level, name, kw in self.calls if name == event_name]


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    """The production worker pool: size 1 + overflow 1, 5 s to wait. Statements and checkouts are recorded."""
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=1, max_overflow=1, pool_timeout=5)
    probe = SimpleNamespace(out=0, peak=0, statements=[])

    @event.listens_for(engine.sync_engine, "checkout")
    def _checkout(*_: Any) -> None:
        probe.out += 1
        probe.peak = max(probe.peak, probe.out)

    @event.listens_for(engine.sync_engine, "checkin")
    def _checkin(*_: Any) -> None:
        probe.out -= 1

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        probe.statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sessions.probe = probe  # type: ignore[attr-defined]
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _remove(env: Any, w: SimpleNamespace) -> None:
    """Everything this module wrote, in FK order; raises on a leak (a leak must fail THIS module)."""
    from app.models import postgres as m

    projects = [w.p1, w.p2]
    async with env() as db:
        await db.execute(delete(m.SuiteMembershipParityRun).where(
            (m.SuiteMembershipParityRun.id.in_(w.parity_ids or [uuid.uuid4()]))
            | (m.SuiteMembershipParityRun.requested_by_id == w.admin)))
        await db.execute(delete(m.CanonicalTestCase).where(m.CanonicalTestCase.project_id.in_(projects)))
        await db.execute(delete(m.Project).where(m.Project.id.in_(projects)))     # suites, memberships, runs cascade
        await db.execute(delete(m.User).where(m.User.id == w.admin))
        await db.commit()
    async with env() as db:
        left = 0
        for model in (m.SuiteMembership, m.SuiteMembershipEvent, m.TestSuite, m.TestRun, m.CanonicalTestCase):
            left += int((await db.execute(
                select(func.count()).select_from(model).where(model.project_id.in_(projects)))).scalar_one())
        left += int((await db.execute(select(func.count()).select_from(m.SuiteMembershipParityRun).where(
            m.SuiteMembershipParityRun.id.in_(w.parity_ids or [uuid.uuid4()])))).scalar_one())
    assert left == 0, f"{left} rows of this module survived its teardown"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def world(env: Any) -> Any:
    """Project ONE: every reason this seed can show; project TWO: one matching key (clean)."""
    from app.models import postgres as m

    tag = _tag()
    w = SimpleNamespace(tag=tag, admin=uuid.uuid4(), p1=uuid.uuid4(), p2=uuid.uuid4(), parity_ids=set())
    fp = {name: f"pq{name}{tag}" for name in ("match", "bucket", "missing", "moved", "default", "two")}
    w.fp = fp
    w.default_suite = f"Default Suite (Parity {tag} One)"
    try:
        async with env() as db:
            db.add(m.User(id=w.admin, username=f"parity-{tag}", email=f"parity-{tag}@parity.example.test",
                          full_name="Parity Admin", hashed_password="!" + secrets.token_urlsafe(16), role="ADMIN",
                          is_active=True))
            db.add_all([
                m.Project(id=w.p1, name=f"Parity {tag} One", slug=f"parity-{tag}-one", is_active=True),
                m.Project(id=w.p2, name=f"Parity {tag} Two", slug=f"parity-{tag}-two", is_active=False),  # archived
            ])
            await db.flush()
            smoke, regression = m.TestSuite(project_id=w.p1, name="Smoke"), m.TestSuite(project_id=w.p1, name="Regression")
            default = m.TestSuite(project_id=w.p1, name=w.default_suite, is_default=True)
            smoke2 = m.TestSuite(project_id=w.p2, name="Smoke")
            db.add_all([smoke, regression, default, smoke2])
            now = datetime.now(timezone.utc)
            r1 = m.TestRun(project_id=w.p1, build_number=f"parity-{tag}-1", status="PASSED",
                           created_at=now - timedelta(hours=2))
            r2 = m.TestRun(project_id=w.p1, build_number=f"parity-{tag}-2", status="PASSED",
                           created_at=now - timedelta(hours=1))
            r3 = m.TestRun(project_id=w.p2, build_number=f"parity-{tag}-3", status="PASSED", created_at=now)
            db.add_all([r1, r2, r3])
            await db.flush()
            for key in ("match", "bucket", "moved", "default"):
                db.add(m.TestCase(test_run_id=r2.id, test_name=f"test_{key}", status="PASSED", test_fingerprint=fp[key]))
            db.add(m.TestCase(test_run_id=r3.id, test_name="test_two", status="PASSED", test_fingerprint=fp["two"]))
            legacy = [
                ("Smoke", "match", "active", r1.id, r2.id), ("Smoke-deleted", "bucket", "needs_review", r1.id, r1.id),
                ("Smoke", "missing", "active", r1.id, r2.id), ("Smoke", "moved", "active", r1.id, r2.id),
            ]
            for suite, key, status, first, last in legacy:
                db.add(m.SuiteMembership(project_id=w.p1, suite_name=suite, test_fingerprint=fp[key],
                                         test_name=f"test_{key}", status=status, source="execution",
                                         first_seen_run_id=first, last_seen_run_id=last))
            db.add(m.SuiteMembership(project_id=w.p2, suite_name="Smoke", test_fingerprint=fp["two"],
                                     test_name="test_two", status="active", first_seen_run_id=r3.id,
                                     last_seen_run_id=r3.id))
            for suite, key in ((smoke, "match"), (smoke, "bucket"), (default, "default"), (regression, "moved")):
                db.add(m.CanonicalTestCase(project_id=w.p1, test_suite_id=suite.id, test_fingerprint=fp[key],
                                           test_name=f"test_{key}", status="active", source="execution",
                                           first_seen_run_id=r1.id, last_seen_run_id=r2.id))
            db.add(m.CanonicalTestCase(project_id=w.p2, test_suite_id=smoke2.id, test_fingerprint=fp["two"],
                                       test_name="test_two", status="active", source="execution",
                                       first_seen_run_id=r3.id, last_seen_run_id=r3.id))
            db.add(m.SuiteMembershipEvent(project_id=w.p1, suite_name="Smoke", test_fingerprint=fp["match"],
                                          test_name="test_match", event_type="modified", run_id=r2.id))
            await db.commit()
        yield w
    finally:
        await _remove(env, w)


async def _row(env: Any, run_id: Any) -> Any:
    from app.models.postgres import SuiteMembershipParityRun

    async with env() as db:
        return await db.get(SuiteMembershipParityRun, uuid.UUID(str(run_id)))


async def _parity_count(env: Any) -> int:
    from app.models.postgres import SuiteMembershipParityRun

    async with env() as db:
        return int((await db.execute(select(func.count()).select_from(SuiteMembershipParityRun))).scalar_one())


def _metric(status: str) -> float:
    from app.core.metrics import suite_parity_runs_total

    return float(suite_parity_runs_total.labels(status=status)._value.get())


async def test_compare_project_on_postgres_gives_each_projects_exact_reasons(env: Any, world: Any) -> None:
    from app.core.config import settings
    from app.services import suite_membership_parity as parity

    async with env() as db:
        one = await parity.compare_project(db, world.p1, window_runs=settings.CANONICAL_DELETION_WINDOW_RUNS)
        await db.rollback()
        two = await parity.compare_project(db, world.p2, window_runs=settings.CANONICAL_DELETION_WINDOW_RUNS)
        await db.rollback()
    fp = world.fp
    assert one.keys == 6        # match, bucket, missing, moved (Smoke), moved (Regression, counted only), default
    assert sorted((d.reason, d.suite, d.fingerprint, d.legacy, d.canonical) for d in one.divergences) == sorted([
        ("default_suite", world.default_suite, fp["default"], "absent", "active"),
        ("deletion_window", "Smoke", fp["bucket"], "removed", "active"),
        ("missing_in_canonical", "Smoke", fp["missing"], "active", "absent"),
        ("moved_suite", "Smoke", fp["moved"], "active", "other_suite"),
    ])
    assert one.info_counts == {"legacy_modified_events": 1, "legacy_restored_events": 0}
    assert (two.keys, two.divergences) == (1, [])                 # the archived project is compared too


async def test_the_real_runner_on_a_one_plus_one_pool(env: Any, world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.services import suite_parity_vocab as vocab
    from app.worker import suite_parity_runner as runner

    log = _Recorder()
    monkeypatch.setattr(runner, "_slog", log)
    before = _metric("diffs")
    env.probe.statements.clear()
    env.probe.peak = 0
    result = await runner.run_parity("admin", world.admin, session_factory=env)
    world.parity_ids.add(uuid.UUID(result["run_id"]))
    assert result["status"] == "diffs" and result["blocking"] >= 1 and result["projects"] >= 2
    assert env.probe.peak == 1, "the runner held two connections at once"
    written = {m.group(1).lower() for s in env.probe.statements if (m := _WRITE.match(s))}
    assert written == {"suite_membership_parity_runs"}, written     # the catalog is never written
    assert _metric("diffs") == before + 1

    row = await _row(env, result["run_id"])
    assert (row.status, row.trigger, row.requested_by_id, row.error_code) == ("diffs", "admin", world.admin, None)
    assert row.finished_at is not None and row.finished_at >= row.started_at
    assert row.window_runs == settings.CANONICAL_DELETION_WINDOW_RUNS
    assert set(row.counts) == set(vocab.BLOCKING_REASONS) | set(vocab.EXPECTED_REASONS) | set(vocab.INFO_COUNTS)
    assert row.blocking_diffs == sum(row.counts[r] for r in vocab.BLOCKING_REASONS) >= 1
    assert row.expected_divergences == sum(row.counts[r] for r in vocab.EXPECTED_REASONS) >= 3
    lines = {line["project_id"]: line for line in row.per_project}
    assert lines[str(world.p1)] == {"project_id": str(world.p1), "blocking": 1, "expected": 3}
    assert str(world.p2) not in lines                              # a clean project lists nothing
    ours = {(s["reason"], s["fingerprint"]) for s in row.sample if s["project_id"] in (str(world.p1), str(world.p2))}
    assert ours <= {("default_suite", world.fp["default"]), ("deletion_window", world.fp["bucket"]),
                    ("missing_in_canonical", world.fp["missing"]), ("moved_suite", world.fp["moved"])}
    [done] = log.named("suite_membership_parity_finished")
    assert done["run_id"] == result["run_id"] and done["status"] == "diffs"
    assert set(done) == {"run_id", "trigger", "status", "projects", "keys", "blocking", "expected", "duration_ms"}
    flat = repr(log.calls)
    assert world.tag not in flat and world.fp["missing"] not in flat   # no name, no fingerprint in a log line


async def _running_row(env: Any, world: Any, *, minutes_ago: int) -> uuid.UUID:
    """A ``running`` row started ``minutes_ago`` by the DATABASE clock (the claim compares with now())."""
    from app.models.postgres import SuiteMembershipParityRun

    run_id = uuid.uuid4()
    world.parity_ids.add(run_id)
    async with env() as db:
        db.add(SuiteMembershipParityRun(
            id=run_id, trigger="admin", requested_by_id=world.admin, status="running", window_runs=5,
            started_at=func.now() - timedelta(minutes=minutes_ago), counts={}, per_project=[], sample=[],
        ))
        await db.commit()
    return run_id


async def test_a_second_claim_while_one_is_running_is_skipped(
    env: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.postgres import SuiteMembershipParityRun
    from app.worker import suite_parity_runner as runner

    log = _Recorder()
    monkeypatch.setattr(runner, "_slog", log)
    running = await _running_row(env, world, minutes_ago=2)
    try:
        before_rows, before = await _parity_count(env), {s: _metric(s) for s in ("clean", "diffs", "failed")}
        result = await runner.run_parity("beat", session_factory=env)
        assert result == {"skipped": "already_running"}
        assert await _parity_count(env) == before_rows                 # no row
        assert {s: _metric(s) for s in ("clean", "diffs", "failed")} == before   # no metric
        row = await _row(env, running)
        assert (row.status, row.finished_at, row.error_code) == ("running", None, None)
        assert log.named("suite_membership_parity_skipped") == [{"reason": "already_running", "trigger": "beat"}]
    finally:
        async with env() as db:
            await db.execute(delete(SuiteMembershipParityRun).where(SuiteMembershipParityRun.id == running))
            await db.commit()


async def test_a_61_minute_old_running_row_is_recovered_as_interrupted(env: Any, world: Any) -> None:
    from app.worker import suite_parity_runner as runner

    stale = await _running_row(env, world, minutes_ago=61)
    result = await runner.run_parity("beat", session_factory=env)
    world.parity_ids.add(uuid.UUID(result["run_id"]))
    assert result["status"] == "diffs"
    recovered = await _row(env, stale)
    assert (recovered.status, recovered.error_code) == ("failed", "interrupted")
    assert recovered.finished_at is not None
    fresh = await _row(env, result["run_id"])
    assert (fresh.status, fresh.trigger, fresh.requested_by_id) == ("diffs", "beat", None)


class ParityProbeError(RuntimeError):
    """A failure only this module raises (its TYPE name is what the row records)."""


async def test_a_failure_is_recorded_failed_with_its_type_on_a_fresh_session(
    env: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import suite_membership_parity as parity
    from app.worker import suite_parity_runner as runner

    async def explode(*_: Any, **__: Any) -> Any:
        raise ParityProbeError("compare broke for pq-secret-name")

    monkeypatch.setattr(parity, "compare_project", explode)
    log = _Recorder()
    monkeypatch.setattr(runner, "_slog", log)
    before = _metric("failed")
    env.probe.peak = 0
    result = await runner.run_parity("beat", session_factory=env)      # never raises out
    world.parity_ids.add(uuid.UUID(result["run_id"]))
    assert result == {"run_id": result["run_id"], "status": "failed", "error_code": "ParityProbeError"}
    assert env.probe.peak == 1                                         # the fresh session opens after the work one closed
    row = await _row(env, result["run_id"])
    assert (row.status, row.error_code) == ("failed", "ParityProbeError")
    assert row.finished_at is not None and row.blocking_diffs == 0 and row.per_project == []
    assert _metric("failed") == before + 1
    assert log.named("suite_membership_parity_failed") == [{"run_id": result["run_id"],
                                                            "error_type": "ParityProbeError"}]
    assert "pq-secret-name" not in repr(log.calls)

