"""W25a HYG-04 — the three parity routes through the REAL router (contract §3.1).

An in-memory SQLite database holds the tables the routes touch (projects, users,
suite_membership_parity_runs, with the one-running partial unique index rebuilt
in SQLite syntax); ``get_db`` and ``get_current_active_user`` are overridden;
``require_instance_admin`` is the real one. An engine listener records every
statement, so "a GET writes nothing" is measured, not assumed. The POST's
dispatch binds the REAL task signature (``run_suite_membership_parity.s(...)``)
BEFORE ``apply_async`` is replaced, so Celery's argument check is never hidden.

W25d HYG-04 (the cutover, contract §2.1 C-6 / C-7, §3): the database also holds
``feature_flags`` (the ``suite_membership_legacy_writes`` row, seeded ON like
migration 0251) and ``settings_audit_log``. The flag is read through the REAL
``feature_flags.is_enabled``; only its shared Redis cache is faked (a miss on
every read) and the Postgres-only advisory lock is a recorder. The cutover POST
runs the REAL ``update_flag`` (its call bound to the real signature first), so
the flag row, its audit row and the order update → commit → cache drop → epoch
bump are measured on the database, not assumed.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.deps import (
    CREDENTIAL_KIND_API_KEY,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
)
from app.db.postgres import get_db
from app.models.postgres import (
    Base,
    FeatureFlag,
    Project,
    SettingsAuditLog,
    SuiteMembershipParityRun,
    User,
    UserRole,
)
from app.routers import admin_maintenance as routes
from app.services import feature_flags as ff
from app.services import suite_membership_cutover as cutover
from app.services import suite_parity_vocab as vocab
from app.worker import tasks

BASE = "/api/v1/admin/maintenance/suite-membership-parity"
CUTOVER = "/api/v1/admin/maintenance/suite-membership-cutover"


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:  # noqa: D103 - JSONB as SQLite JSON
    return "JSON"


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    # ``'{}'::jsonb`` is Postgres syntax: the rows always carry the JSON columns explicitly.
    table = SuiteMembershipParityRun.__table__
    for column in ("counts", "per_project", "sample"):
        monkeypatch.setattr(table.c[column], "server_default", None)
    # W25d: the flag is read through the REAL is_enabled; only the shared Redis cache is faked (always a miss),
    # and the Postgres-only advisory lock is a no-op that journals itself.
    lock_calls: list[str] = []

    async def _miss(key: str) -> None:
        return None

    async def _no_store(key: str, flag: Any) -> None:
        return None

    async def _lock(db: Any) -> None:
        lock_calls.append("lock")

    monkeypatch.setattr(ff, "_load_from_redis", _miss)
    monkeypatch.setattr(ff, "_store_in_redis", _no_store)
    monkeypatch.setattr(ff, "lock_global_agent_authority", _lock)
    monkeypatch.setattr(routes, "lock_global_agent_authority", _lock)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            Project.__table__, User.__table__, table, FeatureFlag.__table__, SettingsAuditLog.__table__,
        ])
        await conn.execute(text("DROP INDEX IF EXISTS uq_smpr_one_running"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX uq_smpr_one_running ON suite_membership_parity_runs (status) "
            "WHERE status = 'running'"
        ))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    maker.statements = statements  # type: ignore[attr-defined]
    maker.engine = engine  # type: ignore[attr-defined]
    maker.lock_calls = lock_calls  # type: ignore[attr-defined]
    yield maker
    await engine.dispose()


def _user(role: UserRole, name: str) -> User:
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True)


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", is_active=True)
    admin, lead = _user(UserRole.ADMIN, "ada"), _user(UserRole.QA_LEAD, "lee")
    flag = FeatureFlag(id=uuid.uuid4(), key=cutover.FEATURE_FLAG, description="seeded by 0251",
                       enabled_global=True, rollout_percent=100)      # migration 0251's seed: legacy writes ON
    async with factory() as db:
        db.add_all([project, admin, lead, flag])
        await db.commit()
    bound = SimpleNamespace(id=admin.id, role=UserRole.ADMIN.value, is_active=True, username="ci-key",
                            full_name=None, email="ci@example.test")
    _bind_credential_kind(_bind_api_key_project(bound, project.id), CREDENTIAL_KIND_API_KEY)
    return SimpleNamespace(project=project, admin=admin, lead=lead, bound=bound)


def _client(factory: Any, user: Any) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)

    async def _db() -> Any:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _run(factory: Any, *, started_at: datetime, status: str = "clean", trigger: str = "beat",
               requested_by_id: uuid.UUID | None = None, **values: Any) -> SuiteMembershipParityRun:
    finished = None if status == "running" else started_at + timedelta(minutes=2)
    row = SuiteMembershipParityRun(
        id=uuid.uuid4(), trigger=trigger, status=status, started_at=started_at, finished_at=finished,
        requested_by_id=requested_by_id, window_runs=5, counts=values.pop("counts", {}),
        per_project=values.pop("per_project", []), sample=values.pop("sample", []), **values,
    )
    async with factory() as db:
        db.add(row)
        await db.commit()
    return row


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Binds each payload to the REAL task signature first (``.s()`` + ``inspect``), then records it."""
    calls: list[dict[str, Any]] = []
    task = tasks.run_suite_membership_parity

    def apply_async(args: Any = None, kwargs: Any = None, **options: Any) -> Any:
        signature = task.s(*(args or ()), **(kwargs or {}))          # Celery's own signature object
        inspect.signature(task.run).bind(*signature.args, **signature.kwargs)
        calls.append(dict(kwargs or {}))
        return SimpleNamespace(id="celery-parity-id")

    monkeypatch.setattr(task, "apply_async", apply_async)
    return calls


def _at(day_offset: int, hour: int = 6, minute: int = 15) -> datetime:
    today = datetime.now(timezone.utc).date()
    day = today - timedelta(days=day_offset)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", ["lead", "bound"])
async def test_a_qa_lead_and_a_project_bound_admin_key_are_refused(
    factory: Any, world: Any, dispatched: list, who: str,
) -> None:
    run = await _run(factory, started_at=_at(1))
    factory.statements.clear()
    async with _client(factory, getattr(world, who)) as client:
        answers = [await client.get(BASE), await client.get(f"{BASE}/{run.id}"), await client.post(BASE, json={})]
    assert [a.status_code for a in answers] == [403, 403, 403]
    assert dispatched == [] and _writes(factory.statements) == []


# ── the history GET ──────────────────────────────────────────────────────────

async def test_no_comparison_yet_is_an_empty_history_not_zero_differences(factory: Any, world: Any) -> None:
    from app.core.config import settings

    async with _client(factory, world.admin) as client:
        answer = await client.get(BASE)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "runs": [], "streak_days": 0, "required_days": vocab.REQUIRED_CLEAN_DAYS, "cutover_ready": False,
        "window_runs_now": settings.CANONICAL_DELETION_WINDOW_RUNS,
        # W25d: before the cutover, today's answer plus the null-filled cut-over state (legacy writes ON).
        "cutover": {"legacy_writes": True, "cut_over_at": None, "cut_over_by_name": None,
                    "streak_days_at_cutover": None},
    }


async def test_seven_clean_days_are_cutover_ready_and_the_get_writes_nothing(factory: Any, world: Any) -> None:
    for offset in range(1, 8):
        await _run(factory, started_at=_at(offset), projects_checked=2, keys_compared=40, expected_divergences=3)
    admin_run = await _run(factory, started_at=_at(1, hour=3), trigger="admin", requested_by_id=world.admin.id,
                           status="diffs", blocking_diffs=4)
    await _run(factory, started_at=_at(20))                            # older than HISTORY_DAYS: not listed
    factory.statements.clear()
    async with _client(factory, world.admin) as client:
        body = (await client.get(BASE)).json()
    assert _writes(factory.statements) == []
    assert body["streak_days"] == 7 and body["cutover_ready"] is True   # 03:00 diffs, then the 06:15 clean decides
    assert len(body["runs"]) == 8
    started = [datetime.fromisoformat(r["started_at"]) for r in body["runs"]]
    assert started == sorted(started, reverse=True)
    listed = {r["id"]: r for r in body["runs"]}
    admin_line = listed[str(admin_run.id)]
    assert admin_line["requested_by_name"] == "ada" and admin_line["status"] == "diffs"
    assert admin_line["blocking_diffs"] == 4
    beat_lines = [r for r in body["runs"] if r["trigger"] == "beat"]
    assert {r["requested_by_name"] for r in beat_lines} == {None}
    assert set(beat_lines[0]) == {"id", "trigger", "status", "started_at", "finished_at", "requested_by_name",
                                  "window_runs", "projects_checked", "keys_compared", "blocking_diffs",
                                  "expected_divergences"}
    assert beat_lines[0]["keys_compared"] == 40 and beat_lines[0]["expected_divergences"] == 3


async def test_a_missing_day_breaks_the_streak(factory: Any, world: Any) -> None:
    for offset in (1, 2, 4, 5, 6, 7, 8):                               # day -3 never ran
        await _run(factory, started_at=_at(offset))
    async with _client(factory, world.admin) as client:
        body = (await client.get(BASE)).json()
    assert body["streak_days"] == 2 and body["cutover_ready"] is False


async def test_a_running_and_a_failed_run_are_listed_but_never_clean(factory: Any, world: Any) -> None:
    await _run(factory, started_at=_at(2), status="failed", error_code="TimeoutError")
    await _run(factory, started_at=_at(1))
    await _run(factory, started_at=datetime.now(timezone.utc) - timedelta(minutes=3), status="running")
    async with _client(factory, world.admin) as client:
        body = (await client.get(BASE)).json()
    assert [r["status"] for r in body["runs"]] == ["running", "clean", "failed"]
    assert body["runs"][0]["finished_at"] is None
    assert body["streak_days"] == 1


# ── the detail GET ───────────────────────────────────────────────────────────

async def test_an_unknown_run_is_404_with_its_sentence(factory: Any, world: Any) -> None:
    async with _client(factory, world.admin) as client:
        missing = await client.get(f"{BASE}/{uuid.uuid4()}")
        malformed = await client.get(f"{BASE}/not-a-uuid")
    assert missing.status_code == 404 and missing.json() == {"detail": "Parity run not found."}
    assert malformed.status_code == 422


async def test_the_detail_resolves_names_flags_blocking_and_zero_fills_counts(factory: Any, world: Any) -> None:
    gone = uuid.uuid4()                                                # a project deleted since the run
    pid = str(world.project.id)
    run = await _run(
        factory, started_at=_at(1), status="diffs", trigger="admin", requested_by_id=world.admin.id,
        projects_checked=2, keys_compared=30, blocking_diffs=1, expected_divergences=2,
        counts={"retire_pending": 1, "moved_suite": 2, "legacy_modified_events": 5},
        per_project=[{"project_id": pid, "blocking": 1, "expected": 1},
                     {"project_id": str(gone), "blocking": 0, "expected": 1}],
        sample=[
            {"reason": "retire_pending", "project_id": pid, "suite": "Smoke", "fingerprint": "fpq-one",
             "test_name": "test_pay", "legacy": "removed", "canonical": "active"},
            {"reason": "moved_suite", "project_id": str(gone), "suite": "Nightly", "fingerprint": "fpq-two",
             "test_name": None, "legacy": "active", "canonical": "other_suite"},
        ],
    )
    factory.statements.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.get(f"{BASE}/{run.id}")
    assert answer.status_code == 200, answer.text
    assert _writes(factory.statements) == []
    body = answer.json()
    assert body["id"] == str(run.id) and body["requested_by_name"] == "ada" and body["error_code"] is None
    assert set(body["counts"]) == set(vocab.BLOCKING_REASONS) | set(vocab.EXPECTED_REASONS) | set(vocab.INFO_COUNTS)
    assert body["counts"]["retire_pending"] == 1 and body["counts"]["legacy_modified_events"] == 5
    assert body["counts"]["name_drift"] == 0
    assert body["per_project"] == [
        {"project_id": pid, "project_name": "Payment Service", "blocking": 1, "expected": 1},
        {"project_id": str(gone), "project_name": None, "blocking": 0, "expected": 1},
    ]
    first, second = body["sample"]
    assert (first["reason"], first["blocking"], first["project_name"]) == ("retire_pending", True, "Payment Service")
    assert (second["reason"], second["blocking"], second["project_name"]) == ("moved_suite", False, None)
    assert second["test_name"] is None and second["canonical"] == "other_suite"


async def test_a_failed_run_carries_its_error_code(factory: Any, world: Any) -> None:
    run = await _run(factory, started_at=_at(1), status="failed", error_code="interrupted")
    async with _client(factory, world.admin) as client:
        body = (await client.get(f"{BASE}/{run.id}")).json()
    assert body["status"] == "failed" and body["error_code"] == "interrupted"
    assert body["per_project"] == [] and body["sample"] == [] and set(body["counts"].values()) == {0}


# ── the POST ("Compare now") ─────────────────────────────────────────────────

async def test_compare_now_queues_the_task_with_its_real_payload(factory: Any, world: Any, dispatched: list) -> None:
    factory.statements.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(BASE, json={})
    assert answer.status_code == 202, answer.text
    assert answer.json() == {"queued": True, "task_id": "celery-parity-id", "task_name": "run_suite_membership_parity"}
    assert dispatched == [{"trigger": "admin", "requested_by_id": str(world.admin.id)}]
    assert _writes(factory.statements) == []                          # the worker claims the row, not the API


def test_the_dispatch_recorder_really_checks_the_signature(dispatched: list) -> None:
    with pytest.raises(TypeError):
        tasks.run_suite_membership_parity.apply_async(kwargs={"trigger": "admin", "requester": "x"})
    assert dispatched == []


async def test_compare_now_is_409_while_a_fresh_run_is_running(factory: Any, world: Any, dispatched: list) -> None:
    await _run(factory, started_at=datetime.now(timezone.utc) - timedelta(minutes=5), status="running")
    async with _client(factory, world.admin) as client:
        answer = await client.post(BASE, json={})
    assert answer.status_code == 409 and answer.json() == {"detail": "A comparison is already running."}
    assert dispatched == []


async def test_a_stale_running_row_does_not_block_compare_now(factory: Any, world: Any, dispatched: list) -> None:
    await _run(factory, started_at=datetime.now(timezone.utc) - timedelta(minutes=61), status="running")
    async with _client(factory, world.admin) as client:
        answer = await client.post(BASE, json={})
    assert answer.status_code == 202                                   # the runner's claim recovers it as interrupted
    assert len(dispatched) == 1


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        return lambda event, **kw: self.calls.append((level, event, kw))


async def test_compare_now_is_503_with_its_sentence_when_dispatch_fails(
    factory: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise ConnectionRefusedError("broker at amqp-Xq unreachable")

    monkeypatch.setattr(tasks.run_suite_membership_parity, "apply_async", refuse)
    log = _Recorder()
    monkeypatch.setattr(routes, "logger", log)
    async with _client(factory, world.admin) as client:
        answer = await client.post(BASE, json={})
    assert answer.status_code == 503 and answer.json() == {"detail": "Could not queue the comparison."}
    assert "amqp-Xq" not in answer.text
    assert log.calls == [("warning", "admin_suite_parity_dispatch_failed",
                          {"actor_user_id": str(world.admin.id), "error_type": "ConnectionRefusedError"})]


# ── the task and the daily beat ──────────────────────────────────────────────

def test_the_daily_beat_entry_and_the_task_limits() -> None:
    from celery.schedules import crontab

    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["daily-suite-membership-parity"]
    assert entry["task"] == "app.worker.tasks.run_suite_membership_parity"
    assert entry["schedule"] == crontab(hour=6, minute=15)
    assert entry["kwargs"] == {"trigger": "beat"}
    assert entry["options"] == {"queue": "default", "ignore_result": True}
    reconcile = celery_app.conf.beat_schedule["nightly-canonical-deletion-reconcile"]["schedule"]
    assert (sorted(reconcile.hour), sorted(reconcile.minute)) == ([5], [30])   # 45 minutes before the comparison
    task = celery_app.tasks["app.worker.tasks.run_suite_membership_parity"]
    assert task.queue == "default" and task.max_retries == 0
    assert task.soft_time_limit == 1500 and task.time_limit == 1800
    assert task.time_limit < vocab.RUN_STALE_MINUTES * 60              # a hard-killed run is recovered by the next claim
    params = inspect.signature(task.run).parameters
    assert list(params) == ["trigger", "requested_by_id"] and params["trigger"].default == "beat"


def test_the_task_binds_its_own_log_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reused worker thread keeps the previous task's structlog context; the parity task clears and rebinds it,
    so ``suite_membership_parity_finished`` never names another task (seen on the W25a local demo)."""
    from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

    from app.worker import suite_parity_runner

    seen: dict[str, Any] = {}

    async def fake_run_parity(trigger: str, requester: Any) -> dict:
        seen.update(get_contextvars())
        return {"status": "clean"}

    monkeypatch.setattr(suite_parity_runner, "run_parity", fake_run_parity)
    bind_contextvars(celery_task_name="app.worker.tasks.run_agent_investigation", investigation_id="stale-id")
    try:
        assert tasks.run_suite_membership_parity(trigger="admin") == {"status": "clean"}
    finally:
        clear_contextvars()
    assert seen["celery_task_name"] == "app.worker.tasks.run_suite_membership_parity"
    assert seen["parity_trigger"] == "admin"
    assert "investigation_id" not in seen


# ── W25d HYG-04: the cutover POST, the GET's cut-over state, "Compare now" after it ──────────────


async def _set_flag(factory: Any, enabled: bool) -> None:
    from sqlalchemy import update

    async with factory() as db:
        await db.execute(update(FeatureFlag).where(FeatureFlag.key == cutover.FEATURE_FLAG)
                         .values(enabled_global=enabled))
        await db.commit()


async def _flag(factory: Any) -> FeatureFlag:
    from sqlalchemy import select

    async with factory() as db:
        return (await db.execute(select(FeatureFlag).where(FeatureFlag.key == cutover.FEATURE_FLAG))).scalar_one()


async def _audit(factory: Any) -> list[SettingsAuditLog]:
    from sqlalchemy import select

    async with factory() as db:
        return list((await db.execute(select(SettingsAuditLog))).scalars().all())


async def _clean_days(factory: Any, offsets: range | tuple[int, ...]) -> None:
    for offset in offsets:
        await _run(factory, started_at=_at(offset), projects_checked=2, keys_compared=40)


@pytest.fixture
def journal(factory: Any, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """update_flag (bound to the REAL signature, then run for real) → DBAPI commit → cache drop → epoch bump."""
    from app.services import cache_service

    entries: list[Any] = []
    real_update = ff.update_flag
    signature = inspect.signature(real_update)

    async def _update(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        entries.append(("update_flag", {k: v for k, v in bound.arguments.items() if k != "db"}))
        return await real_update(*args, **kwargs)

    async def _invalidate(key: str) -> None:
        entries.append(("invalidate", key))

    async def _bump(project_ids: Any) -> None:
        entries.append(("bump", sorted(str(p) for p in project_ids)))

    @event.listens_for(factory.engine.sync_engine, "commit")
    def _commit(conn: Any) -> None:
        entries.append(("commit", None))

    monkeypatch.setattr(ff, "update_flag", _update)
    monkeypatch.setattr(ff, "invalidate_flag_cache", _invalidate)
    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _bump)
    entries.clear()
    return entries


@pytest.mark.parametrize("who", ["lead", "bound"])
async def test_the_cutover_refuses_a_qa_lead_and_a_project_bound_admin_key(
    factory: Any, world: Any, journal: list, who: str,
) -> None:
    await _clean_days(factory, range(1, 8))
    factory.statements.clear()
    journal.clear()
    async with _client(factory, getattr(world, who)) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 403
    assert journal == [] and _writes(factory.statements) == []
    assert (await _flag(factory)).enabled_global is True


async def test_the_cutover_is_409_when_already_cut_over(factory: Any, world: Any, journal: list) -> None:
    await _clean_days(factory, range(1, 8))
    await _set_flag(factory, False)
    factory.statements.clear()
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 409
    assert answer.json() == {"detail": "Already cut over: the old suite-membership tables are not written."}
    assert journal == [] and _writes(factory.statements) == []


async def test_the_cutover_is_409_while_a_comparison_is_running(factory: Any, world: Any, journal: list) -> None:
    await _clean_days(factory, range(1, 8))
    await _run(factory, started_at=datetime.now(timezone.utc) - timedelta(minutes=5), status="running")
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 409
    assert answer.json() == {"detail": "A comparison is running; cut over when it has finished."}
    assert journal == [] and (await _flag(factory)).enabled_global is True


async def test_the_cutover_is_409_before_seven_clean_days(factory: Any, world: Any, journal: list) -> None:
    await _clean_days(factory, (1, 2, 3))
    factory.statements.clear()
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 409
    assert answer.json() == {"detail": "Not ready: day 3 of 7 with no differences."}
    assert journal == [] and _writes(factory.statements) == []
    assert (await _flag(factory)).enabled_global is True and await _audit(factory) == []


async def test_two_clicks_make_one_change_the_reread_under_the_lock_refuses(
    factory: Any, world: Any, journal: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first read said ON; by the time the lock is held another request has turned it off."""
    from sqlalchemy import update

    async def _lock_after_the_other_click(db: Any) -> None:
        await db.execute(update(FeatureFlag).where(FeatureFlag.key == cutover.FEATURE_FLAG)
                         .values(enabled_global=False))

    monkeypatch.setattr(routes, "lock_global_agent_authority", _lock_after_the_other_click)
    await _clean_days(factory, range(1, 8))
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 409
    assert answer.json() == {"detail": "Already cut over: the old suite-membership tables are not written."}
    assert [e for e in journal if e[0] == "update_flag"] == []          # update_flag never ran
    assert await _audit(factory) == []


async def test_the_cutover_turns_the_flag_off_in_order_and_the_card_reads_it(
    factory: Any, world: Any, journal: list, dispatched: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _Recorder()
    monkeypatch.setattr(routes, "logger", log)
    await _clean_days(factory, range(1, 8))
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={"ignored": True})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"cut_over_at", "streak_days"} and body["streak_days"] == 7
    assert datetime.fromisoformat(body["cut_over_at"]) <= datetime.now(timezone.utc)

    # update_flag (real signature) → the ONE commit → the cache drop → the epoch bump, in that order.
    assert [e[0] for e in journal][:4] == ["update_flag", "commit", "invalidate", "bump"]
    update_args = journal[0][1]
    assert update_args == {
        "key": "suite_membership_legacy_writes", "updates": {"enabled_global": False},
        "actor": world.admin, "reason": "HYG-04 cutover after 7 clean UTC days",
    }
    assert journal[2] == ("invalidate", "suite_membership_legacy_writes")
    assert journal[3] == ("bump", [str(world.project.id)])
    assert factory.lock_calls                                            # the flag service's lock was taken
    flag = await _flag(factory)
    assert flag.enabled_global is False and flag.updated_by_user_id == world.admin.id
    [audit] = await _audit(factory)
    assert (audit.setting_key, audit.action, audit.actor_name) == (
        "feature_flag:suite_membership_legacy_writes", "update", "ada")
    assert audit.changed_fields == ["enabled_global"] and audit.reason == "HYG-04 cutover after 7 clean UTC days"
    assert log.calls == [("info", "suite_membership_cutover",
                          {"actor_user_id": str(world.admin.id), "streak_days": 7})]

    async with _client(factory, world.admin) as client:
        history = (await client.get(BASE)).json()
        compare = await client.post(BASE, json={})
        again = await client.post(CUTOVER, json={})
    state = history["cutover"]
    assert state["legacy_writes"] is False and state["cut_over_by_name"] == "ada"
    assert state["streak_days_at_cutover"] == 7 and state["cut_over_at"] is not None
    assert history["streak_days"] == 7 and history["cutover_ready"] is True and len(history["runs"]) == 7
    assert compare.status_code == 409 and compare.json() == {
        "detail": "The comparison is retired: the old suite-membership tables are no longer written."}
    assert dispatched == []
    assert again.status_code == 409 and again.json()["detail"].startswith("Already cut over")


async def test_a_failing_epoch_bump_still_answers_200_and_logs_the_type(
    factory: Any, world: Any, journal: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import cache_service

    async def _redis_down(project_ids: Any) -> None:
        raise ConnectionError("redis at cache-Xq refused")

    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _redis_down)
    log = _Recorder()
    monkeypatch.setattr(routes, "logger", log)
    await _clean_days(factory, range(1, 8))
    journal.clear()
    async with _client(factory, world.admin) as client:
        answer = await client.post(CUTOVER, json={})
    assert answer.status_code == 200, answer.text
    assert "cache-Xq" not in answer.text and "cache-Xq" not in repr(log.calls)
    assert ("warning", "suite_membership_cutover_epoch_bump_failed",
            {"actor_user_id": str(world.admin.id), "error_type": "ConnectionError"}) in log.calls
    assert (await _flag(factory)).enabled_global is False


async def test_after_the_cutover_the_history_window_ends_at_the_cutover(factory: Any, world: Any) -> None:
    """Cut over at day -3 10:12 after seven clean days (-9 … -3); a run started after the cut is not listed, and
    the three dates since (no run: the runner skips) never read as missed days."""
    cut = _at(3, hour=10, minute=12)
    await _clean_days(factory, range(3, 10))
    late = await _run(factory, started_at=_at(1), status="diffs", blocking_diffs=2)
    await _set_flag(factory, False)
    async with factory() as db:
        db.add(SettingsAuditLog(setting_key="feature_flag:suite_membership_legacy_writes", action="update",
                                actor_id=world.admin.id, actor_name="ada", changed_fields=["enabled_global"],
                                reason="HYG-04 cutover after 7 clean UTC days", created_at=cut))
        db.add(SettingsAuditLog(setting_key="feature_flag:suite_membership_legacy_writes", action="update",
                                actor_id=world.admin.id, actor_name="lee", changed_fields=["description"],
                                created_at=cut + timedelta(days=1)))   # a later description edit is not the cutover
        await db.commit()
    factory.statements.clear()
    async with _client(factory, world.admin) as client:
        body = (await client.get(BASE)).json()
    assert _writes(factory.statements) == []
    assert body["cutover"] == {"legacy_writes": False, "cut_over_at": body["cutover"]["cut_over_at"],
                               "cut_over_by_name": "ada", "streak_days_at_cutover": 7}
    assert datetime.fromisoformat(body["cutover"]["cut_over_at"]) == cut
    assert str(late.id) not in {r["id"] for r in body["runs"]} and len(body["runs"]) == 7
    assert body["streak_days"] == 7 and body["cutover_ready"] is True


async def test_off_without_an_audit_row_shows_no_cutover_date_and_the_live_window(factory: Any, world: Any) -> None:
    """The row flipped outside the audit trail: "not written", never "cut over after N clean days"."""
    await _clean_days(factory, (1, 2))
    await _set_flag(factory, False)
    async with _client(factory, world.admin) as client:
        body = (await client.get(BASE)).json()
    assert body["cutover"] == {"legacy_writes": False, "cut_over_at": None, "cut_over_by_name": None,
                               "streak_days_at_cutover": None}
    assert body["streak_days"] == 2 and body["cutover_ready"] is False and len(body["runs"]) == 2
