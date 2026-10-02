"""VIZ-506-BE -- ``GET /api/v1/analytics/test-scatter`` against real Postgres.

The seeded world is ``test_analytics_scope_postgres.py``'s (frozen clock), plus
a suite this module writes BEFORE any request, holding exactly one test of
each kind the endpoint must treat differently:

=====================  ===========================================  ==============================
test                   executions                                   expected
=====================  ===========================================  ==============================
``ok``                 4 passed, 1 failed, 1 skipped; 6 durations   a point: y = 1/5 = 20%, n = 5
``submilli``           6 passed, every duration 0 ms                a point at x = 1 (the floor)
``few``                3 passed                                     excluded: below_min_executions
``few_skipped``        2 skipped                                    excluded: below_min (first)
``skipped_only``       6 skipped                                    excluded: no_evaluated
``untimed``            6 passed, no duration                        excluded: no_duration
``hostile``            5 failed, markup as its name                 a point: y = 100%
=====================  ===========================================  ==============================

Expectations come from ``_expect``, an independent reducer over the SEED PLAN
plus those rows (``percentile_cont`` re-implemented in Python), never from the
SQL under test.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio

from tests.integration.test_analytics_scope_postgres import FROZEN, PLAN_SLUG, R1_KEY
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/test-scatter"
SUITE = "ScatterSuite"
HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

#: name -> per-run (status, duration) for six runs; ``None`` = no execution.
DESIGN = {
    "ok": [("PASSED", 10), ("PASSED", 20), ("PASSED", 30), ("PASSED", 40), ("FAILED", 1000),
           ("SKIPPED", 50)],
    "submilli": [("PASSED", 0)] * 6,
    "few": [("PASSED", 5)] * 3 + [None] * 3,
    "few_skipped": [("SKIPPED", 5)] * 2 + [None] * 4,
    "skipped_only": [("SKIPPED", 7)] * 6,
    "untimed": [("PASSED", None)] * 6,
    "hostile": [("FAILED", 300)] * 5 + [None],
}


@pytest.fixture(scope="module")
def plan():
    from scripts.seed_viz_data import build_viz_seed_plan

    return build_viz_seed_plan(PLAN_SLUG, FROZEN)


@pytest.fixture(scope="module", autouse=True)
def unthrottled():
    from app.core import analytics_read_layer as layer

    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    yield
    patch.undo()


@pytest.fixture(scope="module", autouse=True)
def mounted():
    """Mounted here until the integrator registers it in ``bootstrap.py``."""
    from app.main import app
    from app.routers.analytics_test_scatter import router

    present = any(getattr(route, "path", None) == PATH for route in app.router.routes)
    before = list(app.router.routes)
    if not present:
        app.include_router(router)
    yield
    if not present:
        app.router.routes[:] = before


def _fingerprint(name: str, tag: str) -> str:
    return f"scatter-{name}-{tag}"[:64]


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def designed(world):
    """The designed suite: six runs, three days back, inside every window."""
    from app.models.postgres import LaunchStatus, TestCase, TestRun, TestStatus

    start = FROZEN - timedelta(days=3)
    async with world.sessions() as db:
        for index in range(6):
            run_id = uuid.uuid4()
            db.add(TestRun(
                id=run_id, project_id=world.p1, build_number=f"scatter-{world.tag}-{index}",
                jenkins_job="scatter", status=LaunchStatus.FAILED, ingestion_source="upload",
                total_tests=0, primary_suite_name=SUITE,
                created_at=start + timedelta(minutes=index),
            ))
            await db.flush()
            for name, executions in DESIGN.items():
                if executions[index] is None:
                    continue
                status, duration = executions[index]
                db.add(TestCase(
                    id=uuid.uuid4(), test_run_id=run_id,
                    test_fingerprint=_fingerprint(name, world.tag),
                    test_name=HOSTILE_NAME if name == "hostile" else f"scatter_{name}",
                    suite_name=SUITE, status=TestStatus(status), duration_ms=duration,
                ))
        await db.commit()


# ── the independent reducer ────────────────────────────────────────────────


def _percentile_cont(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _executions(plan, world, days: int, release=None, suite=None):
    """``(fingerprint, name, status, duration)`` for every execution in scope:
    the seed plan's, then the designed suite's."""
    midnight = FROZEN.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=days - 1)
    out = []
    for run in plan.runs:
        if run.start_time < start:
            continue
        if release is not None and run.release_key != release:
            continue
        for case in run.cases:
            effective = (
                run.primary_suite_name.strip()
                if run.trigger_source == "live_stream" and (run.primary_suite_name or "").strip()
                else (case.suite_name or "").strip()
            )
            if suite is not None and effective.lower() != suite.lower():
                continue
            out.append((case.test_fingerprint, case.test_name, case.status.value, case.duration_ms))
    if release is None and (suite is None or suite.lower() == SUITE.lower()):
        for name, executions in DESIGN.items():
            for item in executions:
                if item is not None:
                    out.append((
                        _fingerprint(name, world.tag),
                        HOSTILE_NAME if name == "hostile" else f"scatter_{name}",
                        item[0], item[1],
                    ))
    return out


def _expect(executions, min_executions=5) -> dict:
    tests: dict[str, dict] = {}
    for fp, name, status, duration in executions:
        test = tests.setdefault(fp, {"names": [], "statuses": [], "durations": []})
        test["names"].append(name)
        test["statuses"].append(status)
        if duration is not None:
            test["durations"].append(duration)
    points, excluded = {}, {"below_min_executions": 0, "no_evaluated": 0, "no_duration": 0}
    for fp, test in tests.items():
        statuses = test["statuses"]
        evaluated = sum(1 for s in statuses if s in ("PASSED", "FAILED", "BROKEN"))
        bad = sum(1 for s in statuses if s in ("FAILED", "BROKEN"))
        if len(statuses) < min_executions:
            excluded["below_min_executions"] += 1
        elif evaluated == 0:
            excluded["no_evaluated"] += 1
        elif not test["durations"]:
            excluded["no_duration"] += 1
        else:
            points[fp] = {
                "label": min(test["names"]),
                "x": max(round(_percentile_cont(test["durations"], 0.95), 2), 1.0),
                "y": round(bad / evaluated * 100, 2),
                "size": len(statuses),
                "n": evaluated,
                "bad": bad,
            }
    return {"points": points, "excluded": excluded}


async def _scatter(world, params, headers=None, status=200) -> dict:
    resp = await world.client.get(PATH, params=params, headers=headers or world.member)
    assert resp.status_code == status, resp.text
    body = resp.json()
    if status == 200:
        from app.models.viz_contracts import validate_contract

        validate_contract("chart_series", {k: v for k, v in body.items() if k != "meta"})
        validate_contract("envelope", body["meta"])
    return body


def _base(world, days=30, **extra):
    params = [("project_id", str(world.p1)), ("days", str(days))]
    return params + [(key, str(value)) for key, value in extra.items()]


def _assert_matches(body: dict, want: dict) -> None:
    got = {point["id"]: point for point in body["points"]}
    assert set(got) == set(want["points"])
    for fp, point in want["points"].items():
        for key in ("label", "y", "size", "n"):
            assert got[fp][key] == point[key], (fp, key, got[fp], point)
        assert got[fp]["x"] == pytest.approx(point["x"], abs=0.01), fp
    assert body["excluded"] == want["excluded"]
    if want["points"]:
        assert body["medians"]["x"] == pytest.approx(
            statistics.median(p["x"] for p in want["points"].values()), abs=0.011)
        assert body["medians"]["y"] == pytest.approx(
            statistics.median(p["y"] for p in want["points"].values()), abs=0.011)
    else:
        assert "medians" not in body


# ── the designed suite: one test of each kind ──────────────────────────────


async def test_each_kind_of_test_is_placed_or_counted_once(world) -> None:
    body = await _scatter(world, _base(world, suite_name=SUITE))
    by_label = {point["label"]: point for point in body["points"]}
    assert set(by_label) == {"scatter_ok", "scatter_submilli", HOSTILE_NAME}
    ok = by_label["scatter_ok"]
    # M-506a: the skipped execution is outside the denominator: 1/5, not 1/6.
    assert (ok["y"], ok["n"], ok["size"]) == (20.0, 5, 6)
    # p95 of [10, 20, 30, 40, 50, 1000]: 50 + 0.75 x 950.
    assert ok["x"] == 762.5
    # M-506e: a duration of 0 sits on the log axis floor, not at 0.
    assert by_label["scatter_submilli"]["x"] == 1.0
    assert by_label[HOSTILE_NAME]["y"] == 100.0
    # M-506b: every test left out is counted, once, under its first reason.
    assert body["excluded"] == {"below_min_executions": 2, "no_evaluated": 1, "no_duration": 1}
    assert body["meta"]["definitions"]["x_floor"]


async def test_min_executions_moves_tests_between_the_plot_and_the_count(world) -> None:
    body = await _scatter(world, _base(world, suite_name=SUITE, min_executions=1))
    labels = {point["label"] for point in body["points"]}
    assert "scatter_few" in labels  # 3 executions now enough
    assert body["excluded"] == {"below_min_executions": 0, "no_evaluated": 2, "no_duration": 1}
    body = await _scatter(world, _base(world, suite_name=SUITE, min_executions=7))
    assert body["points"] == [] and "medians" not in body
    assert body["excluded"]["below_min_executions"] == 7


# ── golden: the whole project against the reducer ──────────────────────────


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_the_project_scatter_matches_the_reducer(world, plan, days) -> None:
    body = await _scatter(world, _base(world, days=days))
    want = _expect(_executions(plan, world, days))
    assert want["points"], "the seed stopped seeding"
    _assert_matches(body, want)
    assert body["meta"]["truncated"] is False


async def test_a_release_scope_bounds_the_points(world, plan) -> None:
    body = await _scatter(world, _base(world, days=90, release_id=str(world.releases[R1_KEY])))
    _assert_matches(body, _expect(_executions(plan, world, 90, release=R1_KEY)))


async def test_a_suite_filter_uses_the_effective_suite(world, plan) -> None:
    from tests.integration.test_analytics_scope_postgres import S1

    body = await _scatter(world, _base(world, days=90, suite_name=S1.upper()))
    _assert_matches(body, _expect(_executions(plan, world, 90, suite=S1)))


@pytest.mark.parametrize("order", ["failures", "volume"])
async def test_limit_keeps_the_ranked_tests_and_counts_the_rest(world, plan, order) -> None:
    want = _expect(_executions(plan, world, 30))["points"]
    key = (
        (lambda item: (-item[1]["bad"], -item[1]["size"], item[0])) if order == "failures"
        else (lambda item: (-item[1]["size"], -item[1]["bad"], item[0]))
    )
    ranked = [fp for fp, _ in sorted(want.items(), key=key)]
    body = await _scatter(world, _base(world, limit=3, order=order))
    assert [point["id"] for point in body["points"]] == ranked[:3]
    assert body["meta"]["truncated"] is True
    assert body["meta"]["truncated_total"] == len(ranked)


# ── scope, authorisation, refusals ─────────────────────────────────────────


async def test_all_projects_is_refused(world) -> None:
    resp = await world.client.get(PATH, params=[("days", "30")], headers=world.member)
    assert resp.status_code == 422, resp.text


async def test_a_project_the_caller_cannot_read_is_refused(world) -> None:
    await _scatter(world, _base(world), headers=world.outsider, status=403)


async def test_a_release_in_another_project_is_refused(world) -> None:
    body = await _scatter(world, _base(world, release_id=str(world.r9)), status=403)
    assert "points" not in body


@pytest.mark.parametrize("params,code", [
    ([("order", "'; DROP TABLE test_cases; --")], "order_enum"),
    ([("limit", "5001")], "limit_range"),
    ([("min_executions", "0")], "min_executions_range"),
    ([("days", "366")], "window_days_range"),
])
async def test_refusals_are_contract_bodies(world, params, code) -> None:
    sent = {key for key, _ in params}
    base = [item for item in _base(world) if item[0] not in sent]
    body = await _scatter(world, base + params, status=422)
    assert body["code"] == code
    assert "DROP TABLE" not in body["message"]


async def test_365_days_is_accepted_for_a_direct_caller(world) -> None:
    body = await _scatter(world, _base(world, days=365))
    assert body["points"]
