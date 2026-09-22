"""VIZ-201 / VIZ-210 against real Postgres: one scope resolver, unchanged answers.

Four things only a real database can prove:

1. **Characterisation.** Every analytics route that took ``release_id`` /
   ``suite_name`` before VIZ-201 answers a single-value call exactly as it did
   before: the golden file ``golden/analytics_scope_characterisation.json`` was
   captured from the pre-VIZ-201 code on the same seeded data and frozen clock,
   and every case below must still match it key for key.
2. **Multi-value semantics.** Repeated ``release_id`` and ``suite_name`` mean
   OR within a dimension and AND across dimensions: ``R1+R2`` is the union of
   ``R1`` and ``R2``, and adding suites intersects.
3. **Authorisation covers every id.** One forbidden release in a list is a
   403/404 and no data at all, even when the other ids are readable.
4. **The single-release plan still uses** ``ix_test_runs_project_release_created``
   under a GENERIC plan, where a null-tolerant ``(:x IS NULL OR col = :x)``
   predicate could not.

The clock is frozen (seed and queries share one ``now``), so the golden holds
on any day. Every project and user carries a unique tag and is removed at the
end; the database is shared, so nothing here reads rows it did not write.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head. Uses ``REDIS_URL``
when set, else ``fakeredis``. Regenerate the golden ONLY from a commit whose
behaviour is the reference: ``ANALYTICS_SCOPE_WRITE_GOLDEN=1``.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
#: Fixed so the seed's RNG (keyed by this slug) writes the same rows every time.
PLAN_SLUG = "viz-scope-characterisation"
GOLDEN = Path(__file__).parent / "golden" / "analytics_scope_characterisation.json"
S1, S2 = "CheckoutSuite", "InventorySuite"
R1_KEY, R2_KEY = "1.0.0-rc1", "2.0.0"

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_TIME_MODULES = (
    "app.services.analytics_service",
    "app.services.metrics_service",
    "app.services.summary_report_service",
    "app.services.analytics_scope",
    # VIZ-202 / VIZ-204 (``test_analytics_envelope_postgres.py`` shares this
    # fixture): the envelope's window and the parity routes' own clocks.
    "app.services.analytics_meta",
    "app.services.suite_history_service",
    "app.services.value_metrics_service",
)


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return FROZEN.astimezone(tz) if tz is not None else FROZEN.replace(tzinfo=None)


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
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
        TestRun,
        User,
        UserRole,
    )
    from scripts.seed_viz_data import apply_viz_seed, build_viz_seed_plan

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis_client()

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
    import importlib

    for name in _TIME_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, "datetime"):
            patch.setattr(module, "datetime", _FrozenDatetime)

    tag = uuid.uuid4().hex[:10]
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    admin, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r9 = uuid.uuid4()
    plan = build_viz_seed_plan(PLAN_SLUG, FROZEN)
    try:
        async with sessions() as db:
            project = Project(
                id=p1, name=f"vizscope-p1-{tag}", slug=f"vizscope-p1-{tag}", is_active=True,
                description=f"throwaway VIZ-201 characterisation {tag}",
            )
            db.add(project)
            db.add(Project(
                id=p2, name=f"vizscope-p2-{tag}", slug=f"vizscope-p2-{tag}", is_active=True,
                description=f"throwaway VIZ-201 authz {tag}",
            ))
            for uid, label, role in (
                (admin, "admin", UserRole.ADMIN),
                (member, "member", UserRole.QA_ENGINEER),
                (outsider, "outsider", UserRole.QA_ENGINEER),
            ):
                db.add(User(
                    id=uid, email=f"vizscope-{label}-{tag}@example.com",
                    username=f"vizscope_{label}_{tag}", full_name=f"VizScope {label}",
                    hashed_password="!unusable", role=role.value,
                ))
            await db.flush()
            db.add(ProjectMember(project_id=p1, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=p2, user_id=outsider, role=UserRole.QA_ENGINEER.value))
            # A release in P2 with one run: readable to the outsider and the
            # admin, forbidden to the P1 member.
            db.add(Release(id=r9, project_id=p2, name=f"vizscope-r9-{tag}", version="9.9.9",
                           status="active"))
            await db.flush()
            db.add(TestRun(
                id=uuid.uuid4(), project_id=p2, build_number=f"vizscope-{tag}",
                jenkins_job="vizscope", status=LaunchStatus.PASSED, ingestion_source="unknown",
                total_tests=1, passed_tests=1, primary_release_id=r9,
                primary_suite_name=S1, created_at=FROZEN,
            ))
            await apply_viz_seed(db, project, None, plan)
            await db.commit()

        async with sessions() as db:
            names = {
                row.name: row.id
                for row in (await db.execute(
                    select(Release.id, Release.name).where(Release.project_id == p1)
                )).all()
            }
            runs = {
                str(row.id): row.build_number
                for row in (await db.execute(
                    select(TestRun.id, TestRun.build_number).where(TestRun.project_id == p1)
                )).all()
            }
        releases = {rel.key: names[rel.name] for rel in plan.releases}

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        aliases: dict[str, str] = {str(p1): "<P1>", str(p2): "<P2>", str(r9): "<R9>"}
        aliases.update({str(rid): f"<release:{key}>" for key, rid in releases.items()})
        aliases.update({rid: f"<run:{build}>" for rid, build in runs.items()})

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, engine=engine, tag=tag,
                p1=p1, p2=p2, r9=r9, releases=releases, aliases=aliases,
                admin=_jwt(admin), member=_jwt(member), outsider=_jwt(outsider),
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM canonical_test_cases WHERE project_id IN (:a, :b)"),
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2, :u3)"),
            delete(User).where(User.email.like(f"vizscope-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(
                        statement, {"a": p1, "b": p2, "u1": admin, "u2": member, "u3": outsider}
                    )
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"vizscope teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── helpers ─────────────────────────────────────────────────────────────────


def _normalise(value, world):
    """Replace this run's generated ids and tag with stable placeholders."""
    if isinstance(value, dict):
        return {key: _normalise(item, world) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item, world) for item in value]
    if isinstance(value, str):
        value = _UUID_RE.sub(lambda m: world.aliases.get(m.group(0).lower(), "<uuid>"), value)
        return value.replace(world.tag, "<tag>")
    return value


async def _call(world, path: str, params: list[tuple[str, str]], headers):
    resp = await world.client.get(path, params=params, headers=headers)
    if resp.headers.get("content-type", "").startswith("application/pdf"):
        body = {
            "content_type": resp.headers["content-type"],
            "disposition": resp.headers.get("content-disposition"),
            "is_pdf": resp.content.startswith(b"%PDF"),
        }
    else:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
    return resp.status_code, _normalise(body, world)


#: (case id prefix, path, fixed params, takes suite_name, takes release_id)
ROUTES = (
    ("metrics_summary", "/api/v1/metrics/summary", [("days", "30")], True, True),
    ("metrics_trends", "/api/v1/metrics/trends", [("days", "30")], True, True),
    ("flaky_tests", "/api/v1/analytics/flaky-tests", [("days", "90")], True, True),
    ("flaky_scores", "/api/v1/analytics/flaky-scores", [], False, True),
    ("failure_categories", "/api/v1/analytics/failure-categories", [("days", "90")], True, True),
    ("top_failing", "/api/v1/analytics/top-failing", [("days", "90")], True, True),
    ("coverage", "/api/v1/analytics/coverage", [("days", "90")], True, True),
    ("suite_detail", "/api/v1/analytics/suite-detail", [("days", "90")], True, True),
    ("summary_window", "/api/v1/reports/summary", [("days", "90"), ("mode", "window")], False, True),
    ("summary_latest", "/api/v1/reports/summary", [("days", "90"), ("mode", "latest")], False, True),
    ("summary_pdf", "/api/v1/reports/summary/pdf", [("days", "90")], False, True),
)


def _filters(world):
    r1 = str(world.releases[R1_KEY])
    return {
        "none": [],
        "release": [("release_id", r1)],
        "suite": [("suite_name", S1)],
        "release_suite": [("release_id", r1), ("suite_name", S1)],
        "unattributed": [("release_id", "unattributed")],
        "unattributed_suite": [("release_id", "unattributed"), ("suite_name", S1)],
        # Normalisation: case and surrounding spaces do not change the suite.
        "suite_casefold": [("suite_name", "  checkoutsuite ")],
    }


async def _characterise(world) -> dict:
    out: dict = {}
    for name, path, fixed, takes_suite, _takes_release in ROUTES:
        for label, filt in _filters(world).items():
            if not takes_suite and any(key == "suite_name" for key, _ in filt):
                continue
            params = [("project_id", str(world.p1)), *fixed, *filt]
            out[f"{name}/{label}"] = await _call(world, path, params, world.member)
        # All accessible projects: a member without project_id.
        out[f"{name}/member_all_projects"] = await _call(world, path, list(fixed), world.member)
    return {key: {"status": status, "body": body} for key, (status, body) in out.items()}


def _without_meta(characterised: dict) -> dict:
    """VIZ-204 adds ``meta`` to every analytics body and changes nothing else:
    set it aside (asserting it is there) and the rest must equal the golden."""
    out: dict = {}
    for key, case in characterised.items():
        body = case["body"]
        if case["status"] == 200 and isinstance(body, dict) and "content_type" not in body:
            assert isinstance(body.get("meta"), dict), f"{key}: no meta envelope"
            body = {k: v for k, v in body.items() if k != "meta"}
        out[key] = {"status": case["status"], "body": body}
    return out


# ── 1. characterisation ─────────────────────────────────────────────────────

#: The golden's ONE deliberate re-capture: the four release-scoped summary
#: report cases (``summary_{window,latest}/{release,unattributed}``) were
#: re-taken after VIZ-202 fixed the report to scope its totals and suites by
#: the release it names (before, only top-failing honoured it); every other
#: case is still the pre-VIZ-201 capture. ``meta`` (VIZ-204) is set aside by
#: ``_without_meta`` before comparing.
#:
#: Captured as FastAPI's default 422 list before VIZ-210; now the contract body.
INTENDED_ERROR_BODY_CHANGES = frozenset({
    "flaky_scores/member_all_projects",
    "summary_pdf/member_all_projects",
})


async def test_single_value_responses_match_the_pre_viz201_golden(world):
    actual = _without_meta(await _characterise(world))
    if os.environ.get("ANALYTICS_SCOPE_WRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(
            (json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        )
        pytest.skip(f"golden written: {GOLDEN}")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected), "the case list drifted from the golden"
    # The ONLY intended differences: a validation error on these routes now
    # carries the VIZ-210 body instead of FastAPI's list. Same status.
    for key in INTENDED_ERROR_BODY_CHANGES:
        assert actual[key]["status"] == expected[key]["status"] == 422, key
        assert actual[key]["body"]["code"] == "missing_parameter", actual[key]
        assert actual[key]["body"]["param"] == "project_id", actual[key]
        assert actual[key]["body"]["detail"], actual[key]
    mismatched = [
        key for key in sorted(expected)
        if key not in INTENDED_ERROR_BODY_CHANGES and actual[key] != expected[key]
    ]
    assert not mismatched, (
        f"{len(mismatched)} single-value responses changed: {mismatched[:10]}; first diff "
        f"expected={json.dumps(expected[mismatched[0]])[:600]} "
        f"actual={json.dumps(actual[mismatched[0]])[:600]}"
    )
    # The golden is not vacuous: the seeded data reaches every route.
    assert expected["coverage/none"]["body"]["summary"]["total_executions"] > 0
    assert expected["metrics_trends/release"]["body"]["data"]
    assert expected["suite_detail/suite"]["body"]["test_cases"]


# ── 2. multi-value semantics: OR within a dimension, AND across ─────────────


async def _json(world, path, params, headers=None):
    resp = await world.client.get(path, params=params, headers=headers or world.member)
    assert resp.status_code == 200, (path, params, resp.text)
    return resp.json()


def _trend_totals(body) -> dict:
    return {row["date"]: row["total"] for row in body["data"]}


def _add(*maps) -> dict:
    out: dict = {}
    for mapping in maps:
        for key, value in mapping.items():
            out[key] = out.get(key, 0) + value
    return out


async def test_repeated_releases_are_the_union_and_suites_intersect(world):
    base = [("project_id", str(world.p1)), ("days", "90")]
    r1, r2 = str(world.releases[R1_KEY]), str(world.releases[R2_KEY])
    path = "/api/v1/analytics/coverage"

    async def total(*filters):
        body = await _json(world, path, [*base, *filters])
        return body["summary"]["total_executions"]

    only_r1, only_r2 = await total(("release_id", r1)), await total(("release_id", r2))
    assert only_r1 > 0 and only_r2 > 0
    # A run has one primary release, so the two populations are disjoint: OR
    # within the dimension is exactly the sum. (An AND would be empty.)
    assert await total(("release_id", r1), ("release_id", r2)) == only_r1 + only_r2
    # The sentinel mixes with real ids the same way.
    unattributed = await total(("release_id", "unattributed"))
    assert unattributed > 0
    assert await total(("release_id", r1), ("release_id", "unattributed")) == only_r1 + unattributed

    s1, s2 = await total(("suite_name", S1)), await total(("suite_name", S2))
    assert await total(("suite_name", S1), ("suite_name", S2)) == s1 + s2
    # AND across dimensions: (R1 or R2) and (S1 or S2) is the four cells.
    cells = [
        await total(("release_id", r), ("suite_name", s)) for r in (r1, r2) for s in (S1, S2)
    ]
    assert all(cell >= 0 for cell in cells) and sum(cells) > 0
    both = await total(
        ("release_id", r1), ("release_id", r2), ("suite_name", S1), ("suite_name", S2)
    )
    assert both == sum(cells)
    assert both < only_r1 + only_r2, "the suite dimension must narrow, not widen"


async def test_every_route_honours_repeated_values(world):
    r1, r2 = str(world.releases[R1_KEY]), str(world.releases[R2_KEY])
    p1 = ("project_id", str(world.p1))
    rel = [("release_id", r1), ("release_id", r2)]
    sui = [("suite_name", S1), ("suite_name", S2)]

    # Trends sum run aggregates per UTC day, so the union is the per-day sum.
    trends = "/api/v1/metrics/trends"
    one = _trend_totals(await _json(world, trends, [p1, ("days", "60"), ("release_id", r1)]))
    two = _trend_totals(await _json(world, trends, [p1, ("days", "60"), ("release_id", r2)]))
    both = _trend_totals(await _json(world, trends, [p1, ("days", "60"), *rel]))
    assert both == _add(one, two) and both

    summary = "/api/v1/metrics/summary"

    def kpi(body):
        return body["total_executions_7d"]["value"]

    a = kpi(await _json(world, summary, [p1, ("days", "60"), ("release_id", r1)]))
    b = kpi(await _json(world, summary, [p1, ("days", "60"), ("release_id", r2)]))
    assert kpi(await _json(world, summary, [p1, ("days", "60"), *rel])) == a + b
    a = kpi(await _json(world, summary, [p1, ("days", "60"), ("suite_name", S1)]))
    b = kpi(await _json(world, summary, [p1, ("days", "60"), ("suite_name", S2)]))
    assert kpi(await _json(world, summary, [p1, ("days", "60"), *sui])) == a + b

    detail = "/api/v1/analytics/suite-detail"

    def execs(body):
        return body["summary"]["total_executions"]

    a = await _json(world, detail, [p1, ("days", "90"), ("suite_name", S1)])
    b = await _json(world, detail, [p1, ("days", "90"), ("suite_name", S2)])
    ab = await _json(world, detail, [p1, ("days", "90"), *sui])
    assert execs(ab) == execs(a) + execs(b)
    assert ab["suite_name"] == [S1, S2]

    categories = "/api/v1/analytics/failure-categories"

    def count(body):
        return sum(item["count"] for item in body["items"])

    a = count(await _json(world, categories, [p1, ("days", "90"), ("release_id", r1)]))
    b = count(await _json(world, categories, [p1, ("days", "90"), ("release_id", r2)]))
    assert count(await _json(world, categories, [p1, ("days", "90"), *rel])) == a + b

    for path in ("/api/v1/analytics/flaky-tests", "/api/v1/analytics/top-failing"):
        body = await _json(world, path, [p1, ("days", "90"), *rel, *sui])
        assert isinstance(body["items"], list)
    scores = await _json(world, "/api/v1/analytics/flaky-scores", [p1, *rel])
    assert scores["scope"]["membership"] == "release"
    assert scores["scope"]["release_id"] == [r1, r2]
    report = await _json(world, "/api/v1/reports/summary", [p1, ("days", "90"), *rel])
    assert report["project_id"] == str(world.p1)


async def test_duplicates_collapse_to_the_single_value_answer(world):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    r1 = str(world.releases[R1_KEY])
    status, body = await _call(
        world,
        "/api/v1/analytics/coverage",
        [("project_id", str(world.p1)), ("days", "90"), ("release_id", r1),
         ("release_id", r1.upper()), ("suite_name", S1), ("suite_name", S1)],
        world.member,
    )
    case = _without_meta({"c": {"status": status, "body": body}})["c"]
    assert case == golden["coverage/release_suite"]


# ── 3. authorisation covers every id ────────────────────────────────────────


_AUTHZ_ROUTES = (
    ("/api/v1/metrics/summary", [("days", "30")]),
    ("/api/v1/metrics/trends", [("days", "30")]),
    ("/api/v1/analytics/flaky-tests", [("days", "90")]),
    ("/api/v1/analytics/flaky-scores", []),
    ("/api/v1/analytics/failure-categories", [("days", "90")]),
    ("/api/v1/analytics/top-failing", [("days", "90")]),
    ("/api/v1/analytics/coverage", [("days", "90")]),
    ("/api/v1/analytics/suite-detail", [("days", "90"), ("suite_name", S1)]),
    ("/api/v1/reports/summary", [("days", "90")]),
    ("/api/v1/reports/summary/pdf", [("days", "90")]),
)


@pytest.mark.parametrize("path,fixed", _AUTHZ_ROUTES, ids=[p for p, _ in _AUTHZ_ROUTES])
async def test_one_forbidden_release_refuses_the_whole_request(world, path, fixed):
    r1 = str(world.releases[R1_KEY])
    base = [("project_id", str(world.p1)), *fixed]

    forbidden = await world.client.get(
        path, params=[*base, ("release_id", r1), ("release_id", str(world.r9))],
        headers=world.member,
    )
    assert forbidden.status_code == 403, forbidden.text
    body = forbidden.json()
    assert body["code"] == "forbidden" and body["request_id"], body
    assert set(body) <= {"code", "message", "request_id", "detail"}, "no data for R1 either"

    missing = await world.client.get(
        path, params=[*base, ("release_id", str(uuid.uuid4())), ("release_id", r1)],
        headers=world.member,
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "not_found"

    # The readable id alone still answers.
    ok = await world.client.get(path, params=[*base, ("release_id", r1)], headers=world.member)
    assert ok.status_code == 200, ok.text


async def test_role_matrix_for_projects_and_foreign_releases(world):
    p1 = ("project_id", str(world.p1))
    r9 = ("release_id", str(world.r9))
    coverage = "/api/v1/analytics/coverage"
    # A project the caller is not a member of: 403 on the analytics routes...
    resp = await world.client.get(coverage, params=[p1], headers=world.outsider)
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    # ...and the metrics routes' historical empty payload, not a 403 that
    # would confirm the project exists. Release ids are not even looked at.
    resp = await world.client.get(
        "/api/v1/metrics/summary", params=[p1, ("release_id", str(uuid.uuid4()))],
        headers=world.outsider,
    )
    assert resp.status_code == 200
    body = resp.json()
    # The historical empty payload plus the envelope (VIZ-204), which names
    # no project and measures nothing.
    assert set(body) == {"meta"}
    assert body["meta"]["scope"]["projects"] == [] and body["meta"]["measured"] is False
    # The outsider reads their own project's release.
    resp = await world.client.get(
        coverage, params=[("project_id", str(world.p2)), r9], headers=world.outsider
    )
    assert resp.status_code == 200 and resp.json()["summary"]["total_executions"] == 0
    # An admin may read R9, but it is P2's release: pinned to P1 the answer is
    # the empty intersection, exactly as resolve_release_query_scope has it.
    resp = await world.client.get(coverage, params=[p1, r9], headers=world.admin)
    assert resp.status_code == 200 and resp.json()["suites"] == []
    # A member without project_id is scoped to their memberships (P1 only).
    mine = await _json(world, coverage, [("days", "90")])
    pinned = await _json(world, coverage, [p1, ("days", "90")])
    assert mine.pop("meta")["scope"]["projects"] == pinned.pop("meta")["scope"]["projects"]
    assert mine == pinned


# ── 4. validation (VIZ-210) ─────────────────────────────────────────────────


def _many(key: str, n: int, value=None):
    return [(key, value(i) if value else str(uuid.uuid4())) for i in range(n)]


_P = ("project_id", "00000000-0000-4000-8000-000000000001")

#: (params, path, code, param)
_INVALID = (
    ([_P, ("project_id", "00000000-0000-4000-8000-000000000002")], "/api/v1/analytics/coverage",
     "project_single_valued", "project_id"),
    ([_P, ("project_id", "00000000-0000-4000-8000-000000000002")], "/api/v1/metrics/trends",
     "project_single_valued", "project_id"),
    ([("project_id", "all")], "/api/v1/metrics/summary", "project_id_format", "project_id"),
    ([("project_id", "")], "/api/v1/analytics/top-failing", "project_id_format", "project_id"),
    ([_P, ("release_id", "not-a-uuid")], "/api/v1/analytics/coverage", "release_id_format", "release_id"),
    ([_P, ("release_id", "")], "/api/v1/reports/summary", "release_id_format", "release_id"),
    ([_P, *_many("release_id", 21)], "/api/v1/analytics/flaky-tests", "release_cap", "release_id"),
    ([_P, *_many("suite_name", 51, lambda i: f"s{i}")], "/api/v1/analytics/coverage", "suite_cap", "suite_name"),
    ([_P, ("suite_name", "")], "/api/v1/analytics/suite-detail", "suite_name_length", "suite_name"),
    ([_P, ("suite_name", "x" * 501)], "/api/v1/metrics/trends", "suite_name_length", "suite_name"),
    ([_P, ("days", "0")], "/api/v1/analytics/coverage", "window_days_range", "days"),
    ([_P, ("days", "366")], "/api/v1/analytics/coverage", "window_days_range", "days"),
    ([_P, ("days", "91")], "/api/v1/metrics/summary", "window_days_range", "days"),
    ([_P, ("days", "seven")], "/api/v1/metrics/summary", "window_days_range", "days"),
    ([_P, ("from", "2026-09-01"), ("to", "2026-09-10")], "/api/v1/analytics/coverage",
     "window_range_unsupported", "from"),
    ([], "/api/v1/analytics/flaky-scores", "missing_parameter", "project_id"),
    # A route's own non-scope parameter. FastAPI validates it after the scope
    # dependency has authorised, so the project must be one the caller reads.
    ([("project_id", "<P1>"), ("limit", "0")], "/api/v1/analytics/top-failing",
     "invalid_parameter", "limit"),
)


@pytest.mark.parametrize("params,path,code,param", _INVALID, ids=[c for _, _, c, _ in _INVALID])
async def test_invalid_scope_is_a_422_with_the_contract_body(world, params, path, code, param):
    params = [(key, str(world.p1) if value == "<P1>" else value) for key, value in params]
    resp = await world.client.get(path, params=params, headers=world.member)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert set(body) == {"code", "param", "message", "allowed", "request_id", "detail"}, body
    assert (body["code"], body["param"]) == (code, param), body
    assert body["message"] and body["detail"] == body["message"]
    assert body["request_id"] == resp.headers["x-request-id"]
    assert "Traceback" not in resp.text
    if code == "project_single_valued":
        assert "group_by=project" in body["message"]
    if code in ("window_days_range", "release_cap", "suite_cap"):
        assert body["allowed"]["max"] in (90, 365, 20, 50)


async def test_an_unrelated_routes_422_body_is_unchanged(world):
    # Same router, unmarked route: FastAPI's own list body, untouched.
    resp = await world.client.get("/api/v1/metrics/tia-readiness", headers=world.member)
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"detail"} and isinstance(body["detail"], list)
    assert body["detail"][0]["loc"] == ["query", "project_id"]


async def test_request_id_is_validated_echoed_and_in_the_error_body(world):
    good = "req-2026.09:21_ABC"
    resp = await world.client.get(
        "/api/v1/analytics/coverage", params=[("days", "0")],
        headers={**world.member, "X-Request-ID": good},
    )
    assert resp.headers["x-request-id"] == good and resp.json()["request_id"] == good
    for bad in ("x" * 129, "<script>", "has space", ""):
        resp = await world.client.get(
            "/api/v1/analytics/coverage", params=[("project_id", str(world.p1))],
            headers={**world.member, "X-Request-ID": bad},
        )
        assert resp.status_code == 200
        assert _UUID_RE.fullmatch(resp.headers["x-request-id"]), bad


async def test_an_unhandled_error_is_json_with_the_request_id_and_no_trace(world, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.services import analytics_service

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("secret internals: SELECT * FROM users")

    monkeypatch.setattr(analytics_service, "coverage_stats", _boom)
    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
    )
    try:
        resp = await client.get(
            "/api/v1/analytics/coverage", params=[("project_id", str(world.p1))],
            headers={**world.member, "X-Request-ID": "trace-me-1"},
        )
    finally:
        await client.aclose()
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "internal_error" and body["request_id"] == "trace-me-1"
    assert resp.headers["x-request-id"] == "trace-me-1"
    assert "secret internals" not in resp.text and "RuntimeError" not in resp.text
    assert "Traceback" not in resp.text


# ── 5. the single-release plan keeps its index ──────────────────────────────


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        value = value.isoformat()
    return "'" + str(value).replace("'", "''") + "'"


async def test_single_release_trend_uses_the_project_release_index_under_a_generic_plan(world):
    import asyncpg

    captured: list = []

    def _capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "primary_release_id" in statement and "DATE_TRUNC" in statement:
            captured.append((statement, list(parameters or ())))

    event.listen(world.engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await _json(world, "/api/v1/metrics/trends", [
            ("project_id", str(world.p1)), ("days", "30"),
            ("release_id", str(world.releases[R1_KEY])),
        ])
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", _capture)
    assert len(captured) == 1, captured
    statement, parameters = captured[0]

    # A GENERIC plan is what a null-tolerant `(:x IS NULL OR col = :x)` cannot
    # index; a custom plan would fold the NULL test away and hide the loss.
    conn = await asyncpg.connect(_env("TESTLOOKUP_POSTGRES_TEST_DSN").replace("+asyncpg", ""))
    try:
        await conn.execute("SET plan_cache_mode = force_generic_plan")
        await conn.execute("SET enable_seqscan = off")
        await conn.execute(f"PREPARE vizscope_plan AS {statement}")
        args = ", ".join(_literal(value) for value in parameters)
        raw = await conn.fetchval(f"EXPLAIN (FORMAT JSON) EXECUTE vizscope_plan({args})")
    finally:
        await conn.close()
    plan = json.loads(raw)[0]["Plan"]
    hits = [
        node for node in _plan_nodes(plan)
        if node.get("Index Name") == "ix_test_runs_project_release_created"
        and "primary_release_id" in node.get("Index Cond", "")
    ]
    assert hits, f"the release predicate left the index: {json.dumps(plan)[:1500]}"
