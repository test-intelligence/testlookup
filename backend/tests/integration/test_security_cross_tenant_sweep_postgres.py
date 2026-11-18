"""GAP-B01 / B09 / B11: a member of project A reads and writes nothing of project B.

``test_architectural_authorization.py`` proves a project guard is DECLARED
for every path, query and body id; it can't see a guard that is called but
doesn't refuse (a local helper with an early return, a resolver that hands
back the requested id, a query that ignores the resolved scope). The nearest
behavioural harness, ``test_every_get_route_answers_without_500_postgres.py``,
never swaps in another project's id.

Two projects, each with a run, a result, a suite, a release, a managed case,
a plan, a strategy, a shared saved view, a suite owner and a suite review,
all carrying the project's own marker string. A VIEWER and a QA_LEAD belong
to project A only. Then, through the real app (real auth, real guards):

* every GET route with a project / run / release / case / plan / strategy /
  suite / view id in its PATH, filled with B's ids;
* every GET route with such an id in its QUERY, filled with B's ids while
  the path carries A's (the mixed case);

must answer 403/404, or a body with neither B's marker nor any B id the
caller did not send. Each route is also called with A's ids: where A's marker
comes back the route shows data, and a named set of routes (runs, releases,
cases, plans, strategies, suite owners, saved views, ...) must be among them,
so an empty answer is never read as a pass. Then the POST routes that take a
``project_id`` in the BODY (cases, plans, suites, releases, saved views, chat
sessions, and the two async AI routes of GAP-B11) are sent B's id by A's
QA_LEAD: refused, no row lands in B, nothing is queued for B -- while the
same body naming A succeeds.

Scoped to stay well under two minutes: the three SSE routes are skipped
(they would stream), Mongo gets a client with a short server-selection
timeout (no Mongo data is seeded), and Celery dispatch is recorded, bound to
each task's signature, never sent. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``,
``DATABASE_URL`` (the same database) and ``REDIS_URL``.
"""

from __future__ import annotations

import inspect
import io
import os
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tests.integration.security_seed_cleanup import (
    leftover_rows,
    purge_redis_keys,
    purge_seeds,
)

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
#: Server-sent-event routes: a broken guard would stream forever instead of answering.
SSE = {
    "/api/v1/agents/invocations/{invocation_id}/events",
    "/api/v1/stream/sse/{project_id}",
    "/api/v1/projects/{project_id}/activity/stream",
}
#: path parameter -> the seeded entity it names
PATH_IDS = {
    "project_id": "project",
    "run_id": "run",
    "test_run_id": "run",
    "release_id": "release",
    "case_id": "case",
    "test_case_id": "result",
    "test_id": "result",
    "plan_id": "plan",
    "strategy_id": "strategy",
    "suite_id": "suite",
    "view_id": "view",
    "suite_name": "suite_name",
}
#: query parameter -> the seeded entity it names
QUERY_IDS = {
    "project_id": "project",
    "active_project_id": "project",
    "bucket_project": "project",
    "run_id": "run",
    "test_run_id": "run",
    "baseline_run_id": "run",
    "release_id": "release",
    "history_release_id": "release",
    "bucket_release": "release",
    "test_case_id": "result",
    "suite_id": "suite",
    "saved_view_id": "view",
}
#: Routes that must show A's marker to A's members when called with A's ids
#: (the positive control: an empty answer for B counts only where A's own ids show A's data).
MUST_SHOW = {
    "path": {
        "GET /api/v1/runs/{run_id}",
        "GET /api/v1/test-management/cases/{case_id}",
        "GET /api/v1/test-management/plans/{plan_id}",
        "GET /api/v1/test-management/strategies/{strategy_id}",
        "GET /api/v1/test-management/suite-reviews/by-run/{test_run_id}",
    },
    "query": {
        "GET /api/v1/runs",
        "GET /api/v1/releases",
        "GET /api/v1/test-management/cases",
        "GET /api/v1/test-management/plans",
        "GET /api/v1/test-management/strategies",
        "GET /api/v1/test-management/suite-owners",
        "GET /api/v1/test-management/suite-reviews",
        "GET /api/v1/saved-views",
    },
}


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


async def _seed(
    db, side: str, ids: dict, mark: str, member_id: uuid.UUID, now: datetime
) -> None:
    from app.models.postgres import (
        ManagedTestCase,
        Project,
        Release,
        SavedView,
        SuiteRunReview,
        TestCase,
        TestPlan,
        TestRun,
        TestStrategy,
        TestSuite,
        TestSuiteOwner,
    )

    db.add(
        Project(
            id=ids["project"],
            name=f"secb7 {mark}",
            slug=f"secb7-{mark}",
            is_active=True,
        )
    )
    await db.flush()
    db.add(
        TestSuite(id=ids["suite"], project_id=ids["project"], name=ids["suite_name"])
    )
    db.add(
        Release(
            id=ids["release"],
            project_id=ids["project"],
            name=f"R-{mark}",
            status="in_progress",
        )
    )
    await db.flush()
    db.add(
        TestRun(
            id=ids["run"],
            project_id=ids["project"],
            build_number=f"build-{mark}",
            status="FAILED",
            total_tests=1,
            failed_tests=1,
            primary_release_id=ids["release"],
            created_at=now - timedelta(minutes=5),
            start_time=now - timedelta(minutes=5),
        )
    )
    await db.flush()
    db.add(
        TestCase(
            id=ids["result"],
            test_run_id=ids["run"],
            test_fingerprint=f"fp-{mark}",
            test_name=f"test_{mark}",
            suite_name=ids["suite_name"],
            status="FAILED",
            error_message=f"AssertionError {mark}",
        )
    )
    db.add(
        ManagedTestCase(id=ids["case"], project_id=ids["project"], title=f"case {mark}")
    )
    db.add(TestPlan(id=ids["plan"], project_id=ids["project"], name=f"plan {mark}"))
    db.add(
        TestStrategy(
            id=ids["strategy"], project_id=ids["project"], name=f"strategy {mark}"
        )
    )
    db.add(
        SavedView(
            id=ids["view"],
            user_id=member_id,
            project_id=ids["project"],
            name=f"view {mark}",
            filters={},
            is_shared=True,
        )
    )
    db.add(
        TestSuiteOwner(
            project_id=ids["project"],
            suite_name=ids["suite_name"],
            owner_user_id=member_id,
        )
    )
    db.add(
        SuiteRunReview(
            project_id=ids["project"],
            suite_name=ids["suite_name"],
            test_run_id=ids["run"],
            reviewer_user_id=member_id,
            state="pending",
            note=f"review {mark}",
        )
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    _env("DATABASE_URL")
    _env("REDIS_URL")
    from celery.app.task import Task
    from motor.motor_asyncio import AsyncIOMotorClient

    import app.db.mongo as app_mongo
    from app.core.config import settings
    from app.db import postgres as app_postgres
    from app.db.redis_client import get_redis
    from app.models.postgres import ProjectMember, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    await app_postgres.dispose_engine_for_loop()
    sessions = app_postgres.get_session_factory()

    # Mongo: no data is seeded there; a short server selection keeps a missing
    # Mongo from costing five seconds a route (CI has one; local runs may not).
    saved_mongo = app_mongo._client
    app_mongo._client = AsyncIOMotorClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=300,
        connectTimeoutMS=300,
    )
    # Celery: record every dispatch, bound to the task's real signature.
    queued: list[tuple[str, tuple, dict]] = []
    real_apply_async = Task.apply_async

    def _apply_async(self, args=None, kwargs=None, **_options):
        inspect.signature(self.run).bind(*(args or ()), **(kwargs or {}))
        queued.append((self.name, tuple(args or ()), dict(kwargs or {})))
        return SimpleNamespace(id=f"secb7-task-{len(queued)}")

    Task.apply_async = _apply_async

    tag = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)
    marks = {"a": f"secb7a{tag}", "b": f"secb7b{tag}"}
    ids = {
        side: {
            **{
                key: uuid.uuid4()
                for key in (
                    "project",
                    "run",
                    "release",
                    "suite",
                    "result",
                    "case",
                    "plan",
                    "strategy",
                    "view",
                )
            },
            "suite_name": f"suite-{marks[side]}",
        }
        for side in ("a", "b")
    }
    users = {"viewer": uuid.uuid4(), "lead": uuid.uuid4(), "owner_b": uuid.uuid4()}
    roles = {
        "viewer": UserRole.VIEWER,
        "lead": UserRole.QA_LEAD,
        "owner_b": UserRole.QA_LEAD,
    }
    try:
        async with sessions() as db:
            for name, uid in users.items():
                db.add(
                    User(
                        id=uid,
                        username=f"secb7-{name}-{tag}",
                        email=f"secb7-{name}-{tag}@example.test",
                        hashed_password="!unusable",
                        role=roles[name],
                        is_active=True,
                    )
                )
            await db.flush()
            await _seed(db, "a", ids["a"], marks["a"], users["lead"], now)
            await _seed(db, "b", ids["b"], marks["b"], users["owner_b"], now)
            db.add(
                ProjectMember(
                    project_id=ids["a"]["project"],
                    user_id=users["viewer"],
                    role=UserRole.VIEWER,
                )
            )
            db.add(
                ProjectMember(
                    project_id=ids["a"]["project"],
                    user_id=users["lead"],
                    role=UserRole.QA_LEAD,
                )
            )
            db.add(
                ProjectMember(
                    project_id=ids["b"]["project"],
                    user_id=users["owner_b"],
                    role=UserRole.QA_LEAD,
                )
            )
            await db.commit()
            tokens = {
                name: await issue_access_jwt(
                    db, str(users[name]), timedelta(minutes=30)
                )
                for name in ("viewer", "lead")
            }
        yield SimpleNamespace(
            sessions=sessions,
            ids=ids,
            marks=marks,
            users=users,
            tokens=tokens,
            queued=queued,
        )
    finally:
        Task.apply_async = real_apply_async
        try:
            await purge_redis_keys(get_redis(), *(str(ids[s]["project"]) for s in ids))
            await purge_seeds(
                sessions,
                project_ids=[ids["a"]["project"], ids["b"]["project"]],
                user_ids=list(users.values()),
            )
        finally:
            app_mongo._client.close()
            app_mongo._client = saved_mongo
            await app_postgres.dispose_engine_for_loop()


# ── the GET sweep ───────────────────────────────────────────────────────────


def _routes():
    from fastapi.routing import APIRoute

    from app.main import app

    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and "GET" in route.methods
            and route.path.startswith("/api/v1")
            and route.path not in SSE
        ):
            yield route


def _path_params(route) -> list[str]:
    return re.findall(r"\{(\w+)(?::[^}]*)?\}", route.path)


def _query_params(route) -> list[str]:
    return [p.name for p in route.dependant.query_params if p.name in QUERY_IDS]


def _request(route, path_side: dict, query_side: dict) -> tuple[str, dict, set[str]]:
    """(path, query, every id value sent) with path ids from one side and query ids from another."""

    def value(entity, side):
        return side["suite_name"] if entity == "suite_name" else str(side[entity])

    sent: set[str] = set()

    def fill(match):
        entity = PATH_IDS.get(match.group(1))
        if entity is None:
            return PLACEHOLDER
        sent.add(value(entity, path_side))
        return value(entity, path_side)

    path = re.sub(r"\{(\w+)(?::[^}]*)?\}", fill, route.path)
    query = {name: value(QUERY_IDS[name], query_side) for name in _query_params(route)}
    sent.update(query.values())
    return path, query, sent


def _body_text(resp) -> tuple[str, bool]:
    """The response as text, and whether it was opaque (a binary body that can't be searched)."""
    body = resp.content
    if body[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                return "\n".join(
                    archive.read(n).decode("utf-8", "replace")
                    for n in archive.namelist()
                ), False
        except zipfile.BadZipFile:
            return "", True
    if body[:5] == b"%PDF-":
        return "", True
    return body.decode("utf-8", "replace"), False


def _b_secrets(world, sent: set[str]) -> set[str]:
    """B's marker, plus every B id the caller did not send itself."""
    b = world.ids["b"]
    entity_ids = {
        str(b[k])
        for k in (
            "project",
            "run",
            "release",
            "suite",
            "result",
            "case",
            "plan",
            "strategy",
            "view",
        )
    }
    return {world.marks["b"]} | (entity_ids - sent)


async def _sweep(world, user: str, routes, path_side: str, query_side_b: bool) -> dict:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    a, b = world.ids["a"], world.ids["b"]
    headers = {"Authorization": f"Bearer {world.tokens[user]}"}
    out = {"leaks": [], "shows_a": set(), "statuses": {}, "count": 0}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://sweep.test",
        timeout=60,
    ) as client:
        for route in routes:
            key = f"GET {route.path}"
            # A's ids everywhere: does this route show data at all?
            path, query, _sent = _request(route, a, a)
            resp_a = await client.get(path, params=query, headers=headers)
            text_a, opaque_a = _body_text(resp_a)
            if resp_a.status_code < 300 and (opaque_a or world.marks["a"] in text_a):
                out["shows_a"].add(key)
            # B's ids: in the path (path sweep), or in the query under A's path (mixed sweep).
            path, query, sent = _request(
                route, b if path_side == "b" else a, b if query_side_b else a
            )
            resp = await client.get(path, params=query, headers=headers)
            out["count"] += 1
            out["statuses"].setdefault(resp.status_code, []).append(key)
            text, opaque = _body_text(resp)
            # An id or suite name the caller sent, echoed back, is no leak.
            for echoed in sorted(sent, key=len, reverse=True):
                text = text.replace(echoed, "<sent>")
            found = sorted(s for s in _b_secrets(world, sent) if s in text)
            if resp.status_code < 300 and (found or opaque):
                out["leaks"].append(
                    f"{key} {path} {query} -> {resp.status_code} shows {found or 'an opaque body'}"
                )
            elif world.marks["b"] in text:
                out["leaks"].append(
                    f"{key} {path} {query} -> {resp.status_code} names B's marker in an error"
                )
    return out


def _path_routes():
    return [r for r in _routes() if any(p in PATH_IDS for p in _path_params(r))]


def _query_routes():
    return [r for r in _routes() if _query_params(r)]


def _assert_clean(result: dict, user: str, kind: str, at_least: int) -> None:
    assert result["count"] >= at_least, (
        f"only {result['count']} routes swept -- did route discovery break?"
    )
    assert 429 not in result["statuses"], (
        f"rate limited, the sweep proves nothing: {result['statuses'][429][:5]}"
    )
    missing = sorted(MUST_SHOW[kind] - result["shows_a"])
    assert not missing, (
        f"positive control: A's ids did not show A's marker to its {user} on {missing}"
    )
    assert not result["leaks"], (
        f"{len(result['leaks'])} route(s) showed project B to a {user} of project A only:\n  "
        + "\n  ".join(result["leaks"])
    )


@pytest.mark.parametrize("user", ["viewer", "lead"])
async def test_no_get_route_shows_project_b_for_its_path_ids(world, user):
    result = await _sweep(world, user, _path_routes(), path_side="b", query_side_b=True)

    _assert_clean(result, user, "path", at_least=100)


@pytest.mark.parametrize("user", ["viewer", "lead"])
async def test_no_get_route_shows_project_b_for_its_query_ids(world, user):
    result = await _sweep(
        world, user, _query_routes(), path_side="a", query_side_b=True
    )

    _assert_clean(result, user, "query", at_least=60)


# ── POST bodies that name project B ─────────────────────────────────────────


def _post_bodies(project_id: uuid.UUID, mark: str) -> dict[str, dict]:
    pid = str(project_id)
    return {
        "/api/v1/test-management/cases": {
            "project_id": pid,
            "title": f"posted case {mark}",
        },
        "/api/v1/test-management/plans": {
            "project_id": pid,
            "name": f"posted plan {mark}",
        },
        "/api/v1/test-management/plans/ai-create/async": {"project_id": pid},
        "/api/v1/test-management/strategies/ai-generate/async": {
            "project_id": pid,
            "project_context": f"posted context {mark}",
        },
        "/api/v1/suites": {"project_id": pid, "name": f"posted-suite-{mark}"},
        "/api/v1/releases": {"project_id": pid, "name": f"posted-R-{mark}"},
        "/api/v1/saved-views": {
            "project_id": pid,
            "name": f"posted view {mark}",
            "filters": {},
        },
        "/api/v1/chat/sessions": {"project_id": pid},
    }


async def test_post_bodies_naming_project_b_are_refused_and_leave_nothing_in_b(world):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    a, b = world.ids["a"]["project"], world.ids["b"]["project"]
    headers = {"Authorization": f"Bearer {world.tokens['lead']}"}
    rows_before = await leftover_rows(world.sessions, project_ids=[b])
    accepted_for_a, accepted_for_b = {}, {}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://sweep.test",
        timeout=60,
    ) as client:
        for path, body in _post_bodies(a, world.marks["a"]).items():
            accepted_for_a[path] = (
                await client.post(path, json=body, headers=headers)
            ).status_code
        queued_for_a = len(world.queued)
        for path, body in _post_bodies(b, world.marks["b"]).items():
            accepted_for_b[path] = (
                await client.post(path, json=body, headers=headers)
            ).status_code

    # Control: every body is valid and A's QA_LEAD may post it for A.
    assert all(200 <= s < 300 for s in accepted_for_a.values()), accepted_for_a
    assert queued_for_a >= 2, world.queued  # the two async AI routes queued for A
    # The same bodies naming B: refused, nothing written to B, nothing queued for B.
    assert all(s in (403, 404) for s in accepted_for_b.values()), accepted_for_b
    assert await leftover_rows(world.sessions, project_ids=[b]) == rows_before
    named_b = [q for q in world.queued if str(b) in repr(q)]
    assert named_b == [], f"queued against project B: {named_b}"
