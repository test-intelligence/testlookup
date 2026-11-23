"""D-11: offline, the narrowing lists the commits the CI supplied with the builds in between.

Before the fix ``bisect_service.compare_narrowed`` returned ``unavailable`` /
``offline_mode`` before looking at anything stored, so the commits that arrived
(``run_commit_ranges.source = supplied``) with the builds after the last pass
up to the first failure were never offered offline, while the Suspects panel
beside it ranked an unrelated build's commits.

Now: offline, those rows are read (the bad build's branch series, after the
good build up to and including the bad one), de-duplicated by sha and ranked
as ``state: "supplied"``; ``offline_mode`` only when none carries a range.

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

from app.core.config import settings  # noqa: E402
from app.models.postgres import Project, RunCommitRange, TestCase, TestRun  # noqa: E402
from app.services import bisect_service  # noqa: E402
from app.services import commit_attribution_service as cas  # noqa: E402
from app.services.bisect_narrowing import Narrowing, WalkRow  # noqa: E402
from app.services.bisect_vocab import COMPARE_STATES  # noqa: E402


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
BRANCH = "main"
FP = "fp-sentinel"


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        for table in (Project.__table__, TestRun.__table__, TestCase.__table__, RunCommitRange.__table__):
            await conn.execute(CreateTable(table))

    async def _no_integration(_db: Any, _run: Any) -> None:
        return None

    monkeypatch.setattr(cas, "_commit_link_api_base", _no_integration)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _commit(sha: str, files: list[str]) -> dict:
    return {"sha": sha, "author": "Ben Dev", "message": f"change {sha}", "files": files,
            "committed_at": "2026-11-01T10:00:00Z"}


async def _history(factory: Any, supplied: dict[str, list[dict]], *, other_branch: list[dict] | None = None) -> dict:
    """Builds 1..5 on main, one hour apart: 1 pass, 2 pass (good), 3 untested, 4 fail (bad), 5 fail (the run).

    ``supplied`` maps a build to the commits the CI supplied with it."""
    pid = uuid.uuid4()
    runs: dict[str, TestRun] = {}
    async with factory() as db:
        db.add(Project(id=pid, name="p", slug=f"p-{pid.hex[:6]}", is_active=True))
        for n in range(1, 6):
            run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=str(n), branch=BRANCH,
                          commit_hash=f"{n:040x}", status="FAILED" if n >= 4 else "PASSED",
                          created_at=T0 + timedelta(hours=n), ci_repo="acme/payments")
            runs[str(n)] = run
            db.add(run)
        if other_branch is not None:
            stray = TestRun(id=uuid.uuid4(), project_id=pid, build_number="f-1", branch="feature/x",
                            status="PASSED", created_at=T0 + timedelta(hours=3, minutes=30))
            db.add(stray)
            await db.flush()
            db.add(RunCommitRange(id=uuid.uuid4(), run_id=stray.id, project_id=pid, source="supplied",
                                  base_source="supplied", commits=other_branch))
        await db.flush()
        for build, commits in supplied.items():
            db.add(RunCommitRange(id=uuid.uuid4(), run_id=runs[build].id, project_id=pid, source="supplied",
                                  base_source="supplied", commits=commits))
        db.add(TestCase(id=uuid.uuid4(), test_run_id=runs["5"].id, test_name="checkout_test",
                        class_name="payments.checkout.CheckoutTest", test_fingerprint=FP, status="FAILED"))
        await db.commit()
    return runs


def _ref(run: TestRun, outcome: str) -> WalkRow:
    return WalkRow(run_id=run.id, build_number=run.build_number, commit_hash=run.commit_hash, branch=run.branch,
                   created_at=run.created_at, ci_run_url=None, outcome=outcome)


def _narrowing(runs: dict[str, TestRun]) -> Narrowing:
    return Narrowing(state="narrowed", anchor="same_branch", fallback_branch=None, good=_ref(runs["2"], "pass"),
                     bad=_ref(runs["4"], "fail"), streak_length=2, untested=(_ref(runs["3"], "untested"),),
                     untested_count=1, runs_read=5, bounded=False)


async def _compare(factory: Any, runs: dict[str, TestRun]) -> dict:
    async with factory() as db:
        return await bisect_service.compare_narrowed(db, runs["5"], _narrowing(runs), FP, run_level_commit_count=1)


async def test_offline_lists_the_supplied_commits_of_the_builds_in_between(factory: Any) -> None:
    runs = await _history(factory, {
        "2": [_commit("aaa0001", ["payments/old.py"])],             # the good build's own: before the range
        "3": [_commit("bbb0001", ["README.md"])],
        "4": [_commit("bbb0001", ["README.md"]), _commit("ccc0001", ["payments/checkout.py"])],  # bbb repeated
        "5": [_commit("ddd0001", ["payments/later.py"])],          # after the first failure: not in the range
    })

    compare = await _compare(factory, runs)

    assert "supplied" in COMPARE_STATES
    assert (compare["state"], compare["reason"]) == ("supplied", None)
    assert compare["commit_count"] == 2
    assert sorted(c["sha"] for c in compare["commits"]) == ["bbb0001", "ccc0001"]
    assert compare["commits"][0]["sha"] == "ccc0001"  # the checkout change ranks first (path overlap)
    assert compare["caveat"] == cas.MONOREPO_CAVEAT
    assert compare["run_level_commit_count"] == 1


async def test_another_branchs_supplied_range_is_not_listed(factory: Any) -> None:
    runs = await _history(factory, {"4": [_commit("ccc0001", ["payments/checkout.py"])]},
                          other_branch=[_commit("fff0001", ["payments/checkout.py"])])

    compare = await _compare(factory, runs)

    assert [c["sha"] for c in compare["commits"]] == ["ccc0001"]


async def test_offline_without_a_stored_supplied_range_still_says_offline_mode(factory: Any) -> None:
    runs = await _history(factory, {"5": [_commit("ddd0001", ["payments/later.py"])]})

    compare = await _compare(factory, runs)

    assert (compare["state"], compare["reason"], compare["commits"]) == ("unavailable", "offline_mode", [])


def _online_without_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _no_target(_db: Any, _run: Any) -> tuple[None, None]:
        return None, None

    async def _status(_db: Any, _project_id: Any, ci_repo: Any = None) -> tuple[str, None]:
        return "no_integration", None

    monkeypatch.setattr(bisect_service, "_resolve_target", _no_target)
    monkeypatch.setattr(bisect_service.github_target, "github_target_status", _status)


async def test_online_without_an_integration_lists_the_supplied_commits(
    factory: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same as offline: with no usable git host the stored supplied commits are the answer."""
    _online_without_integration(monkeypatch)
    runs = await _history(factory, {"4": [_commit("ccc0001", ["payments/checkout.py"])]})

    compare = await _compare(factory, runs)

    assert (compare["state"], compare["reason"]) == ("supplied", None)
    assert [c["sha"] for c in compare["commits"]] == ["ccc0001"]


async def test_online_without_an_integration_or_a_supplied_range_names_the_reason(
    factory: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _online_without_integration(monkeypatch)
    runs = await _history(factory, {"5": [_commit("ddd0001", ["payments/later.py"])]})

    compare = await _compare(factory, runs)

    assert (compare["state"], compare["reason"]) == ("unavailable", "no_integration")
