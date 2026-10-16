"""W08 TRND-08 -- ``GET /api/v1/value-metrics/by-team`` against real Postgres.

Proved on a world written here:

1. **Every test counts.** 60 000 executions of 40 tests in one project are 40
   tests and 60 000 executions (the old service stopped at 50 000 rows).
2. **The evaluated pass rate.** Skipped rows are outside it; a team with only
   skipped rows has ``pass_rate: None``.
3. **The shared resolver.** A test's team is the one ``/analytics/top-failing``
   names for it; a live-stream run's tests bucket by their session suite.
4. **Hours through cluster members.** A 3-member cluster of size 3 over two
   teams gives triage counts 2 and 1 and dedup 4/3 and 2/3; a cluster with no
   member found is Unassigned's.
5. **The sum check.** Σ teams' ``hours_saved`` + ``not_attributable_hours`` ==
   ``headline_hours`` (± 0.01), with clusters, duplicates and a run unblocked
   by quarantine -- and the headline is the hours-saved model's own legs.
6. **No rules** → ``rules_configured: false``, ``teams: []``.
7. **The scope.** A foreign project is a 403; release and suite are declared
   ignored.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

NOW = datetime.now(timezone.utc)
PATH = "/api/v1/value-metrics/by-team"
BODY_KEYS = {
    "window_days", "measured", "reason", "rules_configured", "teams",
    "not_attributable_hours", "headline_hours", "meta",
}
BIG_RUNS, BIG_TESTS = 1500, 40


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


#: Project T's runs: key -> (days ago, trigger, primary suite, [(fp, suite, status)]).
T_RUNS = {
    "RP": (3, None, None, [("pay1", "payments", "PASSED"), ("pay2", "payments", "FAILED"),
                           ("pay3", "payments", "SKIPPED"), ("m1", "misc", "PASSED")]),
    "RF": (2, None, None, [("pay2", "payments", "FAILED"), ("pay4", "payments", "FAILED"),
                           ("srch2", "search", "FAILED"), ("srch1", "search", "SKIPPED"),
                           ("sk1", "skipping", "SKIPPED")]),
    # The SDK stamps the class on every row; the session's label is the suite.
    "RL": (2, "live_stream", "payments-live", [("live1", "com.acme.LiveTest", "PASSED")]),
    # Its only failure is quarantined: the run was unblocked.
    "RQ": (1, None, None, [("flaky1", "misc", "FAILED")]),
    "RG": (1, None, None, [("x1", "misc", "FAILED")]),
    # Owned by its CLASS (a component rule), whatever its suite and name say.
    "RC": (1, None, None, [("cart1", "misc", "FAILED")]),
}
#: Row class names (the resolver's ``component``).
T_CLASSES = {"cart1": "com.acme.checkout.CartTest"}
T_RULES = [("component", "com.acme.checkout.*", "Checkout", 20),
           ("suite_name", "pay*", "Payments", 10), ("suite_name", "search*", "Search", 5),
           ("suite_name", "skip*", "Skippers", 1)]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.models.postgres import (
        Defect,
        FailureCluster,
        FlakyQuarantineRequest,
        LaunchStatus,
        Project,
        ProjectMember,
        ServiceOwnershipRule,
        TestCase,
        TestRun,
        User,
        UserRole,
    )
    from app.routers import value_metrics

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(value_metrics.router)
    app.dependency_overrides[get_db] = _get_db
    patch = pytest.MonkeyPatch()
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    ids = {name: uuid.uuid4() for name in ("t", "n", "big", "q")}
    member, outsider = uuid.uuid4(), uuid.uuid4()
    cases: dict[tuple[str, str], uuid.UUID] = {}
    try:
        async with sessions() as db:
            for name, pid in ids.items():
                db.add(Project(id=pid, name=f"team-{name}-{tag}", slug=f"team-{name}-{tag}", is_active=True))
            for uid, label in ((member, "member"), (outsider, "outsider")):
                db.add(User(id=uid, email=f"team-{label}-{tag}@example.com", username=f"team_{label}_{tag}",
                            full_name=f"Team {label}", hashed_password="!unusable",
                            role=UserRole.QA_ENGINEER.value))
            await db.flush()
            for name in ("t", "n", "big"):
                db.add(ProjectMember(project_id=ids[name], user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=ids["q"], user_id=outsider, role=UserRole.QA_ENGINEER.value))
            for match_type, pattern, team, priority in T_RULES:
                db.add(ServiceOwnershipRule(project_id=ids["t"], match_type=match_type, match_pattern=pattern,
                                            service_name=team.lower(), team_name=team, priority=priority))
            db.add(ServiceOwnershipRule(project_id=ids["big"], match_type="suite_name", match_pattern="*",
                                        service_name="big", team_name="BigTeam"))
            await db.flush()

            runs: dict[str, uuid.UUID] = {}
            for key, (days_ago, trigger, primary, rows) in T_RUNS.items():
                run_id = uuid.uuid4()
                runs[key] = run_id
                statuses = [status for _fp, _suite, status in rows]
                db.add(TestRun(id=run_id, project_id=ids["t"], build_number=f"team-{key}-{tag}",
                               jenkins_job="team", status=LaunchStatus.FAILED, ingestion_source="sdk",
                               trigger_source=trigger, primary_suite_name=primary, total_tests=len(rows),
                               passed_tests=statuses.count("PASSED"), failed_tests=statuses.count("FAILED"),
                               skipped_tests=statuses.count("SKIPPED"), created_at=_ago(days_ago)))
                await db.flush()
                for fp, suite, status in rows:
                    case_id = uuid.uuid4()
                    cases[(key, fp)] = case_id
                    db.add(TestCase(id=case_id, test_run_id=run_id, test_fingerprint=fp, test_name=fp,
                                    suite_name=suite, class_name=T_CLASSES.get(fp), status=status,
                                    created_at=_ago(days_ago)))
            await db.flush()
            # C1: size 3 over Payments (2 members) and Search (1).
            db.add(FailureCluster(
                test_run_id=runs["RF"], cluster_id="cl_001", label="c1", size=3,
                member_test_ids=[str(cases[("RF", "pay2")]), str(cases[("RF", "pay4")]),
                                 str(cases[("RF", "srch2")])],
                created_at=_ago(2),
            ))
            # C2: size 2, no member found (one malformed id, one unknown).
            db.add(FailureCluster(test_run_id=runs["RG"], cluster_id="cl_002", label="c2", size=2,
                                  member_test_ids=["not-a-uuid", str(uuid.uuid4())], created_at=_ago(1)))
            db.add(FlakyQuarantineRequest(project_id=ids["t"], test_fingerprint="flaky1", test_name="flaky1",
                                          status="QUARANTINED", quarantine_start=_ago(5)))
            db.add(Defect(project_id=ids["t"], test_case_id=cases[("RP", "pay2")], title="pay2 broke",
                          created_at=_ago(3), resolved_at=_ago(3) + timedelta(hours=6),
                          resolution_status="RESOLVED"))

            # BIG: 60 000 executions of 40 tests. The runs through the ORM (its
            # defaults), the 60 000 rows set-based: one INSERT ... SELECT.
            await db.execute(insert(TestRun), [
                {"id": uuid.uuid4(), "project_id": ids["big"], "build_number": f"big-{n}-{tag}",
                 "jenkins_job": "big", "status": LaunchStatus.PASSED, "ingestion_source": "sdk",
                 "total_tests": BIG_TESTS, "passed_tests": BIG_TESTS, "created_at": _ago(1 + n / BIG_RUNS)}
                for n in range(BIG_RUNS)
            ])
            await db.execute(text(
                "INSERT INTO test_cases (id, test_run_id, test_fingerprint, test_name, suite_name, status, created_at) "
                "SELECT gen_random_uuid(), tr.id, 'big-' || lpad(g::text, 2, '0'), 'big ' || g, 'bulk', 'PASSED', "
                "tr.created_at FROM test_runs tr CROSS JOIN generate_series(0, :last) AS g "
                "WHERE tr.project_id = :pid"
            ), {"pid": ids["big"], "last": BIG_TESTS - 1})
            await db.commit()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, ids=ids, cases=cases,
                member={"Authorization": f"Bearer {create_access_token(str(member))}"},
                outsider={"Authorization": f"Bearer {create_access_token(str(outsider))}"},
            )
        finally:
            await client.aclose()
    finally:
        for statement in (
            "DELETE FROM defects WHERE project_id = ANY(:ids)",
            "DELETE FROM projects WHERE id = ANY(:ids)",
            "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)",
            "DELETE FROM users WHERE id = ANY(:users)",
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(text(statement), {"ids": list(ids.values()), "users": [member, outsider]})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"team teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _scope(pid, days: int = 30):
    from app.services.analytics_scope import AnalyticsScope

    return AnalyticsScope(project_id=pid, allowed_project_ids=None, release_ids=(), suite_names=(), days=days)  # type: ignore[arg-type]


async def _body(world, name: str) -> dict:
    from app.services import team_value_metrics_service as svc

    async with world.sessions() as db:
        return await svc.get_team_value_metrics(db, _scope(world.ids[name]))


def _teams(body: dict) -> dict:
    return {row["team"]: row for row in body["teams"]}


# ── 1. every test counts ───────────────────────────────────────────────────


async def test_sixty_thousand_executions_of_forty_tests_are_all_counted(world):
    body = await _body(world, "big")
    (big,) = body["teams"]
    assert (big["team"], big["tests"], big["executions"]) == ("BigTeam", 40, 60_000)
    assert (big["evaluated"], big["passed"], big["pass_rate"]) == (60_000, 60_000, 100.0)


# ── 2 + 3. the pass rate and the resolver ──────────────────────────────────


async def test_skipped_rows_are_outside_the_rate_and_a_skipped_only_team_is_not_measured(world):
    teams = _teams(await _body(world, "t"))
    payments = teams["Payments"]
    # pay1 P, pay2 F F, pay3 S, pay4 F, live1 P: 5 evaluated, 2 passed.
    assert (payments["tests"], payments["executions"], payments["evaluated"], payments["passed"]) == (5, 6, 5, 2)
    assert payments["pass_rate"] == 40.0
    assert teams["Search"]["pass_rate"] == 0.0
    skippers = teams["Skippers"]
    assert (skippers["tests"], skippers["evaluated"], skippers["pass_rate"]) == (1, 0, None)


async def test_a_live_stream_run_buckets_by_its_session_suite(world):
    """live1's rows say com.acme.LiveTest; its run is labelled payments-live."""
    teams = _teams(await _body(world, "t"))
    assert teams["Payments"]["tests"] == 5
    assert teams["Unassigned"]["tests"] == 3  # m1, flaky1, x1 -- not live1


async def test_a_test_s_team_is_the_one_top_failing_names(world):
    from app.services import analytics_service
    from app.services import team_value_metrics_service as svc

    pid = world.ids["t"]
    async with world.sessions() as db:
        top = await analytics_service.top_failing_tests(db, str(pid), 30, 50)
        rules, cmap = await svc._ownership_inputs(db, pid)
        since = NOW - timedelta(days=31)
        rows = {row["test_fingerprint"]: row for row in await svc._test_rows(db, pid, since)}
    checked = 0
    for item in top["items"]:
        fp = item["test_fingerprint"]
        if fp not in rows or item.get("owner_source") not in ("rule", None):
            continue
        assert svc.team_of(rules, cmap, rows[fp]) == (item["owner_team"] or svc.UNASSIGNED), fp
        checked += 1
    assert checked >= 5  # pay2, pay4, srch2, x1, flaky1, cart1
    # The class rule is the resolver's: a suite-and-name glob would miss it.
    owner = {item["test_fingerprint"]: item["owner_team"] for item in top["items"]}
    assert owner["cart1"] == "Checkout"
    assert _teams(await _body(world, "t"))["Checkout"]["tests"] == 1


# ── 4 + 5. hours ───────────────────────────────────────────────────────────


async def test_a_cluster_over_two_teams_is_split_by_member_and_the_sum_is_the_headline(world):
    from app.services.value_metrics_service import (
        DEFAULT_BLOCKED_RUN_WAIT_MINUTES as WAIT,
    )
    from app.services.value_metrics_service import (
        DEFAULT_DEFECT_FILING_MINUTES as FILE,
    )
    from app.services.value_metrics_service import (
        DEFAULT_TRIAGE_MINUTES_PER_FAILURE as TRIAGE,
    )

    body = await _body(world, "t")
    teams = _teams(body)
    assert teams["Payments"]["hours"] == {
        "triage": round(2 * TRIAGE / 60, 2), "dedup": round(4 / 3 * FILE / 60, 2),
    }
    assert teams["Search"]["hours"] == {
        "triage": round(1 * TRIAGE / 60, 2), "dedup": round(2 / 3 * FILE / 60, 2),
    }
    # C2's members were not found: all of it is Unassigned's.
    assert teams["Unassigned"]["hours"] == {
        "triage": round(2 * TRIAGE / 60, 2), "dedup": round(1 * FILE / 60, 2),
    }
    # The runs-unblocked leg: one run, per run.
    assert body["not_attributable_hours"] == round(1 * WAIT / 60, 2)
    headline = (5 * TRIAGE + 3 * FILE + 1 * WAIT) / 60
    assert body["headline_hours"] == pytest.approx(headline, abs=0.01)
    total = sum(row["hours_saved"] for row in body["teams"]) + body["not_attributable_hours"]
    assert total == pytest.approx(body["headline_hours"], abs=0.01)
    assert [row["team"] for row in body["teams"]][-1] == "Unassigned"


async def test_the_headline_is_the_hours_saved_model_s_own_legs(world):
    """``get_hours_saved_model``'s headline is ``compute_leg_hours`` over
    ``_gather_leg_counts(now - days)``; by-team's headline is that total."""
    from app.services import value_metrics_service as vms

    body = await _body(world, "t")
    async with world.sessions() as db:
        assumptions = await vms.get_effective_assumptions(db, world.ids["t"])
        gathered = await vms._gather_leg_counts(
            db, world.ids["t"], datetime.now(timezone.utc) - timedelta(days=30), monthly=False,
        )
    legs = vms.compute_leg_hours(gathered[vms._TOTAL_KEY], assumptions)
    assert body["headline_hours"] == legs["hours_total"]
    assert body["not_attributable_hours"] == legs["hours_quarantine"]


async def test_defects_go_to_their_test_s_team_with_the_mttr_of_the_resolved(world):
    teams = _teams(await _body(world, "t"))
    assert (teams["Payments"]["defects"], teams["Payments"]["mttr_hours"]) == (1, 6.0)
    assert (teams["Search"]["defects"], teams["Search"]["mttr_hours"]) == (0, None)


# ── 6 + 7. no rules; the scope ─────────────────────────────────────────────


async def test_no_rules_is_not_measured_and_lists_no_team(world):
    resp = await world.client.get(PATH, params={"project_id": str(world.ids["n"])}, headers=world.member)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == BODY_KEYS
    assert (body["rules_configured"], body["measured"], body["teams"]) == (False, False, [])
    assert body["reason"] == "No ownership rules yet."
    assert body["meta"]["measured"] is False


async def test_the_route_answers_the_contract_s_body(world):
    resp = await world.client.get(PATH, params={"project_id": str(world.ids["t"]), "days": 30},
                                  headers=world.member)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == BODY_KEYS
    assert (body["window_days"], body["rules_configured"], body["measured"]) == (30, True, True)
    for row in body["teams"]:
        assert set(row) == {"team", "tests", "executions", "evaluated", "passed", "pass_rate", "flaky_tests",
                            "defects", "mttr_hours", "hours_saved", "hours"}


async def test_a_foreign_project_is_refused_and_no_project_is_the_scope_s_422(world):
    foreign = await world.client.get(PATH, params={"project_id": str(world.ids["t"])}, headers=world.outsider)
    assert foreign.status_code == 403
    missing = await world.client.get(PATH, headers=world.member)
    assert missing.status_code == 422 and missing.json()["param"] == "project_id"
    long = await world.client.get(PATH, params={"project_id": str(world.ids["t"]), "days": 366},
                                  headers=world.member)
    assert long.status_code == 422


async def test_release_and_suite_are_declared_ignored(world):
    resp = await world.client.get(
        PATH, params=[("project_id", str(world.ids["t"])), ("suite_name", "payments")], headers=world.member,
    )
    assert resp.status_code == 200, resp.text
    ignored = resp.json()["meta"]["ignored_filters"]
    assert [item["dimension"] for item in ignored] == ["suite"]
    unscoped = await world.client.get(PATH, params={"project_id": str(world.ids["t"])}, headers=world.member)
    assert _teams(resp.json()) == _teams(unscoped.json())
