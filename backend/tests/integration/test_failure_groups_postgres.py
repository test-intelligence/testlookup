"""VIZ-207 -- ``GET /api/v1/analytics/failure-groups`` against real PostgreSQL.

Two worlds, one frozen clock (``test_analytics_scope_postgres.FROZEN``):

* the SEEDED world (VIZ-213's demo plan on project P1): every group is
  compared with an INDEPENDENT reducer -- ``flaky_signals.error_signature``
  run in Python over the seed PLAN, never the SQL under test;
* a CRAFTED world (P3, P4, P5) from a table a human can read, so the headline
  numbers are literals: unique raw lines that collapse to one signature (F2),
  the case where grouping on RAW lines picks the wrong top group, the
  no-message and singleton roll-ups, a message that literally reads
  ``__no_message__``, hostile and very long labels, categories, the trend,
  edges by hand, the release and suite filters, an in-progress run, an old
  failure outside the window, the 200-group cap (P4) and a deactivated project
  (P5) that must never be counted.

Every 200 body validates as C3 ``chart_series`` (graph) and its ``meta`` as C2
``envelope``. The router is mounted here (``bootstrap.py`` registration belongs
to the integrator); the mount is undone at the end of the module.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from tests.integration.test_analytics_scope_postgres import FROZEN, PLAN_SLUG, S1
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: pytest finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/failure-groups"
XSS = '<img src=x onerror="window.__xss=1"> failed 2 times'
LONG = "LongError: " + "x" * 2000
D = timedelta(days=1)


@pytest.fixture(scope="module", autouse=True)
def mounted():
    """Serve the route from the real app (auth, scope, layer, error contract)
    until the integrator registers it; freeze this route's clock; raise (not
    remove) the per-principal limit for the call volume below."""
    from fastapi import Depends

    from app.core import analytics_read_layer as layer
    from app.core.deps import get_current_user_or_api_key
    from app.main import app
    from app.routers import analytics_failure_groups
    from app.services import failure_groups_service

    before = list(app.router.routes)
    if PATH not in {getattr(route, "path", None) for route in before}:
        app.include_router(
            analytics_failure_groups.router, dependencies=[Depends(get_current_user_or_api_key)]
        )
    patch = pytest.MonkeyPatch()
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    patch.setattr(failure_groups_service, "request_clock", lambda: FROZEN)
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
    status: str
    message: Optional[str] = None
    suite: str = "Payments"
    category: Optional[str] = None


@dataclass
class Run:
    key: str
    at: datetime
    cases: list[Case]
    release: Optional[str] = None
    in_progress: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)


F, B, P, S = "FAILED", "BROKEN", "PASSED", "SKIPPED"


def _timeout(i: int) -> str:
    # Unique raw line every time (number + 12-hex request id), ONE signature.
    return f"TimeoutError: waited {30000 + i}ms (request {i:012x})"


ASSERT = "AssertionError: expected 'ok' but got 'nope'"  # identical raw line


def _crafted_runs() -> list[Run]:
    runs: list[Run] = []
    # Signature A: 12 failures, 12 distinct raw lines, tests fa1..fa4, 3 days.
    for i in range(12):
        runs.append(Run(f"a{i}", FROZEN - (i % 3) * D - timedelta(hours=1 + i), [
            Case(f"fa{1 + i % 4}", F if i % 2 else B, _timeout(i),
                 category="INFRASTRUCTURE" if i < 9 else "PRODUCT_BUG"),
        ], release="rel_a" if i < 6 else None))
    # Signature B: 5 failures with ONE raw line (raw grouping's top group);
    # tests fb1, fa1, fa2 -> shares 2 tests with A (Jaccard 2 / (4 + 3 - 2) = 0.4).
    for i, fp in enumerate(("fb1", "fb1", "fb1", "fa1", "fa2")):
        runs.append(Run(f"b{i}", FROZEN - 2 * D - timedelta(minutes=i), [
            Case(fp, F, ASSERT, suite="Search", category="PRODUCT_BUG"),
        ]))
    runs.append(Run("misc", FROZEN - 1 * D, [
        Case("fc1", F, XSS), Case("fc2", F, XSS.replace("2", "3")),          # C: hostile
        Case("fd1", F, "__no_message__"), Case("fd2", B, "__no_message__"),   # D: a literal
        Case("fe1", F, LONG), Case("fe2", F, LONG + " tail"),                  # E: long
        Case("fs1", F, "Lonely: only once"), Case("fs2", B, "Rare: once too"),  # singletons
        Case("fn1", F, None), Case("fn2", B, ""), Case("fn3", F, "   \n\t "),  # no message
        Case("fp1", P, "AssertionError: passed rows never count"),
        Case("fk1", S, "Skipped: skipped rows never count"),
    ]))
    # In progress, today: one more A failure.
    runs.append(Run("live", FROZEN - timedelta(minutes=5), [
        Case("fa3", F, _timeout(99), category="INFRASTRUCTURE"),
    ], in_progress=True))
    # Outside a 7-day window, inside 30.
    runs.append(Run("old", FROZEN - 20 * D, [Case("fa4", F, _timeout(500))]))
    return runs


def _letters(n: int) -> str:
    out = ""
    for _ in range(3):
        out = chr(ord("a") + n % 26) + out
        n //= 26
    return out


def _cap_runs() -> list[Run]:
    """203 signatures (letters only, so each is its own signature), 2-4
    failures each: more than the 200 the response keeps."""
    cases = []
    for g in range(203):
        for j in range(2 + g % 3):
            cases.append(Case(f"cp{g}_{j}", F, f"CapError {_letters(g)} attempt {j}"))
    return [Run("cap", FROZEN - 2 * D, cases)]


async def _write(db, project_id, runs: list[Run], releases: dict) -> None:
    from app.models.postgres import LaunchStatus, TestCase, TestRun

    for run in runs:
        counts = Counter(c.status for c in run.cases)
        db.add(TestRun(
            id=run.id, project_id=project_id, build_number=f"fg-{run.key}",
            jenkins_job=f"fg-{run.key}", trigger_source="push", ingestion_source="unknown",
            status=LaunchStatus.IN_PROGRESS if run.in_progress else LaunchStatus.FAILED,
            total_tests=len(run.cases), passed_tests=counts[P], failed_tests=counts[F],
            broken_tests=counts[B], skipped_tests=counts[S], unknown_tests=0,
            primary_release_id=releases.get(run.release) if run.release else None,
            start_time=run.at, created_at=run.at,
        ))
        await db.flush()
        for case in run.cases:
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=run.id, test_fingerprint=case.fp,
                test_name=f"test_{case.fp}", suite_name=case.suite, status=case.status,
                error_message=case.message, failure_category=case.category, created_at=run.at,
            ))
        await db.flush()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def crafted(world):
    from app.core.security import create_access_token
    from app.models.postgres import Project, ProjectMember, Release, User, UserRole

    tag = uuid.uuid4().hex[:10]
    p3, p4, p5, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    releases = {"rel_a": uuid.uuid4()}
    try:
        async with world.sessions() as db:
            for pid, label, active in ((p3, "p3", True), (p4, "p4", True), (p5, "p5", False)):
                db.add(Project(id=pid, name=f"fg-{label}-{tag}", slug=f"fg-{label}-{tag}",
                               is_active=active, description=f"throwaway VIZ-207 {tag}"))
            db.add(User(id=member, email=f"fg-member-{tag}@example.com",
                        username=f"fg_member_{tag}", full_name="Failure groups member",
                        hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            for pid in (p3, p4, p5):
                db.add(ProjectMember(project_id=pid, user_id=member,
                                     role=UserRole.QA_ENGINEER.value))
            db.add(Release(id=releases["rel_a"], project_id=p3, name="1.0.0", version="1.0.0",
                           status="active"))
            await db.flush()
            await _write(db, p3, _crafted_runs(), releases)
            await _write(db, p4, _cap_runs(), {})
            # The deactivated project fails loudly with a signature nobody else has.
            await _write(db, p5, [Run("gone", FROZEN - D, [
                Case("gz1", F, "GhostError: deleted project"), Case("gz2", F, "GhostError: deleted project"),
            ])], {})
            await db.commit()
        yield SimpleNamespace(
            p3=p3, p4=p4, p5=p5, releases=releases,
            member={"Authorization": f"Bearer {create_access_token(str(member))}"},
        )
    finally:
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b, :c)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id = :u"),
            delete(User).where(User.email == f"fg-member-{tag}@example.com"),
        ):
            try:
                async with world.sessions.begin() as db:
                    await db.execute(statement, {"a": p3, "b": p4, "c": p5, "u": member})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"failure-groups teardown: {type(exc).__name__}: {str(exc)[:160]}")


SHARED = "SharedError: boom"
THIRD = "ThirdError: one"
FOURTH = "FourthError: two"
FUTURE = "FutureError: the agent's clock is ahead"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def twin(world):
    """R1-1 / R1-9: two projects (staging and prod) ingesting the same suite,
    read by a member of both and of nothing else, so All Projects is exactly
    these two.

    * SHARED fails in test ``sx`` of BOTH projects (P6 x2, P7 x3): one
      fingerprint, two tests.
    * THIRD fails in P6's ``tz`` and FOURTH in P7's ``tz``: keyed on the
      fingerprint alone they "share" a test (Jaccard 1.0), but they share none.
    * FUTURE fails twice in one P7 run dated two days after the clock.
    * Test ``nm`` fails with no message once in each project.
    """
    from app.core.security import create_access_token
    from app.models.postgres import Project, ProjectMember, User, UserRole

    tag = uuid.uuid4().hex[:10]
    p6, p7, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with world.sessions() as db:
            for pid, label in ((p6, "p6"), (p7, "p7")):
                db.add(Project(id=pid, name=f"fg-{label}-{tag}", slug=f"fg-{label}-{tag}",
                               is_active=True, description=f"throwaway R1-1 {tag}"))
            db.add(User(id=member, email=f"fg-twin-{tag}@example.com",
                        username=f"fg_twin_{tag}", full_name="Failure groups twin member",
                        hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            for pid in (p6, p7):
                db.add(ProjectMember(project_id=pid, user_id=member,
                                     role=UserRole.QA_ENGINEER.value))
            await db.flush()
            await _write(db, p6, [
                *(Run(f"t6s{i}", FROZEN - D - timedelta(hours=i), [Case("sx", F, SHARED)])
                  for i in range(2)),
                *(Run(f"t6t{i}", FROZEN - 2 * D - timedelta(hours=i), [Case("tz", F, THIRD)])
                  for i in range(2)),
                Run("t6nm", FROZEN - 4 * D, [Case("nm", F, None)]),
            ], {})
            await _write(db, p7, [
                *(Run(f"t7s{i}", FROZEN - D - timedelta(hours=i), [Case("sx", B, SHARED)])
                  for i in range(3)),
                *(Run(f"t7f{i}", FROZEN - 3 * D - timedelta(hours=i), [Case("tz", F, FOURTH)])
                  for i in range(2)),
                Run("t7future", FROZEN + 2 * D, [Case("fu1", F, FUTURE), Case("fu2", F, FUTURE)]),
                Run("t7nm", FROZEN - 4 * D, [Case("nm", B, None)]),
            ], {})
            await db.commit()
        yield SimpleNamespace(
            p6=p6, p7=p7,
            member={"Authorization": f"Bearer {create_access_token(str(member))}"},
        )
    finally:
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id = :u"),
            delete(User).where(User.email == f"fg-twin-{tag}@example.com"),
        ):
            try:
                async with world.sessions.begin() as db:
                    await db.execute(statement, {"a": p6, "b": p7, "u": member})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"failure-groups twin teardown: {type(exc).__name__}: {str(exc)[:160]}")


# ── helpers ────────────────────────────────────────────────────────────────


async def _get(world, params, headers):
    return await world.client.get(PATH, params=params, headers=headers)


async def _body(world, params, headers) -> dict:
    resp = await _get(world, params, headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from app.models.viz_contracts import validate_contract

    validate_contract("chart_series", {k: v for k, v in body.items() if k != "meta"})
    validate_contract("envelope", body["meta"])
    assert body["kind"] == "graph"
    return body


def _p(project, days=30, **extra):
    params = [("days", str(days))]
    if project is not None:
        params.append(("project_id", str(project)))
    for key, value in extra.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        params += [(key, str(v)) for v in values]
    return params


def _by_id(body) -> dict:
    return {g["id"]: g for g in body["groups"]}


def _sig(message):
    from app.services.flaky_signals import error_signature

    return error_signature(message)


SIG_A, SIG_B = _sig(_timeout(0)), _sig(ASSERT)


# ── 1. the seed against the independent reducer ────────────────────────────


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_the_seed_matches_the_independent_reducer(world, plan, days) -> None:
    body = await _body(world, _p(world.p1, days), world.member)
    start = FROZEN.replace(hour=0, minute=0, second=0, microsecond=0) - (days - 1) * D
    counts: Counter = Counter()
    tests: dict = defaultdict(set)
    total = 0
    for run in plan.runs:
        if run.start_time < start:
            continue
        for case in run.cases:
            if case.status.value not in (F, B):
                continue
            total += 1
            sig = _sig(case.error_message)
            counts[sig] += 1
            tests[sig].add(case.test_fingerprint)
    want = {s: n for s, n in counts.items() if s and n >= 2}
    assert body["total_failures"] == total
    assert {g["id"]: g["failure_count"] for g in body["groups"]} == want
    for group in body["groups"]:
        assert group["affected_tests"] == len(tests[group["id"]])
    assert body["no_message"]["failure_count"] == counts.get("", 0)
    assert body["singletons"]["failure_count"] == sum(
        n for s, n in counts.items() if s and n < 2)
    # Ranked by failures desc, then signature.
    order = sorted(want, key=lambda s: (-want[s], s))
    assert [g["id"] for g in body["groups"]] == order
    assert [n["id"] for n in body["nodes"]] == order
    if days >= 30:
        assert len(want) == 5  # the seed's five groups (seed_viz_data.FAILURE_GROUPS)


# ── 2. the crafted world: literals ─────────────────────────────────────────


async def test_unique_raw_lines_collapse_and_the_top_group_is_right(world, crafted) -> None:
    """M-207a: grouping on the RAW first line would make the 5 identical
    assertion lines the top group and A twelve singletons."""
    body = await _body(world, _p(crafted.p3, 30), crafted.member)
    groups = _by_id(body)
    assert body["groups"][0]["id"] == SIG_A
    a = groups[SIG_A]
    assert a["failure_count"] == 14  # 12 + the in-progress one + the 20-day-old one
    assert a["distinct_raw_lines"] == 14
    assert a["affected_tests"] == 4
    assert a["affected_runs"] == 14
    assert groups[SIG_B]["failure_count"] == 5 and groups[SIG_B]["distinct_raw_lines"] == 1
    assert groups[SIG_B]["label"] == ASSERT
    assert a["label"].startswith("TimeoutError: waited ")


async def test_rollups_and_shares_have_one_denominator(world, crafted) -> None:
    """M-207d: shares over the GROUPED failures would sum to 1 without the roll-ups."""
    body = await _body(world, _p(crafted.p3, 30), crafted.member)
    total = 14 + 5 + 2 + 2 + 2 + 2 + 3  # A, B, C, D, E, singletons, no message
    assert body["total_failures"] == total
    assert body["no_message"] == {
        "id": "__NO_MESSAGE__", "failure_count": 3, "affected_tests": 3, "affected_runs": 1,
        "share_of_failures": pytest.approx(3 / total),
    }
    assert body["singletons"]["group_count"] == 2 and body["singletons"]["failure_count"] == 2
    shares = sum(g["share_of_failures"] for g in body["groups"])
    whole = (shares + body["no_message"]["share_of_failures"]
             + body["singletons"]["share_of_failures"] + body["omitted"]["share_of_failures"])
    assert whole == pytest.approx(1.0)
    assert _by_id(body)[SIG_A]["share_of_failures"] == pytest.approx(14 / total)
    assert body["meta"]["truncated"] is False


async def test_a_literal_no_message_text_is_its_own_group(world, crafted) -> None:
    body = await _body(world, _p(crafted.p3, 30), crafted.member)
    groups = _by_id(body)
    assert groups["__no_message__"]["failure_count"] == 2
    assert "__NO_MESSAGE__" not in groups


async def test_hostile_and_long_labels_round_trip_as_data(world, crafted) -> None:
    body = await _body(world, _p(crafted.p3, 30), crafted.member)
    groups = _by_id(body)
    xss = groups[_sig(XSS)]
    assert xss["label"] in (XSS, XSS.replace("2", "3"))
    assert "<img" in xss["signature"]
    long_group = groups[_sig(LONG)]
    assert len(long_group["label"]) == 160 and long_group["label"].endswith("…")
    assert len(long_group["id"]) == 80
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[_sig(XSS)]["label"] == xss["label"]


async def test_categories_and_the_node_group(world, crafted) -> None:
    body = await _body(world, _p(crafted.p3, 30), crafted.member)
    a = _by_id(body)[SIG_A]
    assert a["categories"] == [
        {"category": "infrastructure", "count": 10},
        {"category": "product_bug", "count": 3},
        {"category": "unknown", "count": 1},
    ]
    assert a["dominant_category"] == "infrastructure"
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[SIG_A] == {"id": SIG_A, "label": a["label"], "size": 14, "group": "infrastructure"}
    assert nodes[SIG_B]["group"] == "product_bug"


async def test_the_trend_is_daily_zero_filled_and_sums_to_the_count(world, crafted) -> None:
    body = await _body(world, _p(crafted.p3, 7), crafted.member)
    assert body["trend_grain"] == "day"
    a = _by_id(body)[SIG_A]
    assert [p["x"] for p in a["trend"]] == [
        (FROZEN - (6 - i) * D).date().isoformat() for i in range(7)
    ]
    assert sum(p["y"] for p in a["trend"]) == a["failure_count"] == 13  # the old one is out
    assert a["trend"][-1]["y"] >= 1  # today: the in-progress run
    assert a["trend"][0]["y"] == 0


async def test_in_progress_runs_are_included_and_declared(world, crafted) -> None:
    body = await _body(world, _p(crafted.p3, 7), crafted.member)
    assert body["meta"]["includes_in_progress"] >= 1
    assert "in-progress runs included" in body["meta"]["definitions"]["window_clock"]


async def test_top_tests_and_edges_by_hand(world, crafted) -> None:
    """M-207e: only pairs that share tests are edges; A-B = 2 / (4 + 3 - 2)."""
    plain = await _body(world, _p(crafted.p3, 30), crafted.member)
    assert plain["edges"] == []
    body = await _body(world, _p(crafted.p3, 30, include="edges"), crafted.member)
    a, b = sorted([SIG_A, SIG_B])
    assert body["edges"] == [{"source": a, "target": b, "weight": 0.4}]
    top = _by_id(body)[SIG_B]["top_tests"]
    assert top[0] == {"fingerprint": "fb1", "project_id": str(crafted.p3), "name": "test_fb1",
                      "count": 3}
    assert {t["fingerprint"] for t in top} == {"fb1", "fa1", "fa2"}


async def test_a_repeated_include_is_one_include(world, crafted) -> None:
    """``include`` is repeatable (a list query parameter): a router whose
    annotations are strings (``from __future__ import annotations``) read it as
    single-valued and refused the repeat with a 422."""
    once = await _body(world, _p(crafted.p3, 30, include="edges"), crafted.member)
    twice = await _body(world, _p(crafted.p3, 30, include=["edges", "edges"]), crafted.member)
    assert twice["edges"] == once["edges"] != []


async def test_release_and_suite_filters(world, crafted) -> None:
    rel = await _body(world, _p(crafted.p3, 30, release_id=crafted.releases["rel_a"]),
                      crafted.member)
    assert rel["total_failures"] == 6 and [g["id"] for g in rel["groups"]] == [SIG_A]
    unattributed = await _body(world, _p(crafted.p3, 30, release_id="unattributed"),
                               crafted.member)
    assert _by_id(unattributed)[SIG_A]["failure_count"] == 8
    search = await _body(world, _p(crafted.p3, 30, suite_name="search"), crafted.member)
    assert search["total_failures"] == 5 and [g["id"] for g in search["groups"]] == [SIG_B]


async def test_beyond_200_groups_the_rest_is_omitted_and_declared(world, crafted) -> None:
    body = await _body(world, _p(crafted.p4, 30), crafted.member)
    assert len(body["groups"]) == 200 == len(body["nodes"])
    meta = body["meta"]
    assert meta["truncated"] is True and meta["truncated_total"] == 203
    assert body["omitted"]["group_count"] == 3
    # The kept groups are the largest (4 failures first), ties by signature.
    counts = [g["failure_count"] for g in body["groups"]]
    assert counts == sorted(counts, reverse=True) and counts[0] == 4
    kept = sum(counts)
    assert body["omitted"]["failure_count"] == body["total_failures"] - kept
    shown = sum(g["share_of_failures"] for g in body["groups"])
    assert shown < 1


# ── 3. tenancy, authorisation, refusals ────────────────────────────────────


async def test_all_projects_covers_only_the_caller_s_active_projects(world, crafted) -> None:
    """M-206e analogue: dropping the ``is_active`` guard counts P5."""
    mine = await _body(world, _p(None, 30), crafted.member)
    ids = {g["id"] for g in mine["groups"]}
    assert SIG_A in ids and _sig("GhostError: deleted project") not in ids
    p3 = await _body(world, _p(crafted.p3, 30), crafted.member)
    p4 = await _body(world, _p(crafted.p4, 30), crafted.member)
    assert mine["total_failures"] == p3["total_failures"] + p4["total_failures"]
    seed_member = await _body(world, _p(None, 30), world.member)
    assert SIG_A not in {g["id"] for g in seed_member["groups"]}


async def test_all_projects_keeps_one_test_of_two_projects_apart(world, twin) -> None:
    """R1-1: under All Projects a test is ``(project, fingerprint)``. Keyed on
    the fingerprint alone, SHARED counted ONE affected test and merged both
    projects' counts into one top test, and THIRD / FOURTH were linked by a
    test they do not share."""
    body = await _body(world, _p(None, 30, include="edges"), twin.member)
    groups = _by_id(body)
    shared = groups[_sig(SHARED)]
    assert shared["failure_count"] == 5 and shared["affected_tests"] == 2
    assert shared["affected_runs"] == 5
    assert shared["top_tests"] == [
        {"fingerprint": "sx", "project_id": str(twin.p7), "name": "test_sx", "count": 3},
        {"fingerprint": "sx", "project_id": str(twin.p6), "name": "test_sx", "count": 2},
    ]
    assert groups[_sig(THIRD)]["affected_tests"] == 1
    assert groups[_sig(FOURTH)]["affected_tests"] == 1
    assert body["edges"] == [], "groups that share no (project, test) were linked"
    assert body["no_message"]["failure_count"] == 2
    assert body["no_message"]["affected_tests"] == 2
    # One project: the same numbers a project-scoped reader always had.
    p6 = _by_id(await _body(world, _p(twin.p6, 30), twin.member))
    assert p6[_sig(SHARED)]["affected_tests"] == 1
    assert p6[_sig(SHARED)]["top_tests"] == [
        {"fingerprint": "sx", "project_id": str(twin.p6), "name": "test_sx", "count": 2}]


async def test_a_future_failure_is_counted_and_declared_outside_the_window(world, twin) -> None:
    """R1-9, the heatmap's rule: a failing run dated after today (clock skew)
    stays in failure_count and the shares, falls outside the trend axis, and is
    counted in ``meta.outside_window`` -- never a silent short sparkline."""
    body = await _body(world, _p(twin.p7, 30), twin.member)
    future = _by_id(body)[_sig(FUTURE)]
    assert future["failure_count"] == 2
    assert sum(p["y"] for p in future["trend"]) == 0
    day = (FROZEN + 2 * D).date().isoformat()
    assert body["meta"]["outside_window"] == {
        "buckets": 1, "executions": 2, "first": day, "last": day,
    }
    drawn = sum(p["y"] for g in body["groups"] for p in g["trend"])
    shown = sum(g["failure_count"] for g in body["groups"])
    assert drawn + body["meta"]["outside_window"]["executions"] == shown
    assert "outside_window" in body["meta"]["definitions"]
    # Nothing outside: the key is absent, never a zero.
    p6 = await _body(world, _p(twin.p6, 30), twin.member)
    assert "outside_window" not in p6["meta"]


async def test_a_project_the_caller_cannot_read_is_refused(world, crafted) -> None:
    resp = await _get(world, _p(crafted.p3, 30), world.member)
    assert resp.status_code == 403, resp.text


async def test_a_release_of_another_project_is_refused(world) -> None:
    resp = await _get(world, _p(world.p1, 30, release_id=str(world.r9)), world.member)
    assert resp.status_code in (403, 404), resp.text
    assert "groups" not in resp.json()


@pytest.mark.parametrize("params,code", [
    ([("include", "edges'; DROP TABLE test_cases; --")], "include_enum"),
    ([("include", "nodes")], "include_enum"),
    ([("days", "366")], "window_days_range"),
])
async def test_refusals_are_viz_210_bodies_that_echo_nothing(world, params, code) -> None:
    resp = await _get(world, [("project_id", str(world.p1))] + params, world.member)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == code and "request_id" in body
    assert "DROP TABLE" not in resp.text and "nodes" not in body.get("message", "")
    assert (await _get(world, _p(world.p1, 7), world.member)).status_code == 200


# ── 4. the read layer ──────────────────────────────────────────────────────


async def test_a_repeat_is_a_hit_and_an_epoch_bump_is_a_miss(world, crafted) -> None:
    from app.services.cache_service import bump_analytics_epoch

    params = _p(crafted.p3, 14)
    first = await _get(world, params, crafted.member)
    second = await _get(world, params, crafted.member)
    assert first.status_code == second.status_code == 200
    if first.headers.get("X-Analytics-Cache") != "miss":
        pytest.skip("the cache is not reachable in this environment")
    assert second.headers["X-Analytics-Cache"] == "hit"
    assert second.headers["ETag"] == first.headers["ETag"]
    not_modified = await world.client.get(
        PATH, params=params, headers={**crafted.member, "If-None-Match": first.headers["ETag"]})
    assert not_modified.status_code == 304
    await bump_analytics_epoch(str(crafted.p3))
    after = await _get(world, params, crafted.member)
    assert after.headers["X-Analytics-Cache"] == "miss"


async def test_a_suite_filter_is_effective_suite_aware(world) -> None:
    """The seed's S1 filter reaches exactly the S1 failures (one scope rule)."""
    body = await _body(world, _p(world.p1, 90, suite_name=S1), world.member)
    everything = await _body(world, _p(world.p1, 90), world.member)
    assert 0 < body["total_failures"] < everything["total_failures"]
