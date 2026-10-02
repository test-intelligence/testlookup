"""VIZ-208 -- ``GET /api/v1/analytics/chart-data/rows`` against real Postgres.

The seeded world is ``test_analytics_scope_postgres.py``'s (VIZ-213's demo plan
on a throwaway project, frozen clock), plus a handful of rows this module adds
BEFORE any request (so no cached answer predates them): two runs either side of
a UTC midnight, two runs with the same ``created_at``, and a suite whose name is
hostile markup.

What is proved here:

1. **The reconciliation property.** For every chart-data metric and every
   dimension, for EVERY bucket the chart drew, the rows endpoint's
   ``reconciliation.value`` equals the mark's ``y`` (counts) or ``n`` (rates,
   durations), and the rows of all buckets add up to the chart's total. This is
   "rows select exactly the executions behind the mark", checked mark by mark
   against the live chart -- not a captured golden.
2. **The same under a scope**: a release filter and a suite filter bound the
   rows exactly as they bound the chart (M-208b).
3. **The day boundary is UTC** (M-208a): a run at 23:59:59 and one at 00:00:00
   fall in different day buckets, on the chart and in the rows.
4. **Order and paging**: newest run first, ties broken by the case id; pages
   partition the bucket.
5. **Authorisation, refusals, hostile names, the envelope.**
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from tests.integration.test_analytics_scope_postgres import FROZEN, R1_KEY, S1
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

CHART = "/api/v1/analytics/chart-data"
ROWS = "/api/v1/analytics/chart-data/rows"
OTHER = "__other__"

HOSTILE_SUITE = '<img src=x onerror="window.__xss=1">'
BOUNDARY_SUITE = "BoundarySuite"
TIE_SUITE = "TieSuite"
#: Ten days back: inside the 30-day window, on a whole UTC day.
BOUNDARY_DAY = (FROZEN - timedelta(days=10)).date()

METRICS = (
    "executions", "passed", "failed", "broken", "skipped", "unknown", "failures",
    "retried_tests", "pass_rate", "failure_rate", "flaky_tests", "unique_tests",
    "run_count", "duration_p50", "duration_p95", "duration_total",
)
DIMENSIONS = (
    "day", "week", "release", "branch", "environment", "ingestion_source",
    "suite", "status", "failure_category",
)


@pytest.fixture(scope="module", autouse=True)
def unthrottled():
    """Several thousand calls from one user: the limits are RAISED, not removed,
    so ``enforce_rate_limit`` still runs on every call."""
    from app.core import analytics_read_layer as layer

    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, CHART, "10000000/minute")
    patch.setitem(layer.RATE_LIMITED_ROUTES, ROWS, "10000000/minute")
    yield
    patch.undo()


@pytest.fixture(scope="module", autouse=True)
def mounted():
    """The integrator registers the router in ``bootstrap.py``; until then
    (and so this module never depends on that) it is mounted here for the
    module and removed afterwards."""
    from app.main import app
    from app.routers.analytics_chart_rows import router

    present = any(getattr(route, "path", None) == ROWS for route in app.router.routes)
    before = list(app.router.routes)
    if not present:
        app.include_router(router)
    yield
    if not present:
        app.router.routes[:] = before


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def extra_rows(world):
    """Rows the seed does not have, written before any request is made."""
    from app.models.postgres import LaunchStatus, TestCase, TestRun, TestStatus

    midnight = datetime(BOUNDARY_DAY.year, BOUNDARY_DAY.month, BOUNDARY_DAY.day,
                        tzinfo=timezone.utc) + timedelta(days=1)
    tie = midnight - timedelta(hours=6)
    runs = [
        # (suite, created_at, statuses)
        (BOUNDARY_SUITE, midnight - timedelta(seconds=1), ("FAILED",)),
        (BOUNDARY_SUITE, midnight, ("FAILED", "PASSED")),
        (TIE_SUITE, tie, ("PASSED", "FAILED", "BROKEN")),
        (TIE_SUITE, tie, ("PASSED", "SKIPPED")),
        (HOSTILE_SUITE, tie, ("FAILED", "PASSED")),
    ]
    ids: dict = {"boundary": [], "tie": []}
    async with world.sessions() as db:
        for index, (suite, created, statuses) in enumerate(runs):
            run_id = uuid.uuid4()
            counts = {status: statuses.count(status) for status in set(statuses)}
            db.add(TestRun(
                id=run_id, project_id=world.p1, build_number=f"rows-{world.tag}-{index}",
                jenkins_job="rows", status=LaunchStatus.FAILED, ingestion_source="upload",
                total_tests=len(statuses), passed_tests=counts.get("PASSED", 0),
                failed_tests=counts.get("FAILED", 0), broken_tests=counts.get("BROKEN", 0),
                skipped_tests=counts.get("SKIPPED", 0), unknown_tests=0,
                primary_suite_name=suite, created_at=created,
            ))
            await db.flush()
            for n, status in enumerate(statuses):
                db.add(TestCase(
                    id=uuid.uuid4(), test_run_id=run_id,
                    test_fingerprint=f"rows-{index}-{n}-{world.tag}"[:64],
                    test_name=f"{suite} case {n}", suite_name=suite,
                    status=TestStatus(status), duration_ms=10 + n,
                    error_message=("Boom 1\nsecond line" if status in ("FAILED", "BROKEN") else None),
                ))
            ids["boundary" if suite == BOUNDARY_SUITE else "tie"].append(run_id)
        await db.commit()
    return ids


# ── helpers ────────────────────────────────────────────────────────────────


def _base(world, days=30, **extra) -> list[tuple[str, str]]:
    params = [("project_id", str(world.p1)), ("days", str(days))]
    params += [(key, str(value)) for key, value in extra.items()]
    return params


async def _chart(world, params, headers=None) -> dict:
    resp = await world.client.get(CHART, params=params, headers=headers or world.member)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _rows(world, params, headers=None, status=200) -> dict:
    resp = await world.client.get(ROWS, params=params, headers=headers or world.member)
    assert resp.status_code == status, resp.text
    body = resp.json()
    if status == 200:
        from app.models.viz_contracts import validate_contract

        validate_contract("envelope", body["meta"])
    return body


def _marks(chart: dict):
    """``(series_key, x, point)`` for every mark the chart drew, except the
    roll-ups (not one bucket)."""
    for series in chart["series"]:
        if series["key"] == OTHER:
            continue
        for point in series["points"]:
            if point["x"] == OTHER:
                continue
            yield series["key"], point["x"], point


def _selectors(dimensions, series_key, x) -> list[tuple[str, str]]:
    out = [(f"bucket_{dimensions[0]}", x)]
    if len(dimensions) == 2:
        out.append((f"bucket_{dimensions[1]}", series_key))
    return out


async def _reconcile(world, scope: list[tuple[str, str]], metric: str, dimensions: tuple,
                     extra: list[tuple[str, str]] = ()) -> int:
    """Every mark of one chart against its rows. Returns the marks checked."""
    chart_params = scope + [("metric", metric)] + [("group_by", d) for d in dimensions] + list(extra)
    chart = await _chart(world, chart_params)
    checked = 0
    rows_sum = 0
    mark_sum = 0
    for series_key, x, point in _marks(chart):
        if point["n"] == 0 and point["y"] in (0, None):
            continue  # an empty, zero-filled bucket: nothing to drill into
        body = await _rows(world, chart_params + _selectors(dimensions, series_key, x) + [("size", "1")])
        rec = body["reconciliation"]
        field = rec["mark_field"]
        want = point[field]
        assert rec["value"] == want, (metric, dimensions, series_key, x, rec, point)
        assert body["meta"]["definitions"]["chart_grain"] == chart["meta"]["definitions"]["grain"]
        if rec["measure"] == "rows":
            rows_sum += body["total"]
            mark_sum += want
        checked += 1
    assert rows_sum == mark_sum
    return checked


# ── 1. the reconciliation property ─────────────────────────────────────────


@pytest.mark.parametrize("dimension", DIMENSIONS)
@pytest.mark.parametrize("metric", METRICS)
async def test_every_mark_s_rows_reconcile_with_the_mark(world, metric, dimension) -> None:
    checked = await _reconcile(world, _base(world), metric, (dimension,))
    assert checked > 0, "no mark was checked: the property is vacuous here"


@pytest.mark.parametrize("dimensions", [
    ("suite", "status"),       # the drill ladder's L0 (stacked bars)
    ("day", "suite"),
    ("day", "status"),
    ("release", "environment"),
    ("failure_category", "suite"),
])
@pytest.mark.parametrize("metric", ("executions", "failures", "pass_rate", "duration_p95",
                                    "unique_tests", "run_count"))
async def test_two_dimension_marks_reconcile(world, metric, dimensions) -> None:
    assert await _reconcile(world, _base(world), metric, dimensions) > 0


async def test_a_test_mark_reconciles_inside_a_suite(world) -> None:
    """The ladder's L1: ``metric=failed&group_by=test&top_n=20&suite_name=...``."""
    scope = _base(world, days=90) + [("suite_name", S1)]
    assert await _reconcile(world, scope, "failed", ("test",), [("top_n", "20")]) > 0
    assert await _reconcile(world, scope, "duration_p95", ("test",)) > 0


async def test_the_rows_of_every_bucket_add_up_to_the_chart_total(world) -> None:
    """Summed over buckets, the rows ARE the chart: nothing double counted,
    nothing between buckets."""
    chart = await _chart(world, _base(world) + [("metric", "executions"), ("group_by", "suite")])
    total = sum(point["y"] for _, _, point in _marks(chart))
    rows = 0
    for _, x, _ in _marks(chart):
        rows += (await _rows(world, _base(world) + [
            ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", x), ("size", "1"),
        ]))["total"]
    assert rows == total > 0


# ── 2. the same under a scope (M-208b) ─────────────────────────────────────


async def test_a_release_scope_bounds_the_rows_as_it_bounds_the_chart(world) -> None:
    scope = _base(world, days=90) + [("release_id", str(world.releases[R1_KEY]))]
    assert await _reconcile(world, scope, "executions", ("day",)) > 0
    assert await _reconcile(world, scope, "failures", ("suite",)) > 0
    # ...and the release-scoped rows are fewer than the unscoped ones.
    chart = await _chart(world, _base(world, days=90) + [("metric", "executions"), ("group_by", "suite")])
    _, x, _ = next(iter(_marks(chart)))
    unscoped = await _rows(world, _base(world, days=90) + [
        ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", x)])
    scoped = await _rows(world, scope + [
        ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", x)])
    assert scoped["total"] < unscoped["total"]
    run_ids = {item["run_id"] for item in scoped["items"]}
    for item in scoped["items"]:
        assert item["release"] and item["release"]["id"] == str(world.releases[R1_KEY]), run_ids


async def test_a_suite_scope_bounds_the_rows(world) -> None:
    scope = _base(world) + [("suite_name", S1)]
    assert await _reconcile(world, scope, "executions", ("day",)) > 0
    assert await _reconcile(world, scope, "pass_rate", ("status",)) > 0


async def test_the_unattributed_release_bucket_selects_runs_with_no_release(world) -> None:
    body = await _rows(world, _base(world) + [
        ("metric", "executions"), ("group_by", "release"), ("bucket_release", "unattributed"),
        ("size", "200"),
    ])
    assert body["total"] > 0
    assert all(item["release"] is None for item in body["items"])


# ── 3. the UTC day boundary (M-208a) ───────────────────────────────────────


async def test_a_run_at_23_59_59_and_one_at_midnight_are_different_days(world) -> None:
    scope = _base(world) + [("suite_name", BOUNDARY_SUITE)]
    day = BOUNDARY_DAY.isoformat()
    next_day = (BOUNDARY_DAY + timedelta(days=1)).isoformat()
    before = await _rows(world, scope + [
        ("metric", "executions"), ("group_by", "day"), ("bucket_day", day)])
    after = await _rows(world, scope + [
        ("metric", "executions"), ("group_by", "day"), ("bucket_day", next_day)])
    assert before["total"] == 1
    assert before["items"][0]["created_at"].startswith(f"{day}T23:59:59")
    assert after["total"] == 2
    assert all(item["created_at"].startswith(f"{next_day}T00:00:00") for item in after["items"])
    # ...exactly what the chart drew for the two days.
    chart = await _chart(world, scope + [("metric", "executions"), ("group_by", "day")])
    by_day = {point["x"]: point["y"] for point in chart["series"][0]["points"]}
    assert (by_day[day], by_day[next_day]) == (1, 2)


# ── 4. order and paging ────────────────────────────────────────────────────


async def test_rows_are_newest_first_with_ties_broken_by_the_case_id(world) -> None:
    body = await _rows(world, _base(world) + [("suite_name", TIE_SUITE)] + [
        ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", TIE_SUITE.lower()),
        ("size", "200"),
    ])
    items = body["items"]
    assert body["total"] == len(items) == 5
    keys = [(item["created_at"], uuid.UUID(item["id"])) for item in items]
    assert keys == sorted(keys, reverse=True)
    assert len({item["created_at"] for item in items}) == 1, "the fixture is a tie"
    # The tie-break decides which rows a PAGE holds, not only how one page is
    # sorted: walked two at a time, the pages are the same sequence.
    paged: list[str] = []
    for page in range(1, 4):
        body = await _rows(world, _base(world) + [("suite_name", TIE_SUITE)] + [
            ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", TIE_SUITE.lower()),
            ("size", "2"), ("page", str(page)),
        ])
        paged += [item["id"] for item in body["items"]]
    assert paged == [item["id"] for item in items]


async def test_pages_partition_the_bucket(world) -> None:
    params = _base(world, days=90) + [
        ("metric", "executions"), ("group_by", "status"), ("bucket_status", "passed")]
    whole = await _rows(world, params + [("size", "200")])
    assert whole["total"] > 30
    seen: list[str] = []
    for page in range(1, 4):
        body = await _rows(world, params + [("size", "10"), ("page", str(page))])
        assert body["total"] == whole["total"]
        assert body["pages"] == -(-whole["total"] // 10)
        seen += [item["id"] for item in body["items"]]
    assert seen == [item["id"] for item in whole["items"][:30]]


async def test_a_page_past_the_end_is_empty_and_keeps_its_total(world) -> None:
    body = await _rows(world, _base(world) + [("suite_name", TIE_SUITE)] + [
        ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", TIE_SUITE.lower()),
        ("page", "9"), ("size", "50"),
    ])
    assert body["items"] == [] and body["total"] == 5


# ── 5. fields, hostile names, authorisation, refusals ──────────────────────


async def test_a_hostile_suite_name_round_trips_as_data(world) -> None:
    key = HOSTILE_SUITE.lower()
    chart = await _chart(world, _base(world) + [("metric", "failures"), ("group_by", "suite")])
    assert key in {x for _, x, _ in _marks(chart)}
    body = await _rows(world, _base(world) + [
        ("metric", "failures"), ("group_by", "suite"), ("bucket_suite", key)])
    assert body["total"] == 1
    (item,) = body["items"]
    assert item["suite"] == HOSTILE_SUITE
    assert item["test_name"] == f"{HOSTILE_SUITE} case 0"
    assert item["status"] == "failed"
    assert item["error_line"] == "Boom 1"
    assert item["duration_ms"] == 10


async def test_failures_rows_are_the_failed_and_broken_ones(world) -> None:
    params = _base(world) + [("suite_name", TIE_SUITE), ("group_by", "suite"),
                             ("bucket_suite", TIE_SUITE.lower()), ("size", "200")]
    failures = await _rows(world, params + [("metric", "failures")])
    assert sorted(item["status"] for item in failures["items"]) == ["broken", "failed"]
    rate = await _rows(world, params + [("metric", "pass_rate")])
    assert rate["total"] == 4, "the skipped execution is outside the rate's denominator"


async def test_a_member_s_all_projects_drill_sees_only_their_projects(world) -> None:
    body = await _rows(world, [
        ("days", "30"), ("metric", "executions"), ("group_by", "project"),
        ("bucket_project", str(world.p2)),
    ])
    assert body["total"] == 0, "the P2 run leaked into a P1 member's drill"
    mine = await _rows(world, [
        ("days", "30"), ("metric", "executions"), ("group_by", "project"),
        ("bucket_project", str(world.p1)),
    ])
    assert mine["total"] > 0
    outsider = await _rows(world, [
        ("days", "30"), ("metric", "executions"), ("group_by", "project"),
        ("bucket_project", str(world.p1)),
    ], headers=world.outsider)
    assert outsider["total"] == 0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def foreign_row(world):
    """One failing execution on P2's run of release R9. The seeded world gives
    that run no test case, so without this a drill that dropped the tenant
    fragment would ALSO select 0 rows and a "foreign id -> 0" test would pass
    vacuously (R1-3)."""
    from sqlalchemy import select, text

    from app.models.postgres import TestCase, TestRun

    case_id = uuid.uuid4()
    async with world.sessions() as db:
        run_id = (await db.execute(
            select(TestRun.id).where(TestRun.primary_release_id == world.r9)
        )).scalar_one()
        db.add(TestCase(
            id=case_id, test_run_id=run_id, test_fingerprint="r1-3-foreign",
            test_name="foreign tenant case", suite_name=S1, status="FAILED",
            error_message="ForeignError: not yours", created_at=FROZEN,
        ))
        await db.commit()
    try:
        yield case_id
    finally:
        async with world.sessions.begin() as db:
            await db.execute(text("DELETE FROM test_cases WHERE id = :id"), {"id": case_id})


async def test_a_member_s_drill_on_a_foreign_release_selects_nothing(world, foreign_row) -> None:
    """R1-3: ``bucket_release`` / ``bucket_project`` are selectors, not scope:
    ``analytics_scope`` never authorises them. What keeps another tenant's
    rows out is the tenant fragment every rows statement ANDs. A P1 member
    naming P2's release (or P2 itself) gets an empty page, not a 403 and not
    P2's row; the admin, who may read P2, gets the row (the control)."""
    selector = [("metric", "executions"), ("group_by", "release"),
                ("bucket_release", str(world.r9))]
    pinned = await _rows(world, [("project_id", str(world.p1)), ("days", "30")] + selector)
    assert pinned["total"] == 0 and pinned["items"] == []
    everywhere = await _rows(world, [("days", "30")] + selector)
    assert everywhere["total"] == 0 and everywhere["items"] == []
    by_project = await _rows(world, [
        ("days", "30"), ("metric", "failures"), ("group_by", "project"),
        ("bucket_project", str(world.p2)),
    ])
    assert by_project["total"] == 0
    admin = await _rows(world, [("project_id", str(world.p2)), ("days", "30")] + selector,
                        headers=world.admin)
    assert admin["total"] == 1
    assert admin["items"][0]["test_name"] == "foreign tenant case"


async def test_a_release_in_another_project_is_refused(world) -> None:
    body = await _rows(world, _base(world) + [
        ("release_id", str(world.r9)), ("metric", "executions"), ("group_by", "day"),
        ("bucket_day", BOUNDARY_DAY.isoformat()),
    ], status=403)
    assert body["code"] in ("forbidden", "not_found") and "items" not in body


async def test_a_project_the_caller_cannot_read_is_refused(world) -> None:
    await _rows(world, [
        ("project_id", str(world.p2)), ("metric", "executions"), ("group_by", "day"),
        ("bucket_day", BOUNDARY_DAY.isoformat()),
    ], status=403)


@pytest.mark.parametrize("params,code", [
    ([("group_by", "day")], "missing_parameter"),
    ([("group_by", "day"), ("bucket_suite", "x")], "selector_not_in_group_by"),
    ([("group_by", "status"), ("bucket_status", "<b>FAILED</b>")], "status_vocab"),
    ([("group_by", "suite"), ("bucket_suite", "x"), ("page", "202")], "page_cap"),
    ([("group_by", "suite"), ("bucket_suite", "x"), ("size", "201")], "size_range"),
    ([("group_by", "suite"), ("bucket_suite", "x"), ("metric", "'; DROP TABLE x; --")],
     "metric_enum"),
    ([("group_by", "day"), ("bucket_day", "2026-13-01")], "bucket_value"),
])
async def test_refusals_are_contract_bodies_that_never_echo(world, params, code) -> None:
    body = await _rows(world, _base(world) + params, status=422)
    assert body["code"] == code
    assert "request_id" in body
    for _, value in params:
        if len(value) > 3 and value not in ("group_by", "day", "suite", "status"):
            assert value not in body["message"]


async def test_a_repeated_selector_is_refused(world) -> None:
    resp = await world.client.get(ROWS, params=_base(world) + [
        ("group_by", "suite"), ("bucket_suite", "a"), ("bucket_suite", "b")],
        headers=world.member)
    assert resp.status_code == 422, resp.text


async def test_the_database_still_answers_after_a_hostile_selector(world) -> None:
    for value in ("'; DROP TABLE test_cases; --", "x' OR '1'='1", "\\", "%", "_"):
        body = await _rows(world, _base(world) + [
            ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", value)])
        assert body["total"] == 0
    assert (await _rows(world, _base(world) + [
        ("metric", "executions"), ("group_by", "suite"), ("bucket_suite", S1.lower())]))["total"] > 0


# ── 6. the failure-group selector (VIZ-207's signature, BE3's M0) ──────────


@pytest.fixture(scope="module")
def plan():
    from scripts.seed_viz_data import build_viz_seed_plan

    from tests.integration.test_analytics_scope_postgres import PLAN_SLUG

    return build_viz_seed_plan(PLAN_SLUG, FROZEN)


def _failing_signatures(plan, days: int) -> dict[str, int]:
    """Failing executions per signature, from the PYTHON reference
    (``flaky_signals.error_signature``) over the seed plan and this module's
    extra rows -- never from the SQL under test."""
    from app.services.failure_signature import NO_MESSAGE_ID
    from app.services.flaky_signals import error_signature

    start = FROZEN.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    counts: dict[str, int] = {}

    def add(message) -> None:
        key = error_signature(message) or NO_MESSAGE_ID
        counts[key] = counts.get(key, 0) + 1

    for run in plan.runs:
        if run.start_time < start:
            continue
        for case in run.cases:
            if case.status.value in ("FAILED", "BROKEN"):
                add(case.error_message)
    # extra_rows: five failing executions, all "Boom 1\nsecond line", all in window.
    for _ in range(5):
        add("Boom 1\nsecond line")
    return counts


async def test_a_failure_group_s_rows_are_exactly_its_failing_executions(world, plan) -> None:
    """Group by group, the rows behind a signature are the failing executions
    the Python reference puts in that group: numbers, ids and timestamps vary
    inside a group (the F2 case), and every one of them is selected."""
    want = _failing_signatures(plan, 30)
    assert len(want) >= 3 and "boom #" in want, want
    for signature, count in want.items():
        body = await _rows(world, _base(world) + [
            ("metric", "failures"), ("group_by", "error_signature"),
            ("bucket_error_signature", signature), ("size", "200"),
        ])
        assert body["total"] == count, (signature, body["total"], count)
        assert body["meta"]["definitions"]["chart_grain"] == "execution_row"
        assert all(item["status"] in ("failed", "broken") for item in body["items"])
    assert sum(want.values()) == (await _rows(world, _base(world) + [
        ("metric", "failures"), ("group_by", "status"), ("bucket_status", "failed")]))["total"] + (
        await _rows(world, _base(world) + [
            ("metric", "failures"), ("group_by", "status"), ("bucket_status", "broken")]))["total"]


async def test_a_group_s_rows_show_the_line_it_was_read_from(world) -> None:
    body = await _rows(world, _base(world) + [
        ("metric", "failures"), ("group_by", "error_signature"),
        ("bucket_error_signature", "boom #"), ("size", "200"),
    ])
    assert body["total"] == 5
    assert {item["error_line"] for item in body["items"]} == {"Boom 1"}
    keys = [(item["created_at"], uuid.UUID(item["id"])) for item in body["items"]]
    assert keys == sorted(keys, reverse=True)
    # The group path pages like every other: the same sequence, two at a time.
    paged: list[str] = []
    for page in range(1, 4):
        part = await _rows(world, _base(world) + [
            ("metric", "failures"), ("group_by", "error_signature"),
            ("bucket_error_signature", "boom #"), ("size", "2"), ("page", str(page)),
        ])
        assert part["total"] == 5
        paged += [item["id"] for item in part["items"]]
    assert paged == [item["id"] for item in body["items"]]


async def test_a_group_drill_narrows_by_a_second_selector(world, plan) -> None:
    """A group's trend point: ``group_by=day&group_by=error_signature``."""
    # BOUNDARY_DAY holds four "Boom" failures (the tie pair's FAILED and
    # BROKEN and the hostile suite's at 18:00, the boundary pair's at
    # 23:59:59); the next UTC day holds the midnight one.
    day = BOUNDARY_DAY.isoformat()
    next_day = (BOUNDARY_DAY + timedelta(days=1)).isoformat()
    totals = {}
    for value in (day, next_day):
        body = await _rows(world, _base(world) + [
            ("metric", "failures"), ("group_by", "day"), ("group_by", "error_signature"),
            ("bucket_day", value), ("bucket_error_signature", "boom #"),
        ])
        assert all(item["created_at"].startswith(value) for item in body["items"])
        totals[value] = body["total"]
    assert totals == {day: 4, next_day: 1}


async def test_every_failure_group_reconciles_with_its_rows(world) -> None:
    """The plan's test: a failure-groups group's ``failure_count`` equals the
    rows endpoint's total for its id -- the no-message group included. BE3's
    router is mounted here as this module mounts its own."""
    try:
        from app.routers.analytics_failure_groups import router as groups_router
    except ImportError:  # pragma: no cover - BE3's route not in this tree
        pytest.skip("failure-groups route not present")
    from app.core import analytics_read_layer as layer
    from app.main import app
    from app.services import failure_groups_service

    from tests.integration.test_analytics_scope_postgres import _FrozenDatetime

    path = "/api/v1/analytics/failure-groups"
    present = any(getattr(route, "path", None) == path for route in app.router.routes)
    before = list(app.router.routes)
    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, path, "1000000/minute")
    # Its own request clock, which the shared world does not freeze: the
    # window must be the one the rows were drawn with.
    if hasattr(failure_groups_service, "datetime"):
        patch.setattr(failure_groups_service, "datetime", _FrozenDatetime)
    if not present:
        app.include_router(groups_router)
    try:
        resp = await world.client.get(path, params=_base(world), headers=world.member)
        assert resp.status_code == 200, resp.text
        groups = resp.json()
    finally:
        if not present:
            app.router.routes[:] = before
        patch.undo()
    entries = list(groups["groups"])
    if groups.get("no_message") and groups["no_message"].get("failure_count"):
        entries.append(groups["no_message"])
    assert entries
    for group in entries:
        body = await _rows(world, _base(world) + [
            ("metric", "failures"), ("group_by", "error_signature"),
            ("bucket_error_signature", group["id"]), ("size", "1"),
        ])
        assert body["total"] == group["failure_count"], group["id"]


async def test_the_envelope_states_the_grain_and_as_of(world) -> None:
    body = await _rows(world, _base(world) + [
        ("metric", "executions"), ("group_by", "day"), ("bucket_day", BOUNDARY_DAY.isoformat())])
    meta = body["meta"]
    assert meta["definitions"]["chart_grain"] == "run_aggregate"
    assert "chart_grain_note" in meta["definitions"]
    assert meta["as_of"]
    assert meta["truncated"] is False
