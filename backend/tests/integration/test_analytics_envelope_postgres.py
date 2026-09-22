"""VIZ-204 (the ``meta`` envelope) and VIZ-202 (release/suite parity) on real Postgres.

Shares the seeded world of ``test_analytics_scope_postgres.py`` (a fresh copy:
the fixture is module-scoped, so this module seeds its own projects) and adds
the rows the VIZ-202 routes read that the chart seed does not write: systemic
clusters, quarantine requests, defects and AI analyses, all pinned to seeded
test cases by suite and release.

1. **Parity golden.** ``golden/analytics_parity_characterisation.json`` was
   captured from the code BEFORE VIZ-202 changed these routes. A call without
   release/suite filters must still answer exactly that, key for key, once
   ``meta`` is set aside.
2. **Parity.** Each VIZ-202 route applies ``release_id`` and ``suite_name``
   (filtered is a subset of unfiltered), or declares the dimension in
   ``meta.ignored_filters`` -- never drops it silently.
3. **Envelope.** Every analytics route's ``meta`` validates against contract
   C2 (``validate_contract("envelope", ...)``); totals ignore release and suite
   but respect project and window; in-progress runs are counted and declared.
4. **Cache shape.** A dashboard payload cached before ``meta`` existed is
   never served.
5. **Summary report.** Totals and suites honour the release (the header states
   a release the numbers used to ignore); the PDF renders the same numbers.
6. **Totals plan.** The totals count stays on a ``test_runs`` project index
   under a generic plan.

Regenerate the parity golden ONLY from a commit whose behaviour is the
reference: ``ANALYTICS_PARITY_WRITE_GOLDEN=1``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from tests.integration.test_analytics_scope_postgres import (
    FROZEN,
    R1_KEY,
    R2_KEY,
    S1,
    S2,
    _env,
    _literal,
    _normalise,
    _plan_nodes,
)
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: The seeded world, as this module's own (module-scoped) fixture: pytest
#: finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PARITY_GOLDEN = Path(__file__).parent / "golden" / "analytics_parity_characterisation.json"
META_HEADER = "x-analytics-meta"
TREND_PATH = f"/api/v1/test-management/suites/{S1}/trend"

_EFFECTIVE_SUITE = (
    "COALESCE(CASE WHEN tr.trigger_source = 'live_stream' "
    "THEN NULLIF(TRIM(tr.primary_suite_name), '') ELSE NULL END, "
    "NULLIF(TRIM(tc.suite_name), ''))"
)


# ── extra rows the VIZ-202 routes read ──────────────────────────────────────


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def extras(world):
    from app.models.postgres import (
        AIAnalysis,
        Defect,
        FlakyQuarantineRequest,
        SystemicFlakeCluster,
        SystemicFlakeClusterMember,
    )

    r1 = world.releases[R1_KEY]
    async with world.sessions() as db:
        rows = (await db.execute(text(f"""
            SELECT tc.id, tc.test_fingerprint AS fp, tc.test_name, tc.status,
                   LOWER({_EFFECTIVE_SUITE}) AS suite, tr.primary_release_id AS release,
                   tr.created_at
            FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tr.project_id = :p AND tc.test_fingerprint IS NOT NULL
            ORDER BY tc.test_fingerprint, tr.created_at, tc.test_name
        """), {"p": world.p1})).all()

    def fps(suite: str) -> list[str]:
        return sorted({r.fp for r in rows if r.suite == suite.lower()})

    s1_fps, s2_fps = fps(S1), fps(S2)
    assert len(s1_fps) >= 3 and len(s2_fps) >= 3, "the seed must reach both suites"
    names = {r.fp: r.test_name for r in rows}
    failing = [r for r in rows if r.status in ("FAILED", "BROKEN")]

    def first(predicate):
        return next((r for r in failing if predicate(r)), None)

    s1_r1 = first(lambda r: r.suite == S1.lower() and r.release == r1)
    s1_any = first(lambda r: r.suite == S1.lower())
    s2_any = first(lambda r: r.suite == S2.lower())
    unattributed = first(lambda r: r.release is None)
    assert s1_any is not None and s2_any is not None and unattributed is not None
    s1_r1 = s1_r1 or s1_any

    clusters = {
        "viz-a": [s1_fps[0], s1_fps[1]],
        "viz-b": [s2_fps[0], s2_fps[1]],
        "viz-c": [s1_fps[2], s2_fps[2]],
    }
    async with world.sessions.begin() as db:
        for index, (key, members) in enumerate(clusters.items()):
            cluster = SystemicFlakeCluster(
                id=uuid.uuid4(), project_id=world.p1, cluster_key=key,
                label=f"cluster {key}", cause_family="unknown", size=len(members) + index,
                cohesion=0.9 - index / 10, co_failure_runs=4 + index, window_days=60,
                computed_at=FROZEN - timedelta(hours=1),
            )
            db.add(cluster)
            await db.flush()
            for fp in members:
                db.add(SystemicFlakeClusterMember(
                    cluster_id=cluster.id, test_fingerprint=fp, test_name=names[fp],
                    failure_runs=3,
                ))
        for fp, status in (
            (s1_fps[0], "QUARANTINED"), (s2_fps[0], "PROPOSED"), (s1_fps[1], "RELEASED"),
            (s2_fps[1], "APPROVED"),
        ):
            db.add(FlakyQuarantineRequest(
                project_id=world.p1, test_fingerprint=fp, test_name=names[fp],
                status=status, detected_at=FROZEN - timedelta(days=2),
            ))
        for hours, case in enumerate((s1_r1, s2_any, None), start=1):
            db.add(Defect(
                project_id=world.p1, test_case_id=case.id if case else None,
                title=f"viz defect {hours}", severity="HIGH", resolution_status="OPEN",
                promotion_source="manual", created_at=FROZEN - timedelta(hours=hours),
            ))
        analysed = {r.id: r for r in (s1_r1, s2_any, unattributed)}
        for score, case in zip((85, 60, 90), analysed.values()):
            db.add(AIAnalysis(
                test_case_id=case.id, confidence_score=score, is_flaky=score > 80,
                backend_error_found=score == 60, requires_human_review=score < 70,
                created_at=FROZEN - timedelta(days=1),
            ))
    yield SimpleNamespace(
        rows=rows, r1=r1, s1_fps=s1_fps, s2_fps=s2_fps, clusters=clusters,
        analysed=list(analysed.values()), defect_cases=[s1_r1, s2_any],
        # A list, not a dict: a fingerprint can sit in both suites' lists.
        quarantine=[
            (s1_fps[0], "QUARANTINED"), (s2_fps[0], "PROPOSED"),
            (s1_fps[1], "RELEASED"), (s2_fps[1], "APPROVED"),
        ],
    )


def _in_scope(row, *, release=None, suite=None) -> bool:
    """The truth the routes must reproduce: a test_cases row is in scope when
    its run's primary release and its effective suite match."""
    if release is not None and row.release != release:
        return False
    return suite is None or row.suite == suite.lower()


def _ran(extras, **scope) -> set[str]:
    return {row.fp for row in extras.rows if _in_scope(row, **scope)}


# ── helpers ─────────────────────────────────────────────────────────────────


async def _get(world, path, params, headers=None):
    resp = await world.client.get(path, params=params, headers=headers or world.member)
    return resp


def _body_and_meta(resp):
    """``(body without meta, meta)``: meta travels in the body for objects; a
    list-shaped body carries the bounded header SUMMARY in
    ``X-Analytics-Meta`` instead (``analytics_meta.header_summary``: ids and
    counts, no names, ``"form": "summary"`` -- not a C2 envelope)."""
    body = resp.json()
    if isinstance(body, dict) and "meta" in body:
        body = dict(body)
        return body, body.pop("meta")
    raw = resp.headers.get(META_HEADER)
    if raw:
        assert len(raw.encode("ascii")) <= 2048, len(raw)
    return body, (json.loads(raw) if raw else None)


def _is_header_summary(meta) -> bool:
    return isinstance(meta, dict) and meta.get("form") == "summary"


def _suites_applied(meta) -> int:
    if _is_header_summary(meta):
        return meta["suites"]["count"]
    return len(meta["scope"]["suites"])


def _validate_header_summary(summary) -> None:
    """The header's own shape: C2's totals rules on ids and counts."""
    from app.models.viz_contracts import Totals

    assert summary["form"] == "summary" and summary["schema_version"] >= 1
    Totals.model_validate(summary["totals"])
    assert isinstance(summary["measured"], bool) and summary["as_of"]
    for key in ("projects", "releases", "suites"):
        assert summary[key]["count"] >= 0
    if not summary["header_truncated"]:
        assert len(summary["projects"]["ids"]) == summary["projects"]["count"]
        assert len(summary["releases"]["ids"]) == summary["releases"]["count"]
        if not summary["measured"]:
            assert (summary["reason"] or "").strip()


async def _ok(world, path, params, headers=None):
    resp = await _get(world, path, params, headers)
    assert resp.status_code == 200, (path, params, resp.text[:500])
    return _body_and_meta(resp)


#: (case id, path, fixed params)
PARITY_ROUTES: tuple[tuple[str, str, list[tuple[str, str]]], ...] = (
    ("systemic_clusters", "/api/v1/analytics/systemic-clusters", []),
    ("ai_summary", "/api/v1/analytics/ai-summary", [("days", "90")]),
    ("defects", "/api/v1/analytics/defects", []),
    ("tm_suites", "/api/v1/test-management/suites", []),
    ("tm_suite_trend", TREND_PATH, [("days", "30")]),
    ("quarantine_stats", "/api/v1/quarantine/stats", []),
    ("value_metrics", "/api/v1/value-metrics", [("days", "30")]),
)

#: A missing ``project_id`` was FastAPI's default 422 list; the route now
#: speaks the VIZ-210 error contract. Same status, intended body change.
INTENDED_PARITY_ERROR_CHANGES = frozenset({"systemic_clusters/member_all_projects"})


async def _parity_characterise(world) -> dict:
    out: dict = {}
    for name, path, fixed in PARITY_ROUTES:
        for label, params in (
            ("none", [("project_id", str(world.p1)), *fixed]),
            ("member_all_projects", list(fixed)),
        ):
            resp = await _get(world, path, params)
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            if isinstance(body, dict):
                body = {k: v for k, v in body.items() if k != "meta"}
            out[f"{name}/{label}"] = {
                "status": resp.status_code, "body": _normalise(body, world),
            }
    return out


# ── 1. parity golden: no filter answers exactly as before VIZ-202 ───────────


async def test_unfiltered_parity_routes_match_the_pre_viz202_golden(world, extras):
    actual = await _parity_characterise(world)
    if os.environ.get("ANALYTICS_PARITY_WRITE_GOLDEN") == "1":
        PARITY_GOLDEN.write_bytes(
            (json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        )
        pytest.skip(f"golden written: {PARITY_GOLDEN}")
    expected = json.loads(PARITY_GOLDEN.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected)
    for key in INTENDED_PARITY_ERROR_CHANGES:
        assert actual[key]["status"] == expected[key]["status"] == 422, key
        assert actual[key]["body"]["code"] == "missing_parameter", actual[key]
    mismatched = [
        key for key in sorted(expected)
        if key not in INTENDED_PARITY_ERROR_CHANGES and actual[key] != expected[key]
    ]
    assert not mismatched, (
        f"{mismatched}; first: expected={json.dumps(expected[mismatched[0]])[:600]} "
        f"actual={json.dumps(actual[mismatched[0]])[:600]}"
    )
    # Not vacuous: the extra rows reach every route that reads them.
    assert expected["systemic_clusters/none"]["body"]["total"] == 3
    assert expected["defects/none"]["body"]["total"] == 3
    assert expected["ai_summary/none"]["body"]["total_analysed"] == 3
    assert expected["quarantine_stats/none"]["body"]["total_live"] == 3
    assert any(row["suite_name"] == S1 for row in expected["tm_suites/none"]["body"])
    assert any(p["run_count"] for p in expected["tm_suite_trend/none"]["body"]["points"])


# ── 2. parity: applied, or declared -- never dropped ────────────────────────


def _releases_of(meta) -> list[str]:
    if _is_header_summary(meta):
        return list(meta["releases"]["ids"])
    return [item["id"] for item in meta["scope"]["releases"]]


def _ignored(meta) -> set[str]:
    if _is_header_summary(meta):
        return set(meta["ignored_dimensions"])
    return {item["dimension"] for item in meta["ignored_filters"]}


@pytest.mark.parametrize("name,path,fixed", PARITY_ROUTES, ids=[r[0] for r in PARITY_ROUTES])
async def test_every_parity_route_applies_or_declares_each_dimension(world, extras, name, path, fixed):
    base = [("project_id", str(world.p1)), *fixed]
    r1 = str(world.releases[R1_KEY])
    # The trend's own suite is S1 (its path), so ask it for another one.
    suite = S2 if name == "tm_suite_trend" else S1
    for dimension, param in (("release", ("release_id", r1)), ("suite", ("suite_name", suite))):
        _body, meta = await _ok(world, path, [*base, param])
        assert meta is not None, f"{name}: no envelope"
        if dimension == "release":
            echoed = r1 in _releases_of(meta)
        elif _is_header_summary(meta):
            # The header summary carries no suite names, only how many applied.
            echoed = meta["suites"]["count"] == 1
        else:
            echoed = suite.lower() in meta["scope"]["suites"]
        declared = dimension in _ignored(meta)
        # Exactly one: an applied filter is echoed, an unapplied one declared.
        assert echoed != declared, (name, dimension, meta)
        if declared and not _is_header_summary(meta):
            reason = next(i["reason"] for i in meta["ignored_filters"] if i["dimension"] == dimension)
            assert reason.strip(), (name, dimension)


async def test_systemic_clusters_keep_the_members_that_ran_in_scope(world, extras):
    path, base = "/api/v1/analytics/systemic-clusters", [("project_id", str(world.p1))]
    everything, _ = await _ok(world, path, base)
    assert "scope" not in everything, "the unfiltered body gained a key"
    unfiltered = {
        item["cluster_key"]: sorted(m["test_fingerprint"] for m in item["members"])
        for item in everything["items"]
    }
    narrowed: list[bool] = []
    for scope, params in (
        ({"suite": S1}, [("suite_name", S1)]),
        ({"release": extras.r1}, [("release_id", str(extras.r1))]),
        ({"release": extras.r1, "suite": S2}, [("release_id", str(extras.r1)), ("suite_name", S2)]),
    ):
        body, _meta = await _ok(world, path, [*base, *params])
        ran = _ran(extras, **scope)
        expected = {
            key: sorted(fp for fp in members if fp in ran)
            for key, members in extras.clusters.items()
            if any(fp in ran for fp in members)
        }
        actual = {
            item["cluster_key"]: sorted(m["test_fingerprint"] for m in item["members"])
            for item in body["items"]
        }
        assert actual == expected, scope
        assert body["total"] == len(expected)
        assert body["scope"]["cluster_stats"] == "project" and body["scope"]["note"]
        narrowed.append(actual != unfiltered)
    # Not vacuous: at least one scope actually removed members or clusters.
    assert any(narrowed), "no scope narrowed the clusters; the seed cannot tell"


async def test_quarantine_stats_count_the_requests_whose_test_ran_in_scope(world, extras):
    path, base = "/api/v1/quarantine/stats", [("project_id", str(world.p1))]
    everything, _ = await _ok(world, path, base)
    statuses = ("quarantined", "proposed", "released", "approved")
    narrowed: list[bool] = []
    for scope, params in (
        ({"suite": S1}, [("suite_name", S1)]),
        ({"suite": S2}, [("suite_name", S2)]),
        ({"release": extras.r1}, [("release_id", str(extras.r1))]),
        ({"release": None}, [("release_id", "unattributed")]),
        ({"release": extras.r1, "suite": S1}, [("release_id", str(extras.r1)), ("suite_name", S1)]),
    ):
        body, _ = await _ok(world, path, [*base, *params])
        ran = _ran(extras, **scope) if scope != {"release": None} else {
            row.fp for row in extras.rows if row.release is None
        }
        want: dict = {}
        for fp, status in extras.quarantine:
            if fp in ran:
                want[status.lower()] = want.get(status.lower(), 0) + 1
        for status in statuses:
            assert body[status] == want.get(status, 0), (scope, status, body)
        narrowed.append(any(body[s] != everything[s] for s in statuses))
    assert any(narrowed), "no scope narrowed the counts; the seed cannot tell"


async def test_defects_are_placed_by_their_execution(world, extras):
    path, base = "/api/v1/analytics/defects", [("project_id", str(world.p1))]
    for scope, params in (
        ({"suite": S1}, [("suite_name", S1)]),
        ({"suite": S2}, [("suite_name", S2)]),
        ({"release": extras.r1}, [("release_id", str(extras.r1))]),
    ):
        body, _ = await _ok(world, path, [*base, *params])
        want = sum(1 for case in extras.defect_cases if _in_scope(case, **scope))
        assert body["total"] == want, (scope, body["total"])
    # An unlinked defect is not "unattributed": it has no execution to place.
    body, _ = await _ok(world, path, [*base, ("release_id", "unattributed")])
    want = sum(1 for case in extras.defect_cases if case.release is None)
    assert body["total"] == want


async def test_ai_summary_counts_the_analyses_in_scope(world, extras):
    path, base = "/api/v1/analytics/ai-summary", [("project_id", str(world.p1)), ("days", "90")]
    for scope, params in (
        ({"suite": S1}, [("suite_name", S1)]),
        ({"release": extras.r1}, [("release_id", str(extras.r1))]),
        ({"suite": S2}, [("suite_name", S2)]),
    ):
        body, _ = await _ok(world, path, [*base, *params])
        want = sum(1 for case in extras.analysed if _in_scope(case, **scope))
        assert body["total_analysed"] == want, (scope, body)


async def test_suite_catalogue_and_trend_narrow_to_scope(world, extras):
    p1 = ("project_id", str(world.p1))
    catalogue = "/api/v1/test-management/suites"
    everything, meta = await _ok(world, catalogue, [p1])
    assert meta is not None and _suites_applied(meta) == 0
    assert _is_header_summary(meta), "a list body carries the header summary"
    names = {row["suite_name"] for row in everything}
    only, meta = await _ok(world, catalogue, [p1, ("suite_name", S1.lower())])
    assert [row["suite_name"] for row in only] == [S1]
    assert _suites_applied(meta) == 1 and S1 not in json.dumps(meta)
    # A blank suite filters nothing, so none is listed as applied.
    _, meta = await _ok(world, catalogue, [p1, ("suite_name", "   ")])
    assert _suites_applied(meta) == 0
    in_release, _ = await _ok(world, catalogue, [p1, ("release_id", str(extras.r1))])
    assert {row["suite_name"] for row in in_release} <= names
    by_name = {row["suite_name"]: row for row in everything}
    for row in in_release:
        assert row["run_count"] <= by_name[row["suite_name"]]["run_count"], row
    assert sum(r["run_count"] for r in in_release) < sum(r["run_count"] for r in everything)

    trend = [("days", "30")]
    full, _ = await _ok(world, TREND_PATH, [p1, *trend])
    scoped, meta = await _ok(world, TREND_PATH, [p1, *trend, ("release_id", str(extras.r1))])
    assert [p["date"] for p in full["points"]] == [p["date"] for p in scoped["points"]]
    for a, b in zip(full["points"], scoped["points"]):
        assert b["run_count"] <= a["run_count"], (a, b)
    assert 0 < sum(p["run_count"] for p in scoped["points"]) < sum(p["run_count"] for p in full["points"])
    # The applied suite is listed as the query normalises it (VIZ-204 review).
    assert meta["scope"]["suites"] == [S1.lower()] and _releases_of(meta) == [str(extras.r1)]
    # The path names the suite; a query suite is declared, not silently used.
    _, meta = await _ok(world, TREND_PATH, [p1, *trend, ("suite_name", S2)])
    assert "suite" in _ignored(meta) and meta["scope"]["suites"] == [S1.lower()]


async def test_value_metrics_declare_release_and_suite_and_answer_unfiltered(world, extras):
    path, base = "/api/v1/value-metrics", [("project_id", str(world.p1)), ("days", "30")]
    plain, meta = await _ok(world, path, base)
    assert meta["ignored_filters"] == []
    body, meta = await _ok(
        world, path, [*base, ("release_id", str(extras.r1)), ("suite_name", S1)]
    )
    assert body == plain
    assert _ignored(meta) == {"release", "suite"}
    assert meta["scope"]["releases"] == [] and meta["scope"]["suites"] == []


# ── 3. the envelope: contract, totals, in-progress ──────────────────────────

#: (path, fixed params) -- every analytics route that carries ``meta``.
META_ROUTES = (
    ("/api/v1/metrics/summary", [("days", "30")]),
    ("/api/v1/metrics/trends", [("days", "30")]),
    ("/api/v1/analytics/flaky-tests", [("days", "90")]),
    ("/api/v1/analytics/flaky-scores", []),
    ("/api/v1/analytics/failure-categories", [("days", "90")]),
    ("/api/v1/analytics/top-failing", [("days", "90")]),
    ("/api/v1/analytics/coverage", [("days", "90")]),
    ("/api/v1/analytics/suite-detail", [("days", "90"), ("suite_name", S2)]),
    ("/api/v1/reports/summary", [("days", "90")]),
    *((path, fixed) for _, path, fixed in PARITY_ROUTES),
)


async def test_every_meta_validates_against_the_envelope_contract(world, extras):
    from app.models.viz_contracts import validate_contract

    r1, r2 = str(world.releases[R1_KEY]), str(world.releases[R2_KEY])
    p1 = ("project_id", str(world.p1))
    filters = {
        "none": [p1],
        "release": [p1, ("release_id", r1)],
        "suite": [p1, ("suite_name", S1)],
        "both": [p1, ("release_id", r1), ("suite_name", S1)],
        "multi": [p1, ("release_id", r1), ("release_id", r2), ("release_id", "unattributed"),
                  ("suite_name", S1), ("suite_name", S2)],
        "member_all_projects": [],
    }
    checked = 0
    failures = []
    for path, fixed in META_ROUTES:
        for label, params in filters.items():
            resp = await _get(world, path, [*params, *fixed])
            if resp.status_code != 200:
                # A route that needs one project answers 422 without it.
                assert label == "member_all_projects" and resp.status_code == 422, (
                    path, label, resp.status_code, resp.text[:300])
                continue
            _body, meta = _body_and_meta(resp)
            try:
                assert meta is not None, "no envelope"
                if _is_header_summary(meta):
                    _validate_header_summary(meta)
                else:
                    validate_contract("envelope", meta)
            except Exception as exc:  # noqa: BLE001 -- collect every route
                failures.append(f"{path} [{label}]: {str(exc)[:300]}")
            checked += 1
    # The denied shape (a member naming a project they cannot read).
    resp = await _get(world, "/api/v1/metrics/summary", [("project_id", str(world.p2))])
    assert resp.status_code == 200
    validate_contract("envelope", resp.json()["meta"])
    assert not failures, "\n".join(failures)
    assert checked >= len(META_ROUTES) * 5


def _window_truth_sql(scope_sql: str = "") -> str:
    return f"""
        SELECT COUNT(*) AS runs, COALESCE(SUM(total_tests), 0) AS execs,
               COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS in_progress
        FROM test_runs tr
        WHERE tr.project_id = :p AND tr.created_at >= :start {scope_sql}
    """


async def test_totals_ignore_release_and_suite_but_respect_project_and_window(world, extras):
    path = "/api/v1/analytics/coverage"
    p1 = ("project_id", str(world.p1))
    r1 = extras.r1

    async def truth(days, scope_sql="", **params):
        async with world.sessions() as db:
            return (await db.execute(
                text(_window_truth_sql(scope_sql)),
                {"p": world.p1, "start": FROZEN - timedelta(days=days), **params},
            )).one()

    everything = await truth(90)
    in_r1 = await truth(90, "AND tr.primary_release_id = :r", r=r1)
    assert 0 < in_r1.runs < everything.runs

    _, meta = await _ok(world, path, [p1, ("days", "90")])
    totals = meta["totals"]
    assert (totals["total_runs"], totals["total_executions"]) == (everything.runs, everything.execs)
    assert (totals["matched_runs"], totals["matched_executions"]) == (everything.runs, everything.execs)
    assert meta["includes_in_progress"] == everything.in_progress > 0
    assert meta["partial_day"] == FROZEN.date().isoformat()
    assert meta["scope"]["window"] == {
        "from": (FROZEN - timedelta(days=90)).date().isoformat(),
        "to": FROZEN.date().isoformat(), "days": 90, "timezone": "UTC",
    }

    _, meta = await _ok(world, path, [p1, ("days", "90"), ("release_id", str(r1))])
    totals = meta["totals"]
    # The release narrows matched_*, never total_*.
    assert (totals["total_runs"], totals["total_executions"]) == (everything.runs, everything.execs)
    assert (totals["matched_runs"], totals["matched_executions"]) == (in_r1.runs, in_r1.execs)
    assert meta["includes_in_progress"] == in_r1.in_progress
    release = meta["scope"]["releases"][0]
    assert release["id"] == str(r1) and release["name"] and release["status"]

    _, meta = await _ok(world, path, [p1, ("days", "90"), ("suite_name", S1)])
    totals = meta["totals"]
    assert (totals["total_runs"], totals["total_executions"]) == (everything.runs, everything.execs)
    s1_runs = await truth(90, """AND (LOWER(TRIM(tr.primary_suite_name)) = :s OR EXISTS (
        SELECT 1 FROM test_cases tc WHERE tc.test_run_id = tr.id AND LOWER(TRIM(tc.suite_name)) = :s))""",
        s=S1.lower())
    assert totals["matched_runs"] == s1_runs.runs
    assert 0 < totals["matched_executions"] < s1_runs.execs, "a suite counts its rows, not whole runs"

    # The window IS respected by the totals.
    shorter = await truth(30)
    _, meta = await _ok(world, path, [p1, ("days", "30")])
    assert meta["totals"]["total_runs"] == shorter.runs < everything.runs
    # And the project: a member's all-projects view is their one project.
    _, meta = await _ok(world, path, [("days", "90")])
    assert meta["totals"]["total_runs"] == everything.runs
    assert [p["id"] for p in meta["scope"]["projects"]] == [str(world.p1)]


# ── 4. a cached payload from before ``meta`` is never served ────────────────


async def test_a_dashboard_payload_cached_before_meta_is_never_served(world, extras):
    from app.db.redis_client import get_redis
    from app.services.cache_service import _build_cache_key, get_analytics_epoch

    project = str(world.p1)
    epoch = await get_analytics_epoch(project)
    assert epoch is not None, "the test needs a readable epoch"
    # Exactly what the pre-VIZ-204 code wrote for this request.
    legacy = _build_cache_key("dashboard_summary_v2", project, epoch=epoch, days=30, suite="")
    await get_redis().set(legacy, json.dumps({"stale": "pre-meta payload"}), ex=60)
    params = [("project_id", project), ("days", "30")]

    first = (await _get(world, "/api/v1/metrics/summary", params)).json()
    assert "stale" not in first and isinstance(first.get("meta"), dict), first
    # The second read is a cache hit on the new key and still has ``meta``,
    # with ``as_of`` the moment the numbers were read.
    second = (await _get(world, "/api/v1/metrics/summary", params)).json()
    assert second["meta"]["as_of"] == first["meta"]["as_of"]
    assert {k: v for k, v in second.items() if k != "meta"} == {
        k: v for k, v in first.items() if k != "meta"
    }
    keys = [key async for key in get_redis().scan_iter(match=f"analytics:dashboard_summary_v2:{project}:*")]
    assert any(":schema=" in key for key in keys), keys


# ── 5. the summary report honours its release ───────────────────────────────


async def test_summary_report_totals_and_suites_honour_the_release(world, extras, monkeypatch):
    r1 = extras.r1
    params = [("project_id", str(world.p1)), ("days", "90"), ("release_id", str(r1))]
    body, meta = await _ok(world, "/api/v1/reports/summary", params)
    start = FROZEN - timedelta(days=90)
    async with world.sessions() as db:
        runs = (await db.execute(text(
            "SELECT COUNT(*) FROM test_runs WHERE project_id = :p AND primary_release_id = :r "
            "AND created_at >= :s AND created_at < :e"
        ), {"p": world.p1, "r": r1, "s": start, "e": FROZEN})).scalar()
        unique = (await db.execute(text(
            "SELECT COUNT(DISTINCT tc.test_fingerprint) FROM test_cases tc "
            "JOIN test_runs tr ON tr.id = tc.test_run_id WHERE tr.project_id = :p "
            "AND tr.primary_release_id = :r AND tr.created_at >= :s AND tr.created_at < :e "
            "AND tc.test_fingerprint IS NOT NULL"
        ), {"p": world.p1, "r": r1, "s": start, "e": FROZEN})).scalar()
        project_runs = (await db.execute(text(
            "SELECT COUNT(*) FROM test_runs WHERE project_id = :p "
            "AND created_at >= :s AND created_at < :e"
        ), {"p": world.p1, "s": start, "e": FROZEN})).scalar()
    assert 0 < runs < project_runs
    assert body["run_count"] == runs
    assert body["totals"]["total_test_cases"] == unique
    ran_in_r1 = {row.suite for row in extras.rows if row.release == r1}
    assert body["suites"] and all(s["suite_name"].lower() in ran_in_r1 for s in body["suites"])
    assert _releases_of(meta) == [str(r1)]

    # Suites too (VIZ-202): one suite in, the others out.
    only, _ = await _ok(world, "/api/v1/reports/summary", [*params, ("suite_name", S1)])
    assert [s["suite_name"] for s in only["suites"]] == [S1]
    assert only["totals"]["total_test_cases"] <= body["totals"]["total_test_cases"]
    assert all(t["suite_name"] == S1 for t in only["top_failing_tests"])

    # The PDF is rendered from the same numbers.
    import app.services.summary_report_pdf as pdf

    seen: list = []
    real = pdf.render_summary_report_pdf

    def _capture(payload):
        seen.append(payload)
        return real(payload)

    monkeypatch.setattr(pdf, "render_summary_report_pdf", _capture)
    resp = await _get(world, "/api/v1/reports/summary/pdf", params)
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert seen and seen[0]["totals"] == body["totals"]
    assert seen[0]["run_count"] == body["run_count"] and seen[0]["suites"] == body["suites"]


# ── 6. the totals count keeps a project index ───────────────────────────────


async def test_totals_count_uses_a_project_index_on_test_runs_under_a_generic_plan(world, extras):
    import asyncpg  # type: ignore[import-untyped]

    captured: list = []

    def _capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "matched_executions" in statement:
            captured.append((statement, list(parameters or ())))

    event.listen(world.engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await _ok(world, "/api/v1/analytics/coverage", [
            ("project_id", str(world.p1)), ("days", "30"),
            ("release_id", str(world.releases[R1_KEY])),
        ])
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", _capture)
    assert len(captured) == 1, captured
    statement, parameters = captured[0]

    conn = await asyncpg.connect(_env("TESTLOOKUP_POSTGRES_TEST_DSN").replace("+asyncpg", ""))
    try:
        await conn.execute("SET plan_cache_mode = force_generic_plan")
        await conn.execute("SET enable_seqscan = off")
        # The throwaway table holds a few dozen runs, where any small index is
        # as cheap as another, so the planner's pick here says nothing about
        # production. The question this answers is structural: can
        # ``ix_test_runs_project_status_created`` serve the count, with BOTH
        # the tenant pin and the window as index conditions? It could not if
        # the SQL wrapped project_id, OR-ed it across tables or made it
        # null-tolerant. So every other plain index on test_runs is hidden,
        # inside a transaction that is rolled back.
        tx = conn.transaction()
        await tx.start()
        try:
            others = await conn.fetch(
                "SELECT i.relname FROM pg_index x "
                "JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_class t ON t.oid = x.indrelid "
                "WHERE t.relname = 'test_runs' AND NOT x.indisunique AND NOT x.indisprimary "
                "AND i.relname <> 'ix_test_runs_project_status_created'"
            )
            for row in others:
                await conn.execute(f'DROP INDEX "{row["relname"]}"')
            await conn.execute(f"PREPARE vizmeta_totals AS {statement}")
            args = ", ".join(_literal(value) for value in parameters)
            raw = await conn.fetchval(f"EXPLAIN (FORMAT JSON) EXECUTE vizmeta_totals({args})")
        finally:
            await tx.rollback()
    finally:
        await conn.close()
    plan = json.loads(raw)[0]["Plan"]
    hits = [
        node for node in _plan_nodes(plan)
        if node.get("Index Name") == "ix_test_runs_project_status_created"
        and "project_id" in node.get("Index Cond", "")
        and "created_at" in node.get("Index Cond", "")
    ]
    assert hits, f"the totals count cannot use its index: {json.dumps(plan)[:1500]}"
