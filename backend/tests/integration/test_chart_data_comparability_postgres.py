"""VIZ-404 -- ``meta.comparability`` on ``/analytics/chart-data`` against real Postgres.

The defect this closes: the multi-series chart's "not comparable" banner could
never appear from real data, so two releases that ran different suites were
compared as if like-for-like. Here the judgement is made from REAL rows, and
every expectation is a count a human made from the tiny world below -- never a
number captured from the code under test.

The world (all runs yesterday, inside every window)::

    project P  (the member reads only P)
      a1  release RA  branch main       suites Checkout, Orders
      b1  release RB  branch feature/x  suites Checkout, Orders
      c1  release RC  branch hotfix     suites Checkout, Payments
      e1  release RE  branch case       suites "checkout " , ORDERS  (RA's suites, other spelling)
    project P2 (nobody but the admin reads it)
      x1  release RX  branch main       suites Checkout, Orders, Secret
    project P3
      d1  release RD  branch nodata     no per-test rows (run totals only)
      f1  release RF  branch rows       suite Checkout

Mutations (see the report): always comparable, comparing suite names
case-sensitively, and dropping the key when it is false each fail here.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/chart-data"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def cmp_world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        LaunchStatus,
        Project,
        ProjectMember,
        Release,
        TestCase,
        TestRun,
        TestStatus,
        User,
        UserRole,
    )

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    fakeredis = pytest.importorskip("fakeredis")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    # VIZ-209 limits this route per principal; raised, not removed.
    from app.core import analytics_read_layer as layer

    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")

    tag = uuid.uuid4().hex[:10]
    p, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    admin, member = uuid.uuid4(), uuid.uuid4()
    rel = {key: uuid.uuid4() for key in ("RA", "RB", "RC", "RE", "RX", "RD", "RF")}
    when = datetime.now(timezone.utc) - timedelta(days=1)
    plan = (
        # (project, release, branch, suites or None for "no per-test rows")
        (p, "RA", "main", ["Checkout", "Orders"]),
        (p, "RB", "feature/x", ["Checkout", "Orders"]),
        (p, "RC", "hotfix", ["Checkout", "Payments"]),
        (p, "RE", "case", ["checkout ", "ORDERS"]),
        (p2, "RX", "main", ["Checkout", "Orders", "Secret"]),
        (p3, "RD", "nodata", None),
        (p3, "RF", "rows", ["Checkout"]),
    )
    try:
        async with sessions() as db:
            for pid, label in ((p, "p"), (p2, "p2"), (p3, "p3")):
                db.add(Project(
                    id=pid, name=f"vizcmp-{label}-{tag}", slug=f"vizcmp-{label}-{tag}",
                    is_active=True, description=f"throwaway VIZ-404 {tag}",
                ))
            for uid, label, role in (
                (admin, "admin", UserRole.ADMIN),
                (member, "member", UserRole.QA_ENGINEER),
            ):
                db.add(User(
                    id=uid, email=f"vizcmp-{label}-{tag}@example.com",
                    username=f"vizcmp_{label}_{tag}", full_name=f"VizCmp {label}",
                    hashed_password="!unusable", role=role.value,
                ))
            await db.flush()
            db.add(ProjectMember(project_id=p, user_id=member, role=UserRole.QA_ENGINEER.value))
            for pid, key, _branch, _suites in plan:
                db.add(Release(
                    id=rel[key], project_id=pid, name=f"vizcmp-{key}-{tag}",
                    version=f"{key}-{tag}", status="active",
                ))
            await db.flush()
            for pid, key, branch, suites in plan:
                run_id = uuid.uuid4()
                cases = [(suite, n) for suite in (suites or []) for n in range(2)]
                total = len(cases) if suites is not None else 5
                db.add(TestRun(
                    id=run_id, project_id=pid, build_number=f"vizcmp-{key}-{tag}",
                    jenkins_job="vizcmp", status=LaunchStatus.PASSED,
                    ingestion_source="unknown", branch=branch,
                    total_tests=total, passed_tests=total,
                    primary_release_id=rel[key], created_at=when,
                ))
                await db.flush()
                for suite, n in cases:
                    name = f"{suite.strip().lower()}_{n}"
                    db.add(TestCase(
                        id=uuid.uuid4(), test_run_id=run_id,
                        test_fingerprint=f"vizcmp-{tag}-{name}"[:64],
                        test_name=name, suite_name=suite,
                        status=TestStatus.PASSED, duration_ms=10, created_at=when,
                    ))
            await db.commit()

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, tag=tag, p=p, p2=p2, p3=p3,
                rel={key: str(value) for key, value in rel.items()},
                admin=_jwt(admin), member=_jwt(member),
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM canonical_test_cases WHERE project_id IN (:a, :b, :c)"),
            text("DELETE FROM projects WHERE id IN (:a, :b, :c)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2)"),
            text("DELETE FROM users WHERE id IN (:u1, :u2)"),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(
                        statement, {"a": p, "b": p2, "c": p3, "u1": admin, "u2": member}
                    )
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"vizcmp teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _chart(world, params: list[tuple[str, str]], headers=None) -> dict:
    from app.models.viz_contracts import validate_contract

    resp = await world.client.get(PATH, params=params, headers=headers or world.admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate_contract("chart_series", body)
    validate_contract("envelope", body["meta"])
    return body


def _releases(world, *keys: str) -> list[tuple[str, str]]:
    return [("release_id", world.rel[key]) for key in keys]


def _by(dimension: str, metric: str = "pass_rate") -> list[tuple[str, str]]:
    return [("metric", metric), ("group_by", "day"), ("group_by", dimension)]


async def test_two_releases_with_the_same_suites_are_comparable(cmp_world) -> None:
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p))] + _by("release")
        + _releases(cmp_world, "RA", "RB"),
    )
    assert len(body["series"]) == 2
    assert body["meta"]["comparability"] == {
        "comparable": True, "reason": None, "reason_code": None,
    }


async def test_two_releases_with_different_suites_say_so_with_the_counts(cmp_world) -> None:
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p))] + _by("release")
        + _releases(cmp_world, "RA", "RC"),
    )
    judged = body["meta"]["comparability"]
    assert judged == {
        "comparable": False,
        "reason": (
            "The 2 series compared by release did not run the same suites in this "
            "scope: 3 suites ran in at least one of them, 1 in all of them."
        ),
        "reason_code": "different_suites",
    }
    # Counts only: no suite, release or project name reaches the banner.
    for name in ("Checkout", "Orders", "Payments", cmp_world.tag, "vizcmp"):
        assert name.lower() not in judged["reason"].lower()
    # The comparison is still drawn.
    assert len(body["series"]) == 2


async def test_suites_match_case_insensitively_and_trimmed(cmp_world) -> None:
    """RE ran ``"checkout "`` and ``ORDERS``: RA's suites, another spelling.
    The chart buckets them as one suite, so the judgement must too."""
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p))] + _by("release")
        + _releases(cmp_world, "RA", "RE"),
    )
    assert body["meta"]["comparability"]["comparable"] is True


async def test_the_judgement_is_over_the_chart_s_suite_scope(cmp_world) -> None:
    """Filtered to Checkout, RA and RC ran the same suites: exactly what the
    chart is drawing."""
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p)), ("suite_name", "Checkout")]
        + _by("release") + _releases(cmp_world, "RA", "RC"),
    )
    assert body["meta"]["comparability"]["comparable"] is True


async def test_a_release_with_no_per_test_rows_is_partial_coverage(cmp_world) -> None:
    """``executions`` is run-aggregate grain, so RD's run is IN the chart with
    its 5 tests -- but no row says which suites they were."""
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p3))] + _by("release", "executions")
        + _releases(cmp_world, "RD", "RF"),
    )
    assert body["meta"]["definitions"]["grain"] == "run_aggregate"
    assert len(body["series"]) == 2
    assert body["meta"]["comparability"] == {
        "comparable": False,
        "reason": (
            "1 of the 2 series compared by release has no per-test results in this "
            "scope, so the suites it ran cannot be compared."
        ),
        "reason_code": "partial_coverage",
    }


async def test_branches_are_judged_the_same_way(cmp_world) -> None:
    same = await _chart(
        cmp_world, [("project_id", str(cmp_world.p))] + _by("branch")
        + _releases(cmp_world, "RA", "RB"),
    )
    assert {s["key"] for s in same["series"]} == {"main", "feature/x"}
    assert same["meta"]["comparability"]["comparable"] is True

    different = await _chart(
        cmp_world, [("project_id", str(cmp_world.p))] + _by("branch")
        + _releases(cmp_world, "RA", "RC"),
    )
    assert different["meta"]["comparability"]["reason_code"] == "different_suites"
    assert "by branch" in different["meta"]["comparability"]["reason"]


async def test_another_project_s_suites_never_enter_the_judgement(cmp_world) -> None:
    """The member reads only P. P2's ``main`` branch ran a third suite; had it
    leaked into the coverage query, ``main`` would count 4 suites anywhere."""
    body = await _chart(cmp_world, _by("branch"), headers=cmp_world.member)
    assert {s["key"] for s in body["series"]} == {"main", "feature/x", "hotfix", "case"}
    assert body["meta"]["comparability"]["reason"] == (
        "The 4 series compared by branch did not run the same suites in this "
        "scope: 3 suites ran in at least one of them, 1 in all of them."
    )


async def test_an_other_series_is_judged_as_the_rest_rolled_into_one(cmp_world) -> None:
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p)), ("top_n", "1")]
        + _by("release", "executions") + _releases(cmp_world, "RA", "RB", "RC"),
    )
    keys = [s["key"] for s in body["series"]]
    assert len(keys) == 2 and keys[-1] == "__other__"
    judged = body["meta"]["comparability"]
    # Whichever release is kept, "other" holds the remaining two, whose union
    # covers Payments only if RC is in it: either way the sets differ.
    assert judged["reason_code"] == "different_suites"
    assert judged["reason"].startswith("The 2 series compared by release")


@pytest.mark.parametrize("params", [
    # One release: nothing to compare with.
    [("group_by", "day"), ("group_by", "release"), ("release_id", "RA")],
    # No series dimension at all.
    [("group_by", "day"), ("release_id", "RA"), ("release_id", "RC")],
    # Series that are not releases or branches.
    [("group_by", "day"), ("group_by", "suite"), ("release_id", "RA"), ("release_id", "RC")],
    [("group_by", "release"), ("release_id", "RA"), ("release_id", "RC")],
], ids=["single-release", "no-series", "suite-series", "release-axis"])
async def test_no_comparison_means_no_key(cmp_world, params) -> None:
    resolved = [
        (name, cmp_world.rel[value] if name == "release_id" else value)
        for name, value in params
    ]
    body = await _chart(
        cmp_world, [("project_id", str(cmp_world.p)), ("metric", "pass_rate")] + resolved,
    )
    assert "comparability" not in body["meta"]
