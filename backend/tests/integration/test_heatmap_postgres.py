"""VIZ-205 -- ``GET /api/v1/analytics/heatmap`` against real Postgres.

Two worlds, one frozen clock (``test_analytics_scope_postgres.FROZEN``):

* the SEEDED world of ``test_analytics_scope_postgres.py`` (VIZ-213's demo
  plan on project P1): the three rate kinds are compared, cell for cell, with
  an INDEPENDENT reducer written over the seed plan (never the SQL under test,
  never a captured golden), and ``suite_day`` is reconciled with
  ``/analytics/chart-data`` for the same scope;
* a CRAFTED world (P3, P4) built here from a table of runs a human can read,
  so the headline numbers are literals: null vs zero, a skipped-only cell, a
  live-stream run's effective suite, two runs with the same ``created_at``
  whose build labels sort the other way, hostile names in every label
  channel, an in-progress run on the partial day, and a 61-suite project for
  the row cap.

Every 200 body is validated as C3 ``chart_series`` (matrix) and its ``meta``
as C2 ``envelope``.

The router is mounted here (``bootstrap.py`` registration belongs to the
integrator); the mount is undone at the end of the module.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from tests.integration.test_analytics_scope_postgres import FROZEN, PLAN_SLUG
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: pytest finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/heatmap"
CHART = "/api/v1/analytics/chart-data"
NO_VALUE = "(none)"
XSS = '<img src=x onerror="window.__xss=1">'
LONG = "S" * 500
TODAY = FROZEN.date().isoformat()


@pytest.fixture(scope="module", autouse=True)
def mounted():
    """Serve the route from the real app (auth, scope, layer, error contract)
    until the integrator registers it, and lift the per-principal limits: the
    matrix below is a few hundred calls from one user. The limit is RAISED,
    not removed, so ``enforce_rate_limit`` still runs on every call."""
    from fastapi import Depends

    from app.core import analytics_read_layer as layer
    from app.core.deps import get_current_user_or_api_key
    from app.main import app
    from app.routers import analytics_heatmap

    before = list(app.router.routes)
    if PATH not in {getattr(route, "path", None) for route in before}:
        app.include_router(
            analytics_heatmap.router, dependencies=[Depends(get_current_user_or_api_key)]
        )
    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    patch.setitem(layer.RATE_LIMITED_ROUTES, CHART, "1000000/minute")
    yield
    patch.undo()
    app.router.routes[:] = before


@pytest.fixture(scope="module")
def plan():
    from scripts.seed_viz_data import build_viz_seed_plan

    return build_viz_seed_plan(PLAN_SLUG, FROZEN)


# ── the crafted world ──────────────────────────────────────────────────────


@dataclass
class Case:
    fp: str
    name: str
    suite: Optional[str]
    status: str  # TestStatus value


@dataclass
class Run:
    key: str
    at: datetime
    build: str
    cases: list[Case]
    environment: Optional[str] = None
    release: Optional[str] = None  # key into RELEASES
    trigger: str = "push"
    label: Optional[str] = None  # primary_suite_name
    in_progress: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def _c(fp, suite, status, name=None):
    return Case(fp, name or f"test_{fp}", suite, status)


P, F, B, S, U = "PASSED", "FAILED", "BROKEN", "SKIPPED", "UNKNOWN"
D = timedelta(days=1)
#: (key, name, version). Sort keys come from the product's own function.
RELEASES = (("rel_h", "constructor", "0.1.0"), ("rel_a", "1.0.0", "1.0.0"),
            ("rel_b", "2.0.0", "2.0.0"))


def _crafted_runs() -> list[Run]:
    tie_low, tie_high = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    at_19 = FROZEN - 2 * D - timedelta(hours=2)   # 2026-09-19 10:00
    at_20 = FROZEN - 1 * D - timedelta(hours=2)   # 2026-09-20 10:00
    tie = FROZEN - 1 * D - timedelta(hours=1)     # 2026-09-20 11:00, twice
    return [
        Run("r7", FROZEN - 20 * D, "b-700", [_c("fpt1", "Checkout", F)],
            environment="prod", release="rel_a"),
        Run("r6", FROZEN - 3 * D, "b-600", [
            _c("fph1", "constructor", P),
            _c("fph2", "__proto__", P), _c("fph3", "__proto__", P),
            _c("fph4", XSS, P), _c("fph5", XSS, P), _c("fph6", XSS, P),
            _c("fph7", LONG, P), _c("fph8", LONG, P), _c("fph9", LONG, P), _c("fph0", LONG, P),
        ], environment=XSS, release="rel_h"),
        Run("r1", at_19, "b-100", [
            _c("fpt1", "Checkout", P), _c("fpt2", "Checkout", F), _c("fpt3", "Checkout", S),
            _c("fps1", "Search", P),
        ], environment="Staging ", release="rel_a"),
        Run("r2", at_20, "b-099", [
            _c("fpt1", "Checkout", P), _c("fpt2", "Checkout", B), _c("fps1", "Search", F),
            _c("fpq1", "Quarantine", S), _c("fpq2", "Quarantine", U),
        ], environment="qa", release="rel_b"),
        # Same instant; the smaller id carries the LARGER build label.
        Run("r3", tie, "b-300", [_c("fpt2", "Checkout", F)], id=tie_low),
        Run("r4", tie, "b-200", [_c("fpt1", "Checkout", P), _c("fpt2", "Checkout", P)],
            id=tie_high),
        # A live-stream run in progress today: its rows carry the class name
        # as the suite; the effective suite is the run's label.
        Run("r5", FROZEN - timedelta(minutes=10), "b-500", [
            _c("fpl1", "com.example.ClassName", P), _c("fpl2", "com.example.ClassName", F),
        ], trigger="live_stream", label="LiveLabel", in_progress=True),
    ]


def _cap_runs() -> list[Run]:
    """61 suites; ``capNN`` has NN // 10 failures and one pass, so the worst
    rows are the HIGHEST names and a name order would keep the wrong ones."""
    cases: list[Case] = []
    for nn in range(61):
        suite = f"cap{nn:02d}"
        cases.append(_c(f"cp{nn:02d}p", suite, P))
        cases += [_c(f"cp{nn:02d}f{i}", suite, F) for i in range(nn // 10)]
    return [Run("cap", FROZEN - 5 * D, "cap-1", cases, environment="qa")]


#: 22 releases, named so that a NAME order is not the version order
#: (``v10`` sorts before ``v9`` as text).
MANY_RELEASES = tuple((f"m{i}", f"v{i}", f"{i}.0.0") for i in range(1, 23))


def _many_release_runs() -> list[Run]:
    runs = [
        Run(f"m{i}", FROZEN - 4 * D + timedelta(minutes=i), f"m-{i}",
            [_c(f"fpm{i}", "rel", P)], release=f"m{i}")
        for i in range(1, 23)
    ]
    runs.append(Run("m-none", FROZEN - 4 * D, "m-none", [_c("fpm0", "rel", P)]))
    return runs


async def _write(db, project_id, runs: list[Run], releases: dict) -> None:
    from app.models.postgres import LaunchStatus, TestCase, TestRun

    for run in runs:
        counts = {status: sum(1 for c in run.cases if c.status == status) for status in (P, F, B, S, U)}
        db.add(TestRun(
            id=run.id, project_id=project_id, build_number=run.build,
            jenkins_job=f"heatmap-{run.key}", trigger_source=run.trigger,
            environment=run.environment, ingestion_source="unknown",
            status=LaunchStatus.IN_PROGRESS if run.in_progress else LaunchStatus.PASSED,
            total_tests=len(run.cases), passed_tests=counts[P], failed_tests=counts[F],
            broken_tests=counts[B], skipped_tests=counts[S], unknown_tests=counts[U],
            primary_release_id=releases.get(run.release) if run.release else None,
            primary_suite_name=run.label, start_time=run.at, created_at=run.at,
        ))
        await db.flush()
        for case in run.cases:
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=run.id, test_fingerprint=case.fp,
                test_name=case.name, suite_name=case.suite, status=case.status,
                created_at=run.at,
            ))
        await db.flush()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def crafted(world):
    from app.core.security import create_access_token
    from app.models.postgres import Project, ProjectMember, Release, User, UserRole
    from app.services.release_sort_key import compute_sort_key

    tag = uuid.uuid4().hex[:10]
    p3, p4, p5, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    runs, cap, many = _crafted_runs(), _cap_runs(), _many_release_runs()
    releases: dict[str, uuid.UUID] = {}
    many_releases: dict[str, uuid.UUID] = {}
    try:
        async with world.sessions() as db:
            for pid, label in ((p3, "p3"), (p4, "p4"), (p5, "p5")):
                db.add(Project(id=pid, name=f"heatmap-{label}-{tag}", slug=f"heatmap-{label}-{tag}",
                               is_active=True, description=f"throwaway VIZ-205 {tag}"))
            db.add(User(id=member, email=f"heatmap-member-{tag}@example.com",
                        username=f"heatmap_member_{tag}", full_name="Heatmap member",
                        hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            for pid in (p3, p4, p5):
                db.add(ProjectMember(project_id=pid, user_id=member,
                                     role=UserRole.QA_ENGINEER.value))
            for key, name, version in RELEASES:
                rid = uuid.uuid4()
                releases[key] = rid
                db.add(Release(id=rid, project_id=p3, name=name, version=version,
                               status="active", sort_key=compute_sort_key(version, name)))
            for key, name, version in MANY_RELEASES:
                rid = uuid.uuid4()
                many_releases[key] = rid
                db.add(Release(id=rid, project_id=p5, name=name, version=version,
                               status="active", sort_key=compute_sort_key(version, name)))
            await db.flush()
            await _write(db, p3, runs, releases)
            await _write(db, p4, cap, {})
            await _write(db, p5, many, many_releases)
            await db.commit()
        yield SimpleNamespace(
            p3=p3, p4=p4, p5=p5, runs={run.key: run for run in runs}, releases=releases,
            many_releases=many_releases,
            member={"Authorization": f"Bearer {create_access_token(str(member))}"},
        )
    finally:
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b, :c)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id = :u"),
            delete(User).where(User.email == f"heatmap-member-{tag}@example.com"),
        ):
            try:
                async with world.sessions.begin() as db:
                    await db.execute(statement, {"a": p3, "b": p4, "c": p5, "u": member})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"heatmap teardown: {type(exc).__name__}: {str(exc)[:160]}")


# ── helpers ────────────────────────────────────────────────────────────────


async def _get(world, params, headers):
    return await world.client.get(PATH, params=params, headers=headers)


async def _matrix(world, params, headers) -> dict:
    resp = await _get(world, params, headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from app.models.viz_contracts import validate_contract

    validate_contract("chart_series", {k: v for k, v in body.items() if k != "meta"})
    validate_contract("envelope", body["meta"])
    assert body["kind"] == "matrix"
    return body


def _grid(body: dict) -> dict:
    """``{(y_key, x_key): cell}``."""
    return {
        (body["y_keys"][cell["y"]], body["x_keys"][cell["x"]]): cell for cell in body["cells"]
    }


def _p(project, kind, days=30, **extra):
    params = [("project_id", str(project)), ("kind", kind), ("days", str(days))]
    for key, value in extra.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        params += [(key, str(v)) for v in values]
    return params


# ── the independent reducer (over the seed plan or the crafted table) ──────


def _window_start(days: int) -> datetime:
    midnight = FROZEN.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days - 1)


@dataclass(frozen=True)
class Exec:
    at: datetime
    environment: Optional[str]
    release: str  # release id as text, or "unattributed"
    suite_key: str
    suite_label: str
    status: str


def _effective(trigger, label, suite) -> str:
    if trigger == "live_stream" and (label or "").strip():
        return label.strip()
    return (suite or "").strip()


def _exec(at, environment, release, trigger, label, suite, status) -> Exec:
    eff = _effective(trigger, label, suite) or NO_VALUE
    return Exec(at, environment, release, eff.lower(), eff, status)


def _seed_execs(plan, releases) -> list[Exec]:
    return [
        _exec(run.start_time, run.environment,
              str(releases[run.release_key]) if run.release_key else "unattributed",
              run.trigger_source, run.primary_suite_name, case.suite_name, case.status.value)
        for run in plan.runs for case in run.cases
    ]


def _crafted_execs(runs, releases) -> list[Exec]:
    return [
        _exec(run.at, run.environment,
              str(releases[run.release]) if run.release else "unattributed",
              run.trigger, run.label, case.suite, case.status)
        for run in runs for case in run.cases
    ]


def _x_key(kind: str, ex: Exec) -> str:
    if kind == "suite_day":
        return ex.at.astimezone(timezone.utc).date().isoformat()
    if kind == "suite_environment":
        return ((ex.environment or "").strip() or NO_VALUE).lower()
    return ex.release


def _expect(execs: list[Exec], kind: str, days: int, rows: int = 40) -> dict:
    """``{"rows": [...keys in order], "row_total": n, "cells": {(y, x): counts}}``."""
    start = _window_start(days)
    cells: dict[tuple, dict] = {}
    for ex in execs:
        if ex.at < start:
            continue
        acc = cells.setdefault((ex.suite_key, _x_key(kind, ex)),
                               {P: 0, F: 0, B: 0, S: 0, U: 0})
        acc[ex.status] += 1
    per_row: dict[str, list[int]] = {}
    for (y, _x), acc in cells.items():
        tot = per_row.setdefault(y, [0, 0])
        tot[0] += acc[F] + acc[B]
        tot[1] += sum(acc.values())
    order = sorted(per_row, key=lambda y: (-per_row[y][0], -per_row[y][1], y))
    return {"rows": order[:rows], "row_total": len(order), "cells": cells}


def _rate(acc: dict) -> Optional[float]:
    evaluated = acc[P] + acc[F] + acc[B]
    return None if evaluated == 0 else round(acc[P] / evaluated * 100, 2)


def _assert_matches(body: dict, want: dict) -> None:
    assert body["y_keys"] == want["rows"]
    grid = _grid(body)
    for (y, x), cell in grid.items():
        acc = want["cells"].get((y, x))
        if acc is None:
            assert cell["value"] is None and cell["n"] == 0, (y, x)
            continue
        assert cell["n"] == sum(acc.values()), (y, x)
        assert cell["counts"] == {
            "passed": acc[P], "failed": acc[F], "broken": acc[B],
            "skipped": acc[S], "unknown": acc[U],
        }, (y, x)
        assert cell["value"] == _rate(acc), (y, x)
    # Every observed pair of a kept row is on the grid (columns not cut).
    kept = set(want["rows"])
    for (y, x) in want["cells"]:
        if y in kept and x in body["x_keys"]:
            assert (y, x) in grid


# ── 1. golden: the seed, every rate kind, three windows ────────────────────


@pytest.mark.parametrize("kind", ["suite_day", "suite_environment", "suite_release"])
@pytest.mark.parametrize("days", [7, 30, 90])
async def test_the_seed_matches_the_independent_reducer(world, plan, kind, days) -> None:
    body = await _matrix(world, _p(world.p1, kind, days), world.member)
    want = _expect(_seed_execs(plan, world.releases), kind, days)
    _assert_matches(body, want)
    assert body["unit"] == "percent" and body["value_type"] == "rate"
    observed_columns = {x for (_y, x) in want["cells"]}
    if kind == "suite_day":
        assert len(body["x_keys"]) == days
        assert body["x_keys"][-1] == TODAY
    elif kind == "suite_environment":
        assert set(body["x_keys"]) == observed_columns
    else:
        assert set(body["x_keys"]) == observed_columns
        if "unattributed" in observed_columns:
            assert body["x_keys"][-1] == "unattributed"


async def test_the_seed_s_skipped_only_suite_is_null_with_its_n(world, plan) -> None:
    """QuarantinedSuite only ever skips: every cell it has is null, n > 0."""
    body = await _matrix(world, _p(world.p1, "suite_day", 30), world.member)
    grid = _grid(body)
    quarantined = [cell for (y, _x), cell in grid.items() if y == "quarantinedsuite"]
    assert quarantined, "the seed stopped seeding its skipped-only suite"
    ran = [cell for cell in quarantined if cell["n"] > 0]
    assert ran and all(cell["value"] is None for cell in ran)
    assert all(cell["counts"]["skipped"] + cell["counts"]["unknown"] == cell["n"] for cell in ran)


# ── 2. reconciliation with chart-data ──────────────────────────────────────


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_suite_day_reconciles_with_chart_data(world, days) -> None:
    """Every cell's rate equals chart-data's pass_rate for (day, suite); the
    evaluated counts equal chart-data's rate sample; ``n`` equals chart-data's
    executions. (chart-data's ``n`` for a rate is the evaluated subset; the
    matrix's ``n`` is every execution, because ``counts`` must add up to it.)"""
    body = await _matrix(world, _p(world.p1, "suite_day", days), world.member)
    base = [("project_id", str(world.p1)), ("days", str(days)),
            ("group_by", "day"), ("group_by", "suite")]
    rates = await world.client.get(CHART, params=base + [("metric", "pass_rate")],
                                   headers=world.member)
    execs = await world.client.get(CHART, params=base + [("metric", "executions")],
                                   headers=world.member)
    assert rates.status_code == 200 and execs.status_code == 200
    rate_pts = {(s["key"], p["x"]): p for s in rates.json()["series"] for p in s["points"]}
    exec_pts = {(s["key"], p["x"]): p for s in execs.json()["series"] for p in s["points"]}
    assert not any(key == "__other__" for key, _ in rate_pts), "the seed outgrew 8 suites"
    compared = 0
    for (y, x), cell in _grid(body).items():
        point = rate_pts.get((y, x))
        if point is None:  # chart-data has no series for a suite with no row
            assert cell["n"] == 0
            continue
        assert cell["value"] == point["y"], (y, x)
        evaluated = cell["counts"]["passed"] + cell["counts"]["failed"] + cell["counts"]["broken"]
        assert evaluated == point["n"], (y, x)
        assert cell["n"] == exec_pts[(y, x)]["y"], (y, x)
        compared += 1
    assert compared >= days, "nothing was reconciled"


# ── 3. the crafted world: literal numbers ──────────────────────────────────


async def test_the_headline_cells_are_the_ones_a_human_counted(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_day", 30), crafted.member)
    grid = _grid(body)
    # 09-19: passed 1, failed 1, skipped 1 -> 50%, n 3 (skipped is outside the
    # denominator: passed / executions would say 33.33).
    assert grid[("checkout", "2026-09-19")]["value"] == 50.0
    assert grid[("checkout", "2026-09-19")]["n"] == 3
    # 09-20 over three runs: passed 3, failed 1, broken 1 -> 60%, n 5.
    assert grid[("checkout", "2026-09-20")]["value"] == 60.0
    assert grid[("checkout", "2026-09-20")]["counts"] == {
        "passed": 3, "failed": 1, "broken": 1, "skipped": 0, "unknown": 0}
    # A real 0% (one failure) is 0.0; a day with no execution is null.
    assert grid[("search", "2026-09-20")]["value"] == 0.0
    assert grid[("search", "2026-09-20")]["n"] == 1
    assert grid[("search", "2026-09-21")] == {
        "x": body["x_keys"].index("2026-09-21"), "y": body["y_keys"].index("search"),
        "value": None, "n": 0,
        "counts": {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0},
    }
    # Only skipped and unknown: not measured, but it ran (n 2).
    assert grid[("quarantine", "2026-09-20")]["value"] is None
    assert grid[("quarantine", "2026-09-20")]["n"] == 2
    # Worst first: Checkout has the most failures (r7, r1, r2, r3).
    assert body["y_keys"][0] == "checkout"
    _assert_matches(body, _expect(_crafted_execs(crafted.runs.values(), crafted.releases),
                                  "suite_day", 30))


async def test_a_live_stream_run_is_read_by_its_effective_suite(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_day", 7), crafted.member)
    assert "livelabel" in body["y_keys"]
    assert body["y_labels"][body["y_keys"].index("livelabel")] == "LiveLabel"
    assert not any("classname" in key for key in body["y_keys"])
    cell = _grid(body)[("livelabel", TODAY)]
    assert cell["value"] == 50.0 and cell["n"] == 2


async def test_the_in_progress_run_and_the_partial_day_are_declared(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_day", 7), crafted.member)
    meta = body["meta"]
    assert meta["includes_in_progress"] == 1
    assert meta["partial_day"] == TODAY
    assert meta["definitions"]["partial_bucket"] == TODAY
    assert body["x_keys"][-1] == TODAY
    assert meta["pass_rate_basis"] == "executions"
    assert meta["scope"]["window"]["days"] == 7


async def test_the_window_moves_the_numbers(world, crafted) -> None:
    """r7 (20 days old) is inside 30 days and outside 7."""
    thirty = _grid(await _matrix(world, _p(crafted.p3, "suite_environment", 30), crafted.member))
    seven = _grid(await _matrix(world, _p(crafted.p3, "suite_environment", 7), crafted.member))
    assert thirty[("checkout", "prod")]["counts"]["failed"] == 1
    assert ("checkout", "prod") not in seven


async def test_environments_fold_case_and_blanks(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_environment", 30), crafted.member)
    assert "staging" in body["x_keys"]  # "Staging " was trimmed and lowered
    assert body["x_labels"][body["x_keys"].index("staging")] == "Staging"
    assert NO_VALUE in body["x_keys"]  # r3, r4, r5 carry none
    _assert_matches(body, _expect(_crafted_execs(crafted.runs.values(), crafted.releases),
                                  "suite_environment", 30))
    # The busiest environment first.
    ns = {key: sum(cell["n"] for (_y, x), cell in _grid(body).items() if x == key)
          for key in body["x_keys"]}
    assert [ns[key] for key in body["x_keys"]] == sorted(ns.values(), reverse=True)


async def test_release_columns_are_in_version_order_with_unattributed_last(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_release", 30), crafted.member)
    rel = crafted.releases
    assert body["x_keys"] == [str(rel["rel_h"]), str(rel["rel_a"]), str(rel["rel_b"]),
                              "unattributed"]
    assert body["x_labels"] == ["constructor", "1.0.0", "2.0.0", "(unattributed)"]
    _assert_matches(body, _expect(_crafted_execs(crafted.runs.values(), crafted.releases),
                                  "suite_release", 30))


async def test_named_releases_are_every_column_even_without_runs(world, crafted) -> None:
    rel = crafted.releases
    body = await _matrix(world, _p(crafted.p3, "suite_release", 7,
                                   release_id=[str(rel["rel_b"]), str(rel["rel_a"])]),
                         crafted.member)
    # rel_a's only run in 7 days is r1; rel_b's is r2. Both named, both shown,
    # in version order whatever order they were asked in.
    assert body["x_keys"] == [str(rel["rel_a"]), str(rel["rel_b"])]
    body = await _matrix(world, _p(crafted.p3, "suite_release", 2,
                                   release_id=[str(rel["rel_h"]), "unattributed"]),
                         crafted.member)
    # rel_h's run is 3 days old: its column is present and empty.
    assert body["x_keys"] == [str(rel["rel_h"]), "unattributed"]
    assert all(cell["value"] is None and cell["n"] == 0
               for cell in body["cells"] if cell["x"] == 0)


async def test_the_most_recent_releases_are_kept_and_unattributed_takes_no_slot(
    world, crafted,
) -> None:
    """22 releases and an unattributed run: the 20 highest VERSIONS are kept
    (not the first 20 names: ``v10`` sorts before ``v9`` as text), oldest
    first, then ``unattributed``; the cut is declared on the x axis."""
    body = await _matrix(world, _p(crafted.p5, "suite_release", 30), crafted.member)
    many = crafted.many_releases
    assert body["x_keys"] == [str(many[f"m{i}"]) for i in range(3, 23)] + ["unattributed"]
    assert body["x_labels"][:2] == ["v3", "v4"]
    meta = body["meta"]
    assert meta["truncated_axes"] == {"x": {"dimension": "release", "kept": 21, "total": 23}}
    assert meta["truncated_total"] == 23
    assert all(cell["value"] == 100.0 for cell in body["cells"])


async def test_hostile_names_round_trip_as_data(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "suite_environment", 30), crafted.member)
    for name in ("constructor", "__proto__", XSS, LONG):
        assert name in body["y_labels"], name[:40]
        assert name.lower() in body["y_keys"], name[:40]
    assert XSS in body["x_labels"]
    assert XSS.lower() in body["x_keys"]
    release = await _matrix(world, _p(crafted.p3, "suite_release", 30), crafted.member)
    assert release["x_labels"][0] == "constructor"
    # Hostile text is only ever a list element or a value, never an object key.
    assert "constructor" not in body["meta"]["definitions"]


# ── 4. test x run ──────────────────────────────────────────────────────────


async def test_test_run_columns_are_runs_in_time_order_ties_by_id(world, crafted) -> None:
    """M-205e: r3 and r4 share created_at; the id breaks the tie, and the
    build labels (b-300 for the smaller id) would order them the other way."""
    body = await _matrix(world, _p(crafted.p3, "test_run", 30), crafted.member)
    runs = crafted.runs
    order = ["r7", "r6", "r1", "r2", "r3", "r4", "r5"]
    assert body["x_keys"] == [str(runs[key].id) for key in order]
    assert body["x_labels"] == [runs[key].build for key in order]
    assert body["value_type"] == "status" and "unit" not in body
    # Rows: tests that failed in the window, most failures first, then fp.
    assert body["y_keys"] == ["fpt2", "fpl2", "fps1", "fpt1"]
    grid = _grid(body)
    assert grid[("fpt2", str(runs["r1"].id))]["value"] == "failed"
    assert grid[("fpt2", str(runs["r2"].id))]["value"] == "broken"
    assert grid[("fpt2", str(runs["r4"].id))]["value"] == "passed"
    assert grid[("fpt1", str(runs["r7"].id))] == {
        "x": 0, "y": 3, "value": "failed", "n": 1}
    # fpt2 has no result in r6: null, n 0.
    assert grid[("fpt2", str(runs["r6"].id))]["value"] is None
    assert grid[("fpt2", str(runs["r6"].id))]["n"] == 0
    assert body["meta"]["pass_rate_basis"] is None


async def test_test_run_keeps_the_last_runs_and_counts_the_rest(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "test_run", 30, runs=2), crafted.member)
    runs = crafted.runs
    assert body["x_keys"] == [str(runs["r4"].id), str(runs["r5"].id)]
    assert body["meta"]["truncated"] is True
    assert body["meta"]["truncated_axes"]["x"] == {"dimension": "run", "kept": 2, "total": 7}


async def test_test_run_with_a_suite_filter_shows_only_runs_of_that_suite(world, crafted) -> None:
    body = await _matrix(world, _p(crafted.p3, "test_run", 30, suite_name="search"),
                         crafted.member)
    runs = crafted.runs
    assert body["x_keys"] == [str(runs["r1"].id), str(runs["r2"].id)]
    assert body["y_keys"] == ["fps1"]


async def test_tie_breaks_are_code_point_order_whatever_the_collation(world) -> None:
    """R1-4: the row and column tie-breaks compare keys by code point
    (``COLLATE "C"``), as the coverage map and failure groups do, so the same
    data keeps the same rows on an ``en_US`` server and a ``C`` one.

    This container's ``en_US.utf8`` is musl's, i.e. byte order, so the default
    database cannot tell the two apart. For this transaction temp tables of the
    same names shadow ``test_runs`` / ``test_cases`` with the key columns in an
    ICU collation, where ``épée`` sorts before ``fence`` (by code point it is
    after: ``é`` is U+00E9). Every pair below ties on failures and executions,
    so only the tie-break orders them."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.services import heatmap_service as hs
    from app.services.analytics_scope import AnalyticsScope

    at = FROZEN - timedelta(hours=1)
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    # A connection of its own, outside the shared pool and with no statement
    # cache: a pooled connection may hold these very statements prepared
    # against the real tables (asyncpg then refuses the shadowed schema), and
    # its cache would hand the shadow's plans to the next test.
    engine = create_async_engine(
        world.engine.url.update_query_dict({"prepared_statement_cache_size": "0"}),
        poolclass=NullPool,
    )
    async with engine.connect() as conn:
        if not await conn.scalar(text(
                "SELECT count(*) FROM pg_collation WHERE collname = 'und-x-icu'")):
            pytest.skip("no ICU collation in this PostgreSQL build")
        try:
            shadow = {
                "test_runs": ("primary_suite_name", "environment"),
                "test_cases": ("suite_name", "test_fingerprint"),
            }
            for table, columns in shadow.items():
                await conn.execute(text(
                    f"CREATE TEMP TABLE {table} AS SELECT * FROM public.{table} WITH NO DATA"))
                for column in columns:
                    await conn.execute(text(
                        f'ALTER TABLE pg_temp.{table} ALTER COLUMN {column} '
                        f'TYPE text COLLATE "und-x-icu"'))
            for run_id, env, build, status in (
                    (run_a, "étage", "fx-a", "FAILED"), (run_b, "final", "fx-b", "PASSED")):
                await conn.execute(text(
                    "INSERT INTO pg_temp.test_runs "
                    "(id, project_id, build_number, trigger_source, environment, created_at) "
                    "VALUES (:id, :p, :b, 'push', :env, :at)"),
                    {"id": run_id, "p": world.p1, "b": build, "env": env, "at": at})
                for fp, suite in (("é1", "épée"), ("f1", "fence")):
                    await conn.execute(text(
                        "INSERT INTO pg_temp.test_cases "
                        "(id, test_run_id, test_fingerprint, test_name, suite_name, status, "
                        "created_at) VALUES (:id, :run, :fp, :name, :suite, :status, :at)"),
                        {"id": uuid.uuid4(), "run": run_id, "fp": fp, "name": f"test_{fp}",
                         "suite": suite, "status": status, "at": at})
            locale_order = await conn.scalar(text(
                "SELECT string_agg(k, ',' ORDER BY k) "
                "FROM (SELECT DISTINCT LOWER(suite_name) AS k FROM test_cases) s"))
            assert locale_order == "épée,fence", "precondition: the shadow must reorder"

            scope = AnalyticsScope(project_id=world.p1, allowed_project_ids=None,
                                   release_ids=(), suite_names=(), days=30)
            session = AsyncSession(bind=conn)
            got = {}
            for kind in ("suite_day", "suite_environment", "test_run"):
                spec = hs.parse_heatmap_spec(kind, None, None, scope=scope)
                got[kind] = await hs.build_heatmap(session, scope, spec, now=FROZEN)
            # Code point order: "f" (U+0066) before "é" (U+00E9).
            assert got["suite_day"]["y_keys"] == ["fence", "épée"]
            assert got["suite_environment"]["y_keys"] == ["fence", "épée"]
            assert got["suite_environment"]["x_keys"] == ["final", "étage"]
            assert got["test_run"]["y_keys"] == ["f1", "é1"]
            # The cut keeps the code-point first, too.
            one = hs.parse_heatmap_spec("suite_environment", 1, None, scope=scope)
            cut = await hs.build_heatmap(session, scope, one, now=FROZEN)
            assert cut["y_keys"] == ["fence"]
        finally:
            await conn.rollback()  # the temp tables go with the transaction
    await engine.dispose()


# ── 5. the cap ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rows", [None, 60, 3])
async def test_rows_beyond_the_cap_are_dropped_and_counted(world, crafted, rows) -> None:
    """M-205c / M-205d: the WORST rows are kept and the drop is declared."""
    extra = {} if rows is None else {"rows": rows}
    body = await _matrix(world, _p(crafted.p4, "suite_day", 30, **extra), crafted.member)
    keep = rows or 40
    want = _expect(_crafted_execs(_cap_runs(), {}), "suite_day", 30, rows=keep)
    assert body["y_keys"] == want["rows"]
    assert body["y_keys"][0] == "cap60"
    meta = body["meta"]
    assert meta["truncated"] is True
    assert meta["truncated_total"] == 61
    assert meta["truncated_axes"] == {"series": {"dimension": "suite", "kept": keep, "total": 61}}
    assert len(body["cells"]) == keep * 30


# ── 6. authorisation and refusals ──────────────────────────────────────────


async def test_a_release_in_another_project_is_refused(world) -> None:
    resp = await _get(world, _p(world.p1, "suite_release", 30, release_id=str(world.r9)),
                      world.member)
    assert resp.status_code in (403, 404), resp.text
    assert "cells" not in resp.json()


async def test_a_project_the_caller_cannot_read_is_refused(world, crafted) -> None:
    resp = await _get(world, _p(crafted.p3, "suite_day", 30), world.member)
    assert resp.status_code == 403, resp.text


async def test_all_projects_covers_only_the_caller_s_projects(world, crafted, plan) -> None:
    params = [("kind", "suite_day"), ("days", "30"), ("rows", "60")]
    member = await _matrix(world, params, world.member)
    seed_suites = {key for key in _expect(_seed_execs(plan, world.releases), "suite_day", 30)["rows"]}
    assert set(member["y_keys"]) == seed_suites  # nothing of P3/P4
    mine = await _matrix(world, params, crafted.member)
    assert "checkout" in mine["y_keys"] and "checkoutsuite" not in mine["y_keys"]
    admin = await _matrix(world, params, world.admin)
    assert {"checkout", "checkoutsuite"} <= set(admin["y_keys"])


@pytest.mark.parametrize("kind", ["test_run", "suite_release"])
async def test_the_one_project_kinds_refuse_all_projects(world, kind) -> None:
    resp = await _get(world, [("kind", kind), ("days", "30")], world.admin)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "project_required" and body["param"] == "project_id"


@pytest.mark.parametrize("params,code", [
    ([("kind", "suite_day'; DROP TABLE test_runs; --")], "kind_enum"),
    ([("kind", "suite_day"), ("days", "91")], "window_cap"),
    ([("kind", "suite_day"), ("days", "366")], "window_days_range"),
    ([("kind", "suite_day"), ("rows", "61")], "rows_range"),
    ([("kind", "suite_environment"), ("runs", "5")], "runs_unsupported"),
    ([("kind", "test_run"), ("runs", "91")], "runs_range"),
    ([], "missing_parameter"),
])
async def test_refusals_are_viz_210_bodies_that_echo_nothing(world, params, code) -> None:
    resp = await _get(world, [("project_id", str(world.p1))] + params, world.member)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == code
    assert "request_id" in body
    assert "DROP TABLE" not in resp.text
    # The database is untouched and the next call works.
    assert (await _get(world, _p(world.p1, "suite_day", 7), world.member)).status_code == 200


# ── 7. the read layer ──────────────────────────────────────────────────────


async def test_a_repeat_is_a_hit_and_an_epoch_bump_is_a_miss(world, crafted) -> None:
    from app.services.cache_service import bump_analytics_epoch

    params = _p(crafted.p3, "suite_day", 14)
    first = await _get(world, params, crafted.member)
    second = await _get(world, params + [("rows", "40")], crafted.member)
    assert first.status_code == second.status_code == 200
    if first.headers.get("X-Analytics-Cache") != "miss":
        pytest.skip("the cache is not reachable in this environment")
    # rows=40 is the default: the same identity, so the same entry and ETag.
    assert second.headers["X-Analytics-Cache"] == "hit"
    assert second.headers["ETag"] == first.headers["ETag"]
    not_modified = await world.client.get(
        PATH, params=params, headers={**crafted.member, "If-None-Match": first.headers["ETag"]})
    assert not_modified.status_code == 304
    await bump_analytics_epoch(str(crafted.p3))
    after = await _get(world, params, crafted.member)
    assert after.headers["X-Analytics-Cache"] == "miss"
    assert after.headers["ETag"] != first.headers["ETag"]


async def test_the_route_s_own_limit_answers_429_with_retry_after(
    world, crafted, monkeypatch,
) -> None:
    """The route's limit is the registry's entry for its path (60/minute in
    production), charged per principal before anything else runs."""
    from app.core import analytics_read_layer as layer

    monkeypatch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "2/minute")
    params = _p(crafted.p3, "suite_day", 3)
    codes = [(await _get(world, params, crafted.member)).status_code for _ in range(3)]
    assert codes == [200, 200, 429], codes
    refused = await _get(world, params, crafted.member)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.json()["code"] == "rate_limited"
    # Another principal has its own budget.
    assert (await _get(world, _p(world.p1, "suite_day", 3), world.member)).status_code == 200
