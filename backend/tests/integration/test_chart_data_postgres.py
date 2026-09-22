"""VIZ-203 -- ``GET /api/v1/analytics/chart-data`` against real Postgres.

The seeded world is ``test_analytics_scope_postgres.py``'s (VIZ-213's demo
plan on a throwaway project, frozen clock), so every number below is
reproducible on any day and on any machine.

**Where the expectations come from.** ``_expect`` is a second, independent
implementation of each metric, written in plain Python over the SEED PLAN --
the same dataclasses the seeder writes from, never the SQL under test and
never a number captured from a previous run. A golden captured from the code
proves only that the code did not change; a reducer written from the story
proves the code is right. The headline cases additionally assert literal
numbers (``test_the_headline_numbers_are_the_ones_a_human_counted``) so a
matching pair of wrong implementations cannot pass.

What is proved here:

1. **Golden, metric x dimension.** Every metric against every dimension it is
   defined for, over the seeded 30- and 90-day windows.
2. **Equivalence with /metrics/trends.** ``metric=passed|failed|skipped|
   broken|executions|pass_rate`` grouped by ``day`` is the same series the
   trend chart draws, value for value -- the run-aggregate grain, the same
   window, the same tenant filter.
3. **Authorisation.** A release in another project is a 403/404 and no data
   for the readable ids either; a member's all-projects call covers only their
   projects; ``group_by=project`` never shows a project the caller cannot read.
4. **Injection.** Quotes, semicolons, comments, NULs, unicode, over-long
   values and repeated arrays in every parameter: 422 from the allow-list, the
   value never echoed, and the database untouched (the next call still works).
5. **The contract.** The body validates as C3 ``chart_series`` and its ``meta``
   as C2 ``envelope``, on the real payload.
6. **The edge cases the story lists.** Zero-fill, top-N with a recomputed
   "other", ``measured:false`` instead of 0%, ISO weeks in UTC, the partial
   current day, the ``(none)`` branch bucket, in-progress runs included.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.test_analytics_scope_postgres import (
    FROZEN,
    PLAN_SLUG,
    R1_KEY,
    S1,
)
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: pytest finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/chart-data"
NO_VALUE = "(none)"
OTHER = "__other__"

HOSTILE = (
    "'; DROP TABLE test_runs; --",
    "day' OR '1'='1",
    'day") UNION SELECT NULL --',
    "day/*x*/",
    "ｄａｙ",
    "day‮",
    "d" * 4000,
    " ",
)


@pytest.fixture(scope="module")
def plan():
    from scripts.seed_viz_data import build_viz_seed_plan

    return build_viz_seed_plan(PLAN_SLUG, FROZEN)


@pytest.fixture(scope="module", autouse=True)
def unthrottled():
    """VIZ-209 limits this route to 120 requests a minute per principal, and
    the matrix below is several hundred calls from one user. The limit is
    RAISED, not removed, so ``enforce_rate_limit`` still runs on every call
    here -- a route this layer cannot serve would still fail. The 429 path
    itself belongs to ``tests/regression/test_analytics_read_layer.py``.
    """
    from app.core import analytics_read_layer as layer

    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    yield
    patch.undo()


# ── the independent reducer ────────────────────────────────────────────────


def _window_start(days: int) -> datetime:
    midnight = FROZEN.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days - 1)


def _runs(plan, days: int):
    start = _window_start(days)
    return [run for run in plan.runs if run.start_time >= start]


def _effective_suite(run, case) -> str:
    if run.trigger_source == "live_stream" and (run.primary_suite_name or "").strip():
        return run.primary_suite_name.strip()
    return (case.suite_name or "").strip()


def _run_key(run, dimension: str, releases: dict) -> str:
    if dimension == "day":
        return run.start_time.astimezone(timezone.utc).date().isoformat()
    if dimension == "week":
        day = run.start_time.astimezone(timezone.utc).date()
        return (day - timedelta(days=day.isoweekday() - 1)).isoformat()
    if dimension == "branch":
        return (run.branch or "").strip() or NO_VALUE
    if dimension == "environment":
        return ((run.environment or "").strip() or NO_VALUE).lower()
    if dimension == "ingestion_source":
        return run.ingestion_source
    if dimension == "release":
        return str(releases[run.release_key]) if run.release_key else "unattributed"
    raise AssertionError(f"not a run-level dimension: {dimension}")


def _row_key(run, case, dimension: str, releases: dict) -> str:
    if dimension == "suite":
        return (_effective_suite(run, case) or "").lower() or NO_VALUE
    if dimension == "status":
        return case.status.value.lower()
    if dimension == "failure_category":
        return (case.failure_category.value if case.failure_category else "unknown").lower()
    if dimension == "test":
        return case.test_fingerprint
    return _run_key(run, dimension, releases)


ROW_DIMENSIONS = {"suite", "status", "failure_category", "test"}


def _expect(plan, releases, metric: str, dimension: str, days: int) -> dict:
    """``{bucket: y}`` for ``metric`` grouped by ``dimension`` -- run-aggregate
    grain unless the dimension or the metric needs execution rows."""
    row_grain = dimension in ROW_DIMENSIONS or metric in {
        "unique_tests", "flaky_tests", "retried_tests",
        "duration_p50", "duration_p95", "duration_total",
    }
    acc: dict[str, dict] = {}
    for run in _runs(plan, days):
        if row_grain:
            for case in run.cases:
                key = _row_key(run, case, dimension, releases)
                cell = acc.setdefault(key, _empty())
                _add_case(cell, run, case)
        else:
            key = _run_key(run, dimension, releases)
            cell = acc.setdefault(key, _empty())
            cell["passed"] += run.passed_tests
            cell["failed"] += run.failed_tests
            cell["broken"] += run.broken_tests
            cell["skipped"] += run.skipped_tests
            cell["unknown"] += run.unknown_tests
            cell["executions"] += run.total_tests
            cell["runs"] += 1
    return {key: _value(metric, cell) for key, cell in acc.items()}


def _empty() -> dict:
    return {
        "passed": 0, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0,
        "executions": 0, "runs": 0, "retried": 0,
        "fingerprints": set(), "flaky": set(), "durations": [],
    }


def _add_case(cell: dict, run, case) -> None:
    status = case.status.value.lower()
    if status in cell:
        cell[status] += 1
    cell["executions"] += 1
    cell["runs"] = cell["runs"]  # runs are counted as distinct below
    cell.setdefault("run_ids", set()).add(run.build_number)
    if (case.retry_count or 0) > 0:
        cell["retried"] += 1
    cell["fingerprints"].add(case.test_fingerprint)
    if case.is_flaky_run:
        cell["flaky"].add(case.test_fingerprint)
    if case.duration_ms is not None:
        cell["durations"].append(case.duration_ms)


def _percentile(values: list[int], fraction: float):
    """``percentile_cont``: linear interpolation between the closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _value(metric: str, cell: dict):
    from app.core.pass_rate import canonical_pass_rate, executed_count

    evaluated = executed_count(cell["passed"], cell["failed"], cell["broken"])
    if metric == "pass_rate":
        return None if evaluated < 1 else canonical_pass_rate(
            cell["passed"], cell["failed"], cell["broken"]
        )
    if metric == "failure_rate":
        return None if evaluated < 1 else round(
            (cell["failed"] + cell["broken"]) / evaluated * 100, 2
        )
    if metric in ("passed", "failed", "broken", "skipped", "unknown"):
        return cell[metric]
    if metric == "executions":
        return cell["executions"]
    if metric == "run_count":
        return len(cell["run_ids"]) if "run_ids" in cell else cell["runs"]
    if metric == "retried_tests":
        return cell["retried"]
    if metric == "unique_tests":
        return len(cell["fingerprints"])
    if metric == "flaky_tests":
        return len(cell["flaky"])
    if metric == "duration_total":
        return sum(cell["durations"]) if cell["durations"] else 0
    if metric == "duration_p50":
        return _percentile(cell["durations"], 0.5)
    if metric == "duration_p95":
        return _percentile(cell["durations"], 0.95)
    raise AssertionError(metric)


# ── helpers ────────────────────────────────────────────────────────────────


async def _get(world, params, headers=None):
    return await world.client.get(
        PATH, params=params, headers=headers if headers is not None else world.member
    )


async def _chart(world, params, headers=None) -> dict:
    resp = await _get(world, params, headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from app.models.viz_contracts import validate_contract

    validate_contract("chart_series", body)
    validate_contract("envelope", body["meta"])
    return body


def _flat(body: dict) -> dict:
    """``{(series_key, x): y}`` for the whole payload."""
    return {
        (series["key"], point["x"]): point["y"]
        for series in body["series"]
        for point in series["points"]
    }


def _single(body: dict) -> dict:
    """``{x: y}`` for a one-series (single group_by) payload."""
    assert len(body["series"]) <= 1, body["dimensions"]
    if not body["series"]:
        return {}
    return {point["x"]: point["y"] for point in body["series"][0]["points"]}


def _base(world, **extra) -> list[tuple[str, str]]:
    days = extra.pop("days", 30)
    params = [("project_id", str(world.p1)), ("days", str(days))]
    params += [(key, str(value)) for key, value in extra.items()]
    return params


# ── 1. golden: metric x dimension ──────────────────────────────────────────

_SINGLE_DIMENSIONS = (
    "day", "week", "release", "branch", "environment", "ingestion_source",
    "suite", "status", "failure_category",
)


@pytest.mark.parametrize("metric", [
    "executions", "passed", "failed", "broken", "skipped", "unknown",
    "retried_tests", "pass_rate", "failure_rate", "flaky_tests",
    "unique_tests", "run_count", "duration_p50", "duration_p95", "duration_total",
])
@pytest.mark.parametrize("dimension", _SINGLE_DIMENSIONS)
async def test_golden_metric_by_dimension(world, plan, metric, dimension) -> None:
    body = await _chart(world, _base(world, metric=metric, group_by=dimension))
    got = _single(body)
    want = _expect(plan, world.releases, metric, dimension, 30)

    # Zero-fill: a bucket with no data is present. A count reads 0; a rate,
    # a percentile or anything else that was not measured reads null.
    for bucket, value in want.items():
        assert bucket in got, f"{metric}/{dimension}: missing bucket {bucket}"
        if value is None:
            assert got[bucket] is None, f"{metric}/{dimension}/{bucket}"
        else:
            assert got[bucket] == pytest.approx(value, rel=1e-6), (
                f"{metric}/{dimension}/{bucket}"
            )
    for bucket, value in got.items():
        if bucket in want:
            continue
        assert value in (0, None), f"{metric}/{dimension}: {bucket} filled with {value}"


async def test_the_headline_numbers_are_the_ones_a_human_counted(world, plan) -> None:
    """A literal cross-check, so two matching wrong implementations cannot pass.

    The seed's 30-day window holds a fixed set of runs; these three totals are
    read straight off the plan with no shared helper.
    """
    runs = _runs(plan, 30)
    total_executions = sum(run.total_tests for run in runs)
    total_runs = len(runs)
    assert total_executions > 0 and total_runs > 10, "the seed stopped seeding"

    body = await _chart(world, _base(world, metric="executions", group_by="day"))
    assert sum(point["y"] for point in body["series"][0]["points"]) == total_executions

    body = await _chart(world, _base(world, metric="run_count", group_by="day"))
    assert sum(point["y"] for point in body["series"][0]["points"]) == total_runs


async def test_two_dimensions_key_the_series_by_the_second(world, plan) -> None:
    body = await _chart(
        world, _base(world, metric="pass_rate", group_by="day") + [("group_by", "suite")]
    )
    assert body["dimensions"] == ["day", "suite"]
    assert body["x_type"] == "time"
    keys = {series["key"] for series in body["series"]}
    expected = set(_expect(plan, world.releases, "pass_rate", "suite", 30))
    assert keys <= expected | {OTHER}
    # Every series has the same, complete day axis.
    axes = {tuple(point["x"] for point in series["points"]) for series in body["series"]}
    assert len(axes) == 1
    assert len(next(iter(axes))) == 30


# ── 2. equivalence with /metrics/trends ────────────────────────────────────


@pytest.mark.parametrize("metric,column", [
    ("passed", "passed"), ("failed", "failed"), ("skipped", "skipped"),
    ("broken", "broken"), ("executions", "total"),
])
async def test_the_shared_case_equals_metrics_trends(world, metric, column) -> None:
    """Same project, same window, no suite filter: the same numbers."""
    trends = await world.client.get(
        "/api/v1/metrics/trends",
        params=[("project_id", str(world.p1)), ("days", "30")],
        headers=world.member,
    )
    assert trends.status_code == 200, trends.text
    by_day = {row["date"]: row[column] for row in trends.json()["data"]}

    chart = _single(await _chart(world, _base(world, metric=metric, group_by="day")))
    for date, value in by_day.items():
        assert chart[date] == value, f"{metric} diverges from /metrics/trends on {date}"


async def test_the_pass_rate_matches_metrics_trends_where_it_is_measured(world) -> None:
    trends = await world.client.get(
        "/api/v1/metrics/trends",
        params=[("project_id", str(world.p1)), ("days", "30")],
        headers=world.member,
    )
    rows = {row["date"]: row for row in trends.json()["data"]}
    chart = _single(await _chart(world, _base(world, metric="pass_rate", group_by="day")))
    for date, row in rows.items():
        evaluated = row["passed"] + row["failed"] + row["broken"]
        if evaluated == 0:
            # The trend chart reports 0.0 here; the chart endpoint refuses to.
            assert chart[date] is None
            continue
        assert chart[date] == pytest.approx(row["pass_rate"], abs=0.051)


# ── 3. authorisation ───────────────────────────────────────────────────────


async def test_a_release_in_another_project_is_refused_and_returns_no_data(world) -> None:
    resp = await _get(world, _base(
        world, metric="executions", group_by="day",
    ) + [("release_id", str(world.releases[R1_KEY])), ("release_id", str(world.r9))])
    assert resp.status_code in (403, 404), resp.text
    body = resp.json()
    assert body["code"] in ("forbidden", "not_found")
    assert "request_id" in body and "series" not in body


async def test_a_project_the_caller_cannot_read_is_refused(world) -> None:
    resp = await _get(world, [
        ("project_id", str(world.p2)), ("days", "30"),
        ("metric", "executions"), ("group_by", "day"),
    ])
    assert resp.status_code == 403, resp.text


async def test_all_projects_covers_only_the_callers_projects(world) -> None:
    body = await _chart(world, [
        ("days", "30"), ("metric", "executions"), ("group_by", "project"),
    ])
    keys = {point["x"] for point in body["series"][0]["points"]} if body["series"] else set()
    assert str(world.p2) not in keys
    assert {project["id"] for project in body["meta"]["scope"]["projects"]} <= {str(world.p1)}


async def test_an_admin_sees_both_projects_grouped_by_project(world) -> None:
    body = await _chart(
        world, [("days", "30"), ("metric", "executions"), ("group_by", "project")],
        headers=world.admin,
    )
    keys = {point["x"] for point in body["series"][0]["points"]}
    assert {str(world.p1), str(world.p2)} <= keys
    # The x key stays the id a drill-down sends back; x_labels is what the
    # axis shows, so a project chart is not a wall of UUIDs.
    labels = body["x_labels"]
    assert labels[str(world.p1)].startswith("vizscope-p1-")
    assert labels[str(world.p2)].startswith("vizscope-p2-")


async def test_a_release_series_is_labelled_with_the_release_name(world) -> None:
    body = await _chart(
        world,
        _base(world, metric="executions", group_by="day", days=90)
        + [("group_by", "release")],
    )
    labels = {series["label"] for series in body["series"]}
    assert any(label.startswith("v") or label == "Other" for label in labels), labels
    assert not any(label.count("-") == 4 and len(label) == 36 for label in labels), (
        "a release series was left labelled with its UUID"
    )


async def test_an_unknown_release_is_refused_like_a_forbidden_one(world) -> None:
    """403, not 404: an unknown id and an id in another project must be one
    answer, or the parameter reports which release ids exist."""
    unknown = await _get(world, _base(
        world, metric="executions", group_by="day",
    ) + [("release_id", str(uuid.uuid4()))])
    forbidden = await _get(world, _base(
        world, metric="executions", group_by="day",
    ) + [("release_id", str(world.r9))])
    assert unknown.status_code == 403, unknown.text
    assert (unknown.status_code, unknown.json()["message"]) == (
        forbidden.status_code, forbidden.json()["message"],
    )


# ── 4. injection, in every parameter ───────────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
@pytest.mark.parametrize("param", ["metric", "group_by", "top_n", "project_id", "release_id"])
async def test_injection_in_every_parameter(world, value, param) -> None:
    params = _base(world, metric="executions", group_by="day")
    params = [(key, item) for key, item in params if key != param]
    params.append((param, value))
    resp = await _get(world, params)
    assert resp.status_code in (403, 404, 422), (param, value, resp.status_code)
    body = resp.json()
    assert set(body) >= {"code", "message", "request_id", "detail"}
    assert "Traceback" not in body["message"]
    if value.strip():
        # A blank value is a substring of any prose; the rule is about a value
        # that carries a payload.
        assert value not in body["message"], "an untrusted value was echoed back"


@pytest.mark.parametrize("value", HOSTILE)
async def test_injection_in_the_suite_name(world, value) -> None:
    """A suite name is untrusted PLAIN TEXT: it is matched, never rejected for
    its contents (contract change rule 2), so the answer is an empty chart."""
    params = _base(world, metric="executions", group_by="day")
    params.append(("suite_name", value))
    resp = await _get(world, params)
    assert resp.status_code in (200, 422), resp.text
    if resp.status_code == 200 and value.strip():
        # A whitespace-only name normalises to no key at all and so filters
        # nothing -- the pre-VIZ-201 behaviour of every analytics route.
        assert all(
            point["y"] in (0, None)
            for series in resp.json()["series"] for point in series["points"]
        )


async def test_repeated_arrays_do_not_multiply_the_group_by(world) -> None:
    resp = await _get(world, [
        ("project_id", str(world.p1)), ("days", "30"), ("metric", "executions"),
        ("group_by", "day"), ("group_by", "suite"), ("group_by", "status"),
    ])
    assert resp.status_code == 422
    assert resp.json()["code"] == "group_by_cap"


async def test_the_database_still_answers_after_every_injection(world) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="day"))
    assert body["series"], "the seeded data is gone"


async def test_an_unknown_metric_lists_the_allowed_values(world) -> None:
    resp = await _get(world, _base(world, metric="not_a_metric", group_by="day"))
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "metric_enum"
    assert body["param"] == "metric"
    assert "pass_rate" in body["allowed"] and "duration_p95" in body["allowed"]


async def test_an_unknown_dimension_lists_the_allowed_values(world) -> None:
    resp = await _get(world, _base(world, metric="executions", group_by="quarter"))
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "dimension_enum"
    assert "day" in body["allowed"] and "failure_category" in body["allowed"]


async def test_suite_by_test_is_refused(world) -> None:
    resp = await _get(world, _base(
        world, metric="executions", group_by="suite", top_n=5
    ) + [("group_by", "test")])
    assert resp.status_code == 422
    assert resp.json()["code"] == "high_cardinality_pair"


# ── 5. the edge cases the story lists ──────────────────────────────────────


async def test_an_all_skipped_suite_is_not_measured_not_zero(world) -> None:
    """``QuarantinedSuite`` is skipped in every seeded run."""
    from scripts.seed_viz_data import ALWAYS_SKIPPED_SUITE

    body = await _chart(world, _base(world, metric="pass_rate", group_by="suite"))
    points = {point["x"]: point for point in body["series"][0]["points"]}
    point = points[ALWAYS_SKIPPED_SUITE.lower()]
    assert point["y"] is None, "an all-skipped suite read as a pass rate"
    assert point["measured"] is False
    assert point["reason"].strip()
    # Its executions are still counted.
    counts = _single(await _chart(world, _base(world, metric="executions", group_by="suite")))
    assert counts[ALWAYS_SKIPPED_SUITE.lower()] > 0


async def test_the_series_label_keeps_the_suite_s_real_spelling(world) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="suite"))
    # One series (labelled by the metric); the suites are the x axis, whose
    # keys are normalised and whose labels keep the ingested spelling.
    assert {series["label"] for series in body["series"]} == {"executions"}
    assert body["x_labels"][S1.lower()] == S1
    body = await _chart(
        world, _base(world, metric="executions", group_by="day") + [("group_by", "suite")]
    )
    assert any(series["label"] == S1 for series in body["series"]), (
        "the suite legend lost its original casing"
    )


async def test_top_n_adds_an_other_bucket_recomputed_from_counts(world, plan) -> None:
    body = await _chart(world, _base(
        world, metric="pass_rate", group_by="day", top_n=2
    ) + [("group_by", "suite")])
    keys = [series["key"] for series in body["series"]]
    assert len(keys) == 3 and keys[-1] == OTHER

    # "other" is the suites outside the top 2, recomputed from their counts.
    kept = set(keys[:-1])
    dropped = {
        name for name in _expect(plan, world.releases, "executions", "suite", 30)
        if name not in kept
    }
    assert dropped, "nothing was dropped -- this test proves nothing"
    other = next(series for series in body["series"] if series["key"] == OTHER)
    # The rate of the "other" bucket is bounded by the extremes it merges, and
    # is NOT their unweighted mean unless they happen to be equal.
    assert any(point["y"] is not None for point in other["points"])


async def test_week_buckets_are_iso_weeks_in_utc_and_the_timezone_is_stated(world) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="week", days=90))
    xs = [point["x"] for point in body["series"][0]["points"]]
    assert all(datetime.fromisoformat(x).isoweekday() == 1 for x in xs), xs
    assert xs == sorted(xs)
    assert body["meta"]["scope"]["window"]["timezone"] == "UTC"
    assert "ISO" in body["meta"]["definitions"]["bucket"]


async def test_the_current_utc_day_is_flagged_partial(world) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="day"))
    today = FROZEN.date().isoformat()
    assert body["meta"]["partial_day"] == today
    assert body["series"][0]["points"][-1]["x"] == today


async def test_in_progress_runs_are_included_and_declared(world) -> None:
    body = await _chart(world, _base(world, metric="run_count", group_by="day"))
    assert body["meta"]["includes_in_progress"] >= 1
    today = FROZEN.date().isoformat()
    points = {point["x"]: point["y"] for point in body["series"][0]["points"]}
    assert points[today] >= 1, "the in-progress run dated today was dropped"


async def test_a_null_branch_is_the_none_bucket(world, plan) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="branch", days=90))
    got = _single(body)
    assert NO_VALUE in got, sorted(got)
    assert got[NO_VALUE] == _expect(plan, world.releases, "executions", "branch", 90)[NO_VALUE]


async def test_an_empty_window_is_zero_filled_not_empty(world) -> None:
    """A release nothing in the window belongs to: every day is still there.

    Review finding 4, on the wire. This used to be ``len(series) <= 1`` with
    the assertions behind an ``if``, because an empty window really did answer
    ``series: []`` while one matching run answered seven zero-filled points --
    two shapes for "nothing happened", and the test accepted both.
    """
    body = await _chart(world, _base(
        world, metric="executions", group_by="day", days=7,
    ) + [("release_id", "unattributed"), ("suite_name", "no-such-suite")])
    assert len(body["series"]) == 1, "an empty window lost its series"
    assert len(body["series"][0]["points"]) == 7
    assert all(point["y"] == 0 for point in body["series"][0]["points"])
    assert body["meta"]["measured"] is False
    assert body["meta"]["reason"].strip()


async def test_the_status_dimension_uses_the_contract_vocabulary(world) -> None:
    body = await _chart(world, _base(world, metric="executions", group_by="status"))
    keys = set(_single(body))
    assert keys <= {"passed", "failed", "broken", "skipped", "unknown"}
    assert "passed" in keys


async def test_flaky_tests_counts_fingerprints_and_says_so(world, plan) -> None:
    body = await _chart(world, _base(world, metric="flaky_tests", group_by="day"))
    definition = body["meta"]["definitions"]["flaky_tests"]
    assert "is_flaky_run" in definition and "FlakyScore" in definition
    got = _single(body)
    want = _expect(plan, world.releases, "flaky_tests", "day", 30)
    for bucket, value in want.items():
        assert got[bucket] == value


async def test_a_suite_filter_narrows_the_chart(world) -> None:
    unfiltered = _single(await _chart(world, _base(world, metric="executions", group_by="day")))
    filtered = _single(await _chart(
        world, _base(world, metric="executions", group_by="day") + [("suite_name", S1)]
    ))
    assert set(filtered) == set(unfiltered)
    assert all(filtered[day] <= unfiltered[day] for day in filtered)
    assert sum(filtered.values()) < sum(unfiltered.values())


async def test_the_caps_are_never_exceeded(world) -> None:
    from app.models.viz_contracts import MAX_POINTS_PER_SERIES, MAX_SERIES

    body = await _chart(world, _base(
        world, metric="executions", group_by="day", days=365, top_n=5
    ) + [("group_by", "test")])
    assert len(body["series"]) <= MAX_SERIES
    assert all(len(series["points"]) <= MAX_POINTS_PER_SERIES for series in body["series"])
    if body["meta"]["truncated"]:
        assert body["meta"]["truncated_total"] >= len(body["series"])


async def test_the_envelope_totals_ignore_release_and_suite(world) -> None:
    body = await _chart(
        world,
        _base(world, metric="executions", group_by="day") + [("suite_name", S1)],
    )
    totals = body["meta"]["totals"]
    assert totals["matched_executions"] <= totals["total_executions"]
    assert totals["matched_runs"] <= totals["total_runs"]


async def test_the_response_is_a_pure_get_with_no_side_effects(world) -> None:
    """Cache-friendly (VIZ-209): the same request twice is the same payload."""
    params = _base(world, metric="pass_rate", group_by="day")
    first = await _chart(world, params)
    second = await _chart(world, params)
    assert _flat(first) == _flat(second)
    assert first["series"] == second["series"]


# ── 8. the ranking, the cut and the roll-up happen in the database ─────────


def _scope_for(world, **extra):
    from app.services.analytics_scope import AnalyticsScope

    base = dict(project_id=world.p1, allowed_project_ids=None,
                release_ids=(), suite_names=(), days=30)
    base.update(extra)
    return AnalyticsScope(**base)


def _grouped_cte(sql: str) -> str:
    """The inner aggregate alone -- the statement this endpoint used to return
    whole. Matching parentheses, because the body is full of them."""
    opening = sql.index("WITH grouped AS (") + len("WITH grouped AS (")
    depth = 1
    for index in range(opening, len(sql)):
        depth += {"(": 1, ")": -1}.get(sql[index], 0)
        if depth == 0:
            return sql[opening:index]
    raise AssertionError("the grouped CTE is not closed")


async def test_only_the_kept_keys_and_their_roll_up_ever_leave_the_database(world) -> None:
    """Review finding 1. ``top_n`` and the 8 x 366 caps used to be applied in
    Python AFTER every group had been fetched: the bare aggregate returns one
    row per (day, test) pair, and the chart shows 8 series.

    Mutation (d): return the bare ``GROUP BY`` from ``build_statement`` and
    this fails on the row count -- the numbers would still be right, which is
    exactly why a correctness test alone never caught it.
    """
    from app.services import chart_data_service as svc

    scope = _scope_for(world, days=90)
    spec = svc.parse_chart_spec("executions", ["day", "test"], 5, scope=scope)

    sql, params = svc.build_statement(spec, scope, now=FROZEN)
    bare = _grouped_cte(sql)

    async with world.sessions() as db:
        from app.services.analytics_scope import scoped_text

        ranked = (await db.execute(scoped_text(sql, params), params)).fetchall()
        unranked = (await db.execute(scoped_text(bare, params), params)).fetchall()

    assert unranked, "the seed produced no groups -- this test proves nothing"
    assert len(ranked) < len(unranked), (
        f"SQL returned every group ({len(unranked)}) instead of the ranked ones"
    )
    assert len(ranked) <= svc.MAX_GROUPS
    # The kept keys plus at most one roll-up, over the days that have data.
    keys = {row.series_key for row in ranked}
    assert len(keys - {OTHER}) <= 5
    assert OTHER in keys, "the dropped fingerprints were not rolled up"


async def test_the_other_bucket_sql_built_sums_to_everything_that_was_dropped(world) -> None:
    """The roll-up moved into SQL; its arithmetic did not change."""
    from app.services import chart_data_service as svc

    scope = _scope_for(world, days=90)
    spec = svc.parse_chart_spec("executions", ["suite"], 1, scope=scope)
    async with world.sessions() as db:
        payload = await svc.build_chart_data(db, scope, spec, now=FROZEN)
    points = {point["x"]: point["y"] for point in payload["series"][0]["points"]}
    assert OTHER in points, "nothing was dropped -- this test proves nothing"

    total = sum(_expect(plan_of(world), world.releases, "executions", "suite", 90).values())
    assert sum(points.values()) == total, "the roll-up lost or double-counted rows"


def plan_of(world):
    from scripts.seed_viz_data import build_viz_seed_plan

    return build_viz_seed_plan(PLAN_SLUG, FROZEN)


async def test_the_sql_ranking_keeps_the_same_keys_the_assembly_would_have(world) -> None:
    """Two rankings, one rule. The SQL ``ORDER BY rank_total DESC, rank_key
    ASC`` has to agree with ``assemble``'s ``(-rank, key)`` exactly, or a
    smaller series stays in the chart while a bigger one is rolled into
    'other'."""
    from app.services import chart_data_service as svc

    scope = _scope_for(world, days=90)
    for metric, top_n in (("executions", 2), ("pass_rate", 2), ("duration_p95", 2)):
        spec = svc.parse_chart_spec(metric, ["day", "suite"], top_n, scope=scope)
        unranked = svc.parse_chart_spec(metric, ["day", "suite"], None, scope=scope)
        async with world.sessions() as db:
            ranked = await svc.build_chart_data(db, scope, spec, now=FROZEN)
            everything = await svc.build_chart_data(db, scope, unranked, now=FROZEN)
        # The unranked chart sees every suite (there are fewer than the cap),
        # so ranking IT in Python is the reference the SQL has to match.
        by_key = {series["key"]: series for series in everything["series"]}
        assert len(by_key) > top_n, (metric, "nothing was dropped")
        totals = {
            key: sum(point["n"] or 0 for point in series["points"])
            for key, series in by_key.items()
        }
        expected = sorted(totals, key=lambda key: (-totals[key], key))[:top_n]
        assert [s["key"] for s in ranked["series"] if s["key"] != OTHER] == expected, metric


async def test_run_count_s_n_is_the_executions_not_the_run_count(world) -> None:
    """Review finding 5: ``n`` was ``y`` on the run grain."""
    body = await _chart(world, _base(world, metric="run_count", group_by="day"))
    assert body["meta"]["definitions"]["grain"] == "run_aggregate"
    drawn = [point for point in body["series"][0]["points"] if point["y"]]
    assert drawn, "no run in the window -- this test proves nothing"
    assert all(point["n"] >= point["y"] for point in drawn)
    assert any(point["n"] != point["y"] for point in drawn), (
        "n still equals y: the sample measures the value it is the sample of"
    )


async def test_a_suite_filter_declares_that_it_moved_the_grain(world) -> None:
    """Review finding 6."""
    bare = await _chart(world, _base(world, metric="run_count", group_by="day"))
    filtered = await _chart(
        world, _base(world, metric="run_count", group_by="day") + [("suite_name", S1)]
    )
    assert bare["meta"]["definitions"]["grain"] == "run_aggregate"
    assert "grain_changed_by" not in bare["meta"]["definitions"]
    assert filtered["meta"]["definitions"]["grain"] == "execution_row"
    assert filtered["meta"]["definitions"]["grain_changed_by"] == "suite_name"
    assert "suite_name" in filtered["meta"]["definitions"]["grain_changed_note"]


async def test_truncation_reaches_the_envelope_per_axis(world) -> None:
    """Review finding 3: ``meta.truncated_axes`` names WHICH axis lost what."""
    body = await _chart(world, _base(
        world, metric="executions", group_by="day", days=365, top_n=2
    ) + [("group_by", "test")])
    # top_n was asked for, so nothing is "truncated" -- the roll-up is the
    # answer, not a loss.
    assert body["meta"]["truncated"] is False
    assert "truncated_axes" not in body["meta"]

    # ``group_by=test`` within one suite has no ``top_n``, so the C3 series cap
    # is the only thing bounding it -- and a cap nobody asked for is a LOSS,
    # which is what ``truncated_axes`` has to name.
    capped = await _chart(world, _base(
        world, metric="executions", group_by="day", days=365
    ) + [("group_by", "test"), ("suite_name", S1)])
    if capped["meta"]["truncated"]:
        axes = capped["meta"]["truncated_axes"]
        assert axes["series"]["dimension"] == "test"
        assert axes["series"]["total"] > axes["series"]["kept"]
        assert capped["meta"]["truncated_total"] == axes["series"]["total"]
        assert "x" not in axes, "a generated time axis cannot truncate"
    else:  # pragma: no cover - the seed decides
        pytest.skip("this suite has 8 or fewer fingerprints; nothing to truncate")
