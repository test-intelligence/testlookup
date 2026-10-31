"""W25a HYG-04 — the three parity routes through the REAL router (contract §3.1).

An in-memory SQLite database holds the tables the routes touch (projects, users,
suite_membership_parity_runs, with the one-running partial unique index rebuilt
in SQLite syntax); ``get_db`` and ``get_current_active_user`` are overridden;
``require_instance_admin`` is the real one. An engine listener records every
statement, so "a GET writes nothing" is measured, not assumed. The POST's
dispatch binds the REAL task signature (``run_suite_membership_parity.s(...)``)
BEFORE ``apply_async`` is replaced, so Celery's argument check is never hidden.
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
from app.models.postgres import Base, Project, SuiteMembershipParityRun, User, UserRole
from app.routers import admin_maintenance as routes
from app.services import suite_parity_vocab as vocab
from app.worker import tasks

BASE = "/api/v1/admin/maintenance/suite-membership-parity"


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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Project.__table__, User.__table__, table])
        await conn.execute(text("DROP INDEX IF EXISTS uq_smpr_one_running"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX uq_smpr_one_running ON suite_membership_parity_runs (status) "
            "WHERE status = 'running'"
        ))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    maker.statements = statements  # type: ignore[attr-defined]
    yield maker
    await engine.dispose()


def _user(role: UserRole, name: str) -> User:
    return User(id=uuid.uuid4(), username=name, email=f"{name}@example.test", full_name=name.title(),
                hashed_password="x", role=role.value, is_active=True)


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", is_active=True)
    admin, lead = _user(UserRole.ADMIN, "ada"), _user(UserRole.QA_LEAD, "lee")
    async with factory() as db:
        db.add_all([project, admin, lead])
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
