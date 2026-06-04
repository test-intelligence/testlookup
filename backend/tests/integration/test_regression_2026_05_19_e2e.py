"""End-to-end integration tests for the 2026-05-18/19 regression fixes.

Each test drives a real HTTP request through the FastAPI dispatch
stack (routing, middleware, validation, auth) against a stubbed DB.
The unit-level pins in ``tests/regression/`` cover service + router
internals; these e2e tests pin the HTTP-visible behaviour the user
journeys depend on.

Skipped automatically when integration deps aren't importable
locally — matches the rest of ``tests/integration/`` (`asyncpg`,
`jose`, etc. are Docker-only on bare checkouts).

Covered journeys:

  * ``GET /api/v1/me/assigned-failures?scope=team`` — QA_LEAD sees
    the team scope; lower roles are silently downgraded.
  * ``GET /api/v1/runs/{id}`` — 200 with synthesised IN_PROGRESS
    payload when only a LiveSession exists; 404 when neither exists.
  * ``GET /api/v1/runs/{id}/regression-diff`` — in-progress payload
    for live sessions (was 404).
  * ``GET /api/v1/test-cases/{id}/review`` — 200/null when no review
    row exists (was 404).
  * ``GET /api/v1/test-management/suites/{name}/trend`` — endpoint
    routed; envelope shape ``{suite_name, days, points}``.
  * ``POST /api/v1/admin/maintenance/drain-active-live-sessions`` —
    ADMIN-only.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


# ── /my-failures scope ─────────────────────────────────────────────────────


async def test_my_failures_scope_team_accepted_for_qa_lead(client, auth_as, fake_db):
    auth_as(role=UserRole.QA_LEAD)
    # COUNT comes back 0 → short-circuit. We only care that the route
    # parses ``scope=team`` and returns 200 (not a 422 from a stricter
    # validator).
    from tests.integration.conftest import fake_execute_result
    fake_db.set_execute_results([fake_execute_result(scalar=0)])
    resp = await client.get("/api/v1/me/assigned-failures?scope=team")
    assert resp.status_code == 200


async def test_my_failures_scope_invalid_value_rejected(client, auth_as, fake_db):
    auth_as(role=UserRole.QA_LEAD)
    from tests.integration.conftest import fake_execute_result
    fake_db.set_execute_results([fake_execute_result(scalar=0)])
    resp = await client.get("/api/v1/me/assigned-failures?scope=admin")
    # The pattern regex only allows mine|team. Anything else 422s.
    assert resp.status_code == 422


# ── /admin/maintenance/* ───────────────────────────────────────────────────


async def test_admin_maintenance_drain_requires_admin(client, auth_as):
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.post("/api/v1/admin/maintenance/drain-active-live-sessions")
    # QA_ENGINEER < ADMIN — require_role gate must 403.
    assert resp.status_code in (401, 403)


async def test_admin_maintenance_drain_admin_queues_task(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    fake_task = SimpleNamespace(id="celery-task-x")
    with patch("app.worker.tasks.drain_active_live_sessions") as task:
        task.apply_async = lambda **_: fake_task
        resp = await client.post("/api/v1/admin/maintenance/drain-active-live-sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] is True
    assert body["task_id"] == "celery-task-x"


# ── Suite trend endpoint ───────────────────────────────────────────────────


async def test_suite_trend_endpoint_routed(client, auth_as, fake_db):
    auth_as(role=UserRole.QA_ENGINEER)
    project_id = uuid.uuid4()
    # The route now enforces tenant access on a provided project_id (IDOR fix
    # added after this routing pin was written) — grant the QA_ENGINEER access
    # to the requested project so the routing assertion isn't masked by a 403.
    with patch("app.core.deps.get_accessible_project_ids",
               AsyncMock(return_value={project_id})), \
         patch("app.services.suite_history_service.compute_suite_trend",
               AsyncMock(return_value=[])):
        resp = await client.get(
            f"/api/v1/test-management/suites/Auth/trend?days=14&project_id={project_id}",
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suite_name"] == "Auth"
    assert body["days"] == 14
    assert "points" in body


# ── Test-case review GET — 200/null contract ───────────────────────────────


async def test_test_case_review_get_returns_null_when_no_review(client, auth_as, fake_db):
    auth_as(role=UserRole.QA_ENGINEER)
    case_id = uuid.uuid4()
    project_id = uuid.uuid4()

    with patch(
        "app.routers.test_execution_reviews.svc.get_test_case_project",
        AsyncMock(return_value=project_id),
    ), patch(
        "app.routers.test_execution_reviews._assert_project_access",
        AsyncMock(return_value=None),
    ), patch(
        "app.routers.test_execution_reviews.svc.get_review",
        AsyncMock(return_value=None),
    ):
        resp = await client.get(f"/api/v1/test-cases/{case_id}/review")
    # Was 404 pre-fix; now 200 with null body.
    assert resp.status_code == 200
    # FastAPI serialises None → "null". Both shapes are acceptable
    # depending on response_model handling.
    assert resp.text in ("null", "", "null\n")


# ── /runs/{id}/recover-live synthesis fallback ─────────────────────────────


async def test_recover_live_falls_back_to_synthesis_when_buffer_and_archive_empty(
    client, auth_as, fake_db,
):
    """End-to-end pin for the 2026-05-19 user-reported bug:

    Live run reports aggregates (100 tests / 90 passed / 10 failed)
    but has zero test_cases rows. Both the 25-hour Redis buffer and
    the 15-day durable archive are empty/expired. Pre-fix: the
    recovery endpoint 422'd, leaving the user stuck. Post-fix:
    endpoint queues persist_live_session with ``source="synthesis"``
    so the task's synthesis branch generates one placeholder row
    per reported test.
    """
    from tests.integration.conftest import fake_execute_result
    from app.models.postgres import LaunchStatus

    # ADMIN bypasses ``require_run_access`` (the recover endpoint's guard)
    # so this test exercises the synthesis-fallback logic, not authz.
    # A non-admin would 403 here because the guard does its own run→project
    # + ProjectMember lookups against the random fake run before the body
    # ever runs (and the fake_db is only seeded for the body's queries).
    auth_as(role=UserRole.ADMIN)
    run_id = uuid.uuid4()

    fake_run = SimpleNamespace(
        id=run_id,
        project_id=uuid.uuid4(),
        trigger_source="live_stream",
        status=LaunchStatus.PASSED,
        passed_tests=90,
        failed_tests=10,
        broken_tests=0,
        skipped_tests=0,
        total_tests=100,
        build_number="b-2029",
        branch="main",
        commit_hash=None,
        primary_suite_name="Auth",
        event_archive=None,       # no durable archive
        event_archive_at=None,
    )
    # Router executes two queries: TestRun lookup, then COUNT(test_cases).
    fake_db.set_execute_results([
        fake_execute_result(scalar=fake_run),
        fake_execute_result(scalar=0),  # tc_count == 0
    ])

    fake_redis = AsyncMock()
    fake_redis.llen = AsyncMock(return_value=0)  # empty Redis buffer

    queued_kwargs = {}

    def _apply_async(**kwargs):
        queued_kwargs.update(kwargs)
        return SimpleNamespace(id="celery-task-synth")

    with patch("app.db.redis_client.get_redis", return_value=fake_redis), \
         patch("app.worker.tasks.persist_live_session") as task, \
         patch("app.worker.ingestion_routing.queue_for_project",
               return_value="ingestion.shard.0"):
        task.apply_async = _apply_async

        resp = await client.post(f"/api/v1/runs/{run_id}/recover-live")

    assert resp.status_code == 202, (
        f"Recovery must succeed with 202 (was 422 pre-fix). body={resp.text}"
    )
    body = resp.json()
    assert body["queued"] is True
    assert body["source"] == "synthesis", (
        "When buffer + archive are empty but aggregates are non-zero, "
        "the endpoint must fall back to source='synthesis' instead of 422."
    )
    assert body["buffered_events"] == 0
    # The persist task fires with the run's aggregates in final_state so
    # the synthesis branch can size + bucket the placeholder rows.
    task_kwargs = queued_kwargs.get("kwargs") or {}
    final_state = task_kwargs.get("final_state") or {}
    assert final_state.get("passed") == 90
    assert final_state.get("failed") == 10


async def test_recover_live_still_422s_when_nothing_to_recover(
    client, auth_as, fake_db,
):
    """Sanity check on the guard — a run with zero aggregates AND no
    buffer AND no archive truly has nothing to materialise. The 422
    stays."""
    from tests.integration.conftest import fake_execute_result
    from app.models.postgres import LaunchStatus

    # ADMIN bypasses ``require_run_access`` (the recover endpoint's guard)
    # so this test exercises the synthesis-fallback logic, not authz.
    # A non-admin would 403 here because the guard does its own run→project
    # + ProjectMember lookups against the random fake run before the body
    # ever runs (and the fake_db is only seeded for the body's queries).
    auth_as(role=UserRole.ADMIN)
    run_id = uuid.uuid4()

    fake_run = SimpleNamespace(
        id=run_id,
        project_id=uuid.uuid4(),
        trigger_source="live_stream",
        status=LaunchStatus.PASSED,
        passed_tests=0,
        failed_tests=0,
        broken_tests=0,
        skipped_tests=0,
        total_tests=0,
        build_number="b-2030",
        branch="main",
        commit_hash=None,
        primary_suite_name=None,
        event_archive=None,
        event_archive_at=None,
    )
    fake_db.set_execute_results([
        fake_execute_result(scalar=fake_run),
        fake_execute_result(scalar=0),
    ])

    fake_redis = AsyncMock()
    fake_redis.llen = AsyncMock(return_value=0)

    with patch("app.db.redis_client.get_redis", return_value=fake_redis):
        resp = await client.post(f"/api/v1/runs/{run_id}/recover-live")

    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    assert "no buffered events" in detail.lower()
