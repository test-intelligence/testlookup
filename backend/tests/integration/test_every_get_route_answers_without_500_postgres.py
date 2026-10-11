"""Every GET endpoint answers without a 500, for an admin and for a viewer.

Owner request 2026-10-10: "all the page navigations should be tested for 500
internal server error". Pages are covered by the route sweep in
``frontend/e2e/sweeps`` against a live deployment; this is the CI half -- the
real app (routers, middleware, auth, dependencies) in-process against the CI
Postgres/Mongo/Redis, every ``GET /api/v1`` route with its path parameters
filled from a seeded project. Any 500 fails the build, with the route named.

A 404/403/422 is a legitimate answer (an id the route does not know, a role
it refuses). 502/503/504 mean a dependency the CI job does not run (an LLM, a
vector store); they are listed in the failure message's tail but do not fail
it. What must never happen is an unhandled exception -- the sweep found one on
its first run: ``GET /training/status`` 500'd for every role on the homelab.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

#: Routes that stream or download -- not a JSON answer to judge.
SKIP = re.compile(r"/(export|download|stream|events|sse|ws)\b|/metrics$", re.I)
PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    if not os.environ.get("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL must point the app at the same database")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    _dsn()
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import (
        Project, ProjectMember, Release, TestCase, TestRun, TestSuite, User, UserRole,
    )
    from app.services.auth_session_tokens import issue_access_jwt

    tag = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)
    pid, run_id, tc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    suite_id, release_id = uuid.uuid4(), uuid.uuid4()
    admin_id, viewer_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Project(id=pid, name=f"sweep-{tag}", slug=f"sweep-{tag}", is_active=True))
        db.add_all([
            User(id=admin_id, username=f"sweep-admin-{tag}", email=f"a-{tag}@example.test",
                 hashed_password="x", role=UserRole.ADMIN, is_active=True),
            User(id=viewer_id, username=f"sweep-viewer-{tag}", email=f"v-{tag}@example.test",
                 hashed_password="x", role=UserRole.VIEWER, is_active=True),
        ])
        await db.flush()
        db.add(ProjectMember(project_id=pid, user_id=viewer_id, role=UserRole.VIEWER))
        db.add(TestSuite(id=suite_id, project_id=pid, name=f"suite-{tag}"))
        db.add(Release(id=release_id, project_id=pid, name=f"R-{tag}", status="in_progress"))
        await db.flush()
        db.add(TestRun(id=run_id, project_id=pid, build_number=f"b-{tag}", status="FAILED",
                       total_tests=1, failed_tests=1, primary_release_id=release_id,
                       created_at=now - timedelta(minutes=5), start_time=now - timedelta(minutes=5)))
        await db.flush()
        db.add(TestCase(id=tc_id, test_run_id=run_id, test_fingerprint=f"{tag}-fp", test_name="test_sweep",
                        suite_name=f"suite-{tag}", status="FAILED", error_message="AssertionError"))
        await db.commit()
        tokens = {
            "admin": await issue_access_jwt(db, str(admin_id), timedelta(minutes=30)),
            "viewer": await issue_access_jwt(db, str(viewer_id), timedelta(minutes=30)),
        }
    fill = {
        "project_id": str(pid), "run_id": str(run_id), "test_run_id": str(run_id),
        "test_id": str(tc_id), "test_case_id": str(tc_id), "case_id": str(tc_id),
        "suite_id": str(suite_id), "release_id": str(release_id), "user_id": str(admin_id),
    }
    try:
        yield {"tokens": tokens, "fill": fill, "project_id": str(pid)}
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
            await db.execute(text("DELETE FROM users WHERE id IN (:a, :v)"), {"a": admin_id, "v": viewer_id})
            await db.commit()


def _get_routes():
    from fastapi.routing import APIRoute

    from app.main import app

    for route in app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods and route.path.startswith("/api/v1") \
                and not SKIP.search(route.path):
            yield route


async def _sweep(world, role):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    failures, unavailable, swept = [], [], 0
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://sweep.test", timeout=60) as client:
        for route in _get_routes():
            path = re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: world["fill"].get(m.group(1), PLACEHOLDER), route.path)
            query = {}
            if any(p.name == "project_id" for p in route.dependant.query_params):
                query["project_id"] = world["project_id"]
            r = await client.get(path, params=query, headers={"Authorization": f"Bearer {world['tokens'][role]}"})
            swept += 1
            if r.status_code == 500:
                failures.append(f"{route.path} -> 500 {r.text[:160]}")
            elif r.status_code > 500:
                unavailable.append(f"{route.path} -> {r.status_code}")
    return failures, unavailable, swept


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_no_get_endpoint_answers_500(world, role):
    failures, unavailable, swept = await _sweep(world, role)
    # A sweep that found nothing to call proves nothing (the app had 272 GET routes).
    assert swept >= 200, f"only {swept} GET routes swept -- did route discovery break?"
    assert not failures, (
        f"{len(failures)} GET endpoint(s) answered 500 as {role}:\n  " + "\n  ".join(failures)
        + (f"\n(dependency unavailable, not failing: {len(unavailable)})" if unavailable else "")
    )
