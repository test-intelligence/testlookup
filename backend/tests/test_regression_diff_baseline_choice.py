"""D-10 / S-C-4: the Changes tab's baseline is the last GOOD run of the branch, by execution time.

Before the fix ``compute_regression_diff`` took the newest earlier run of the
PROJECT whose pass rate cleared ``RELEASE_PASS_RATE_THRESHOLD * 0.85`` (76.5
with the default 90), ordered by ``created_at`` (upload order), with no branch
and no status term. On a green, red, red history the second red run (93.8 %
pass) became the third run's baseline, so the third run showed zero new
failures and "Bisect from last green" landed on an empty view; a feature-branch
run or an old build uploaded late became main's "last good run".

Now: the run's own branch series first, a PASSED run (no failed or broken test)
before one that only clears the threshold, execution time
(``coalesce(start_time, created_at)``) not upload order, finished runs only;
``baseline_kind`` says which rule chose the baseline.

Executes the REAL statement against in-memory SQLite (the ORM tables).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import types as sqltypes  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.models.postgres import Project, RunBaseline, TestCase, TestRun  # noqa: E402
from app.services import commit_attribution_service, regression_diff_service  # noqa: E402


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


T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        # The tables only: several of their indexes are Postgres-only (NULLS LAST, partial, GIN).
        for table in (Project.__table__, TestRun.__table__, TestCase.__table__, RunBaseline.__table__):
            await conn.execute(CreateTable(table))

    # The commit range is not under test here (test_regression_diff_commit_range.py owns it).
    async def get_commit_range(_db: Any, _run: Any) -> dict:
        return {"commits": []}

    class _Status:
        def model_dump(self, mode: Any = None) -> dict:
            return {"reason": "no_commits"}

    async def status(_db: Any, _run: Any, _serialized: Any) -> Any:
        return _Status()

    monkeypatch.setattr(commit_attribution_service, "get_commit_range", get_commit_range)
    monkeypatch.setattr(regression_diff_service, "commit_range_status", status)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _project(factory: Any) -> uuid.UUID:
    pid = uuid.uuid4()
    async with factory() as db:
        db.add(Project(id=pid, name=f"p-{pid.hex[:6]}", slug=f"p-{pid.hex[:6]}", is_active=True))
        await db.commit()
    return pid


async def _run(
    factory: Any, pid: uuid.UUID, build: str, *, failing: tuple[str, ...] = (), total: int = 16,
    branch: str | None = "main", created_at: datetime, start_time: datetime | None = None,
    status: str | None = None,
) -> TestRun:
    """A finished run of ``total`` tests, ``failing`` of them FAILED (fingerprints), the rest PASSED."""
    failed = len(failing)
    run = TestRun(
        id=uuid.uuid4(), project_id=pid, build_number=build, branch=branch,
        status=status or ("FAILED" if failed else "PASSED"),
        total_tests=total, passed_tests=total - failed, failed_tests=failed, broken_tests=0, skipped_tests=0,
        pass_rate=round(100.0 * (total - failed) / total, 1),
        created_at=created_at, start_time=start_time or created_at,
    )
    async with factory() as db:
        db.add(run)
        await db.flush()
        for n in range(total):
            fp = failing[n] if n < failed else f"ok-{n}"
            db.add(TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name=f"t-{fp}", suite_name="s",
                            test_fingerprint=fp, status="FAILED" if n < failed else "PASSED"))
        await db.commit()
    return run


async def _diff(factory: Any, run: TestRun) -> dict:
    async with factory() as db:
        return await regression_diff_service.compute_regression_diff(run, db)


async def test_red_red_after_green_compares_the_second_red_with_the_green_run(factory: Any) -> None:
    pid = await _project(factory)
    green = await _run(factory, pid, "main-1", created_at=T0)
    await _run(factory, pid, "main-2", failing=("fp-a",), created_at=T0 + timedelta(hours=1))   # 93.8 %
    red2 = await _run(factory, pid, "main-3", failing=("fp-a",), created_at=T0 + timedelta(hours=2))

    diff = await _diff(factory, red2)

    # Before the fix the 93.8 % red run (>= 76.5) was the baseline: zero new failures.
    assert diff["baseline_run_id"] == str(green.id)
    assert diff["baseline_kind"] == "passing"
    assert diff["new_failing_count"] == 1
    assert [t["test_fingerprint"] for t in diff["new_failing_tests"]] == ["fp-a"]


async def test_a_run_on_another_branch_is_never_the_baseline(factory: Any) -> None:
    pid = await _project(factory)
    green = await _run(factory, pid, "main-1", created_at=T0)
    await _run(factory, pid, "feat-1", branch="feature/x", created_at=T0 + timedelta(hours=1))
    red = await _run(factory, pid, "main-2", failing=("fp-a",), created_at=T0 + timedelta(hours=2))

    diff = await _diff(factory, red)

    assert diff["baseline_run_id"] == str(green.id)


async def test_execution_time_beats_upload_order(factory: Any) -> None:
    """An old green build uploaded AFTER a newer red one is not the newer run's "last good run";
    the green that ran before the red one is."""
    pid = await _project(factory)
    ran_first = await _run(factory, pid, "main-1", created_at=T0)
    red = await _run(factory, pid, "main-3", failing=("fp-a",), created_at=T0 + timedelta(hours=1),
                     start_time=T0 + timedelta(hours=3))
    # Executed between main-1 and main-3, uploaded last -- the true last good run before main-3.
    late = await _run(factory, pid, "main-2", created_at=T0 + timedelta(hours=5), start_time=T0 + timedelta(hours=2))

    diff = await _diff(factory, red)

    assert diff["baseline_run_id"] == str(late.id) != str(ran_first.id)
    # And the late upload is not compared against a run that executed after it.
    later = await _diff(factory, late)
    assert later["baseline_run_id"] == str(ran_first.id)


async def test_the_threshold_rule_is_only_a_labelled_fallback(factory: Any) -> None:
    pid = await _project(factory)
    near = await _run(factory, pid, "main-1", failing=("fp-old",), created_at=T0)         # 93.8 %, not green
    red = await _run(factory, pid, "main-2", failing=("fp-old", "fp-a"), created_at=T0 + timedelta(hours=1))

    diff = await _diff(factory, red)

    assert diff["baseline_run_id"] == str(near.id)
    assert diff["baseline_kind"] == "threshold"
    assert diff["baseline_threshold"] == pytest.approx(regression_diff_service.baseline_threshold())
    assert diff["new_failing_count"] == 1


async def test_an_in_progress_run_is_never_the_baseline(factory: Any) -> None:
    pid = await _project(factory)
    green = await _run(factory, pid, "main-1", created_at=T0)
    await _run(factory, pid, "main-2", created_at=T0 + timedelta(hours=1), status="IN_PROGRESS")
    red = await _run(factory, pid, "main-3", failing=("fp-a",), created_at=T0 + timedelta(hours=2))

    assert (await _diff(factory, red))["baseline_run_id"] == str(green.id)


async def test_runs_without_a_branch_compare_among_themselves_then_fall_back_to_main(factory: Any) -> None:
    pid = await _project(factory)
    main_green = await _run(factory, pid, "main-1", created_at=T0)
    bare_red = await _run(factory, pid, "local-1", branch=None, failing=("fp-a",), created_at=T0 + timedelta(hours=1))

    # No earlier branchless run: main is the fallback.
    assert (await _diff(factory, bare_red))["baseline_run_id"] == str(main_green.id)

    bare_green = await _run(factory, pid, "local-2", branch=None, created_at=T0 + timedelta(hours=2))
    await _run(factory, pid, "main-2", created_at=T0 + timedelta(hours=3))
    bare_red2 = await _run(factory, pid, "local-3", branch=None, failing=("fp-a",), created_at=T0 + timedelta(hours=4))

    # Its own series first, even though a newer main green exists.
    assert (await _diff(factory, bare_red2))["baseline_run_id"] == str(bare_green.id)


async def test_no_earlier_run_answers_no_baseline_with_a_null_kind(factory: Any) -> None:
    pid = await _project(factory)
    red = await _run(factory, pid, "main-1", failing=("fp-a",), created_at=T0)

    diff = await _diff(factory, red)

    assert diff["baseline_available"] is False
    assert diff["baseline_kind"] is None
    # ``?bisect=top`` can still open a narrowing: the run's first failing test.
    assert diff["first_failing_test"] == {"test_name": "t-fp-a", "suite_name": "s", "test_fingerprint": "fp-a"}


async def test_the_first_failing_test_is_named_when_nothing_is_new(factory: Any) -> None:
    """Both runs fail the same tests: zero new failures, but the run's first failing test (by name) is named."""
    pid = await _project(factory)
    await _run(factory, pid, "main-1", failing=("fp-b", "fp-a"), created_at=T0)
    red = await _run(factory, pid, "main-2", failing=("fp-b", "fp-a"), created_at=T0 + timedelta(hours=1))

    diff = await _diff(factory, red)

    assert diff["new_failing_count"] == 0
    assert diff["first_failing_test"]["test_fingerprint"] == "fp-a"

    green = await _run(factory, pid, "main-3", created_at=T0 + timedelta(hours=2))
    assert (await _diff(factory, green))["first_failing_test"] is None
