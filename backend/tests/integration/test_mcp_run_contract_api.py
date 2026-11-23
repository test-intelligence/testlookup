"""Contracts consumed by MCP clients, through the mounted FastAPI application."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from app.models.postgres import DeepFinding, TestRun as RunRecord, UserRole
from app.routers import deep_investigation, runs
from app.services import pipeline_admission
from tests.integration.conftest import fake_execute_result

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_PATH = f"/api/v1/runs/{RUN_ID}"
DEEP_PATH = f"/api/v1/deep-investigate/{RUN_ID}"


async def test_run_listing_accepts_newest_order_and_all_time(client, auth_as, monkeypatch):
    auth_as(role=UserRole.ADMIN)
    listing = AsyncMock(return_value=([], 0, 0))
    monkeypatch.setattr(runs, "list_project_runs", listing)

    response = await client.get("/api/v1/runs", params={"order": "newest", "days": 0, "page": 2, "size": 10})

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "page": 2, "size": 10, "pages": 0}
    listing.assert_awaited_once()
    assert listing.await_args.kwargs["order"] == "newest"
    assert listing.await_args.kwargs["days"] is None


@pytest.mark.parametrize("rate", [None, 0.0, 75.0], ids=["unmeasured", "zero", "measured"])
async def test_run_detail_preserves_a_null_pass_rate(client, auth_as, fake_db, rate):
    auth_as(role=UserRole.ADMIN)
    run = RunRecord(
        id=RUN_ID,
        project_id=PROJECT_ID,
        build_number="checkout-42",
        status="PASSED" if rate is None else "FAILED",
        pass_rate=rate,
    )
    fake_db.set_execute_results([fake_execute_result(scalar=run)])

    response = await client.get(RUN_PATH)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(RUN_ID)
    assert response.json()["pass_rate"] == rate
    assert fake_db.committed == 0


async def test_deep_findings_serve_the_stored_root_cause(client, auth_as, fake_db):
    auth_as(role=UserRole.ADMIN)
    finding = DeepFinding(
        test_run_id=RUN_ID,
        cluster_id="checkout",
        root_cause="The checkout service waited for a closed connection.",
        failure_category="INFRASTRUCTURE",
        confidence_score=72,
        log_evidence={"origin": "pipeline", "confidence_basis": "heuristic_estimate"},
    )
    fake_db.set_execute_results([fake_execute_result(scalars_all=[finding])])

    response = await client.get(f"{DEEP_PATH}/findings")

    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["cluster_id"] == finding.cluster_id
    assert row["root_cause"] == finding.root_cause
    assert row["failure_category"] == "INFRASTRUCTURE"
    assert row["confidence_score"] == 72
    assert row["origin"] == "pipeline"
    assert fake_db.committed == 0


@pytest.fixture
def deep_dispatch(auth_as, fake_db, monkeypatch):
    auth_as(role=UserRole.ADMIN)
    run = SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, build_number="checkout-42")
    fake_db.set_execute_results([fake_execute_result(scalar=run)])
    monkeypatch.setattr(deep_investigation, "require_deep_investigation", AsyncMock())
    monkeypatch.setattr(pipeline_admission, "runs_with_in_progress_pipeline", AsyncMock(return_value=set()))
    monkeypatch.setattr(deep_investigation, "record_activity", AsyncMock())
    dispatch = Mock(return_value=SimpleNamespace(id="qa-task-1"))
    monkeypatch.setattr(deep_investigation.run_agent_pipeline, "apply_async", dispatch)
    return dispatch


@pytest.mark.parametrize(
    ("body", "workflow_type"),
    [({}, "deep"), ({"mode": "deep"}, "deep"), ({"mode": "offline"}, "offline")],
    ids=["default", "deep", "offline"],
)
async def test_deep_trigger_dispatches_the_selected_mode(client, deep_dispatch, body, workflow_type):
    response = await client.post(DEEP_PATH, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == "qa-task-1"
    assert response.json()["already_in_progress"] is False
    deep_dispatch.assert_called_once()
    assert deep_dispatch.call_args.kwargs["kwargs"]["workflow_type"] == workflow_type


async def test_deep_trigger_refuses_an_unknown_mode_before_dispatch(client, deep_dispatch):
    response = await client.post(DEEP_PATH, json={"mode": "rules"})

    assert (response.status_code, deep_dispatch.call_count) == (422, 0), response.text


async def test_deep_trigger_reuses_an_active_pipeline(client, deep_dispatch, monkeypatch):
    pipeline_id = UUID("33333333-3333-4333-8333-333333333333")
    monkeypatch.setattr(pipeline_admission, "runs_with_in_progress_pipeline", AsyncMock(return_value={RUN_ID}))
    monkeypatch.setattr(
        pipeline_admission, "latest_in_progress_pipeline", AsyncMock(return_value=SimpleNamespace(id=pipeline_id))
    )

    response = await client.post(DEEP_PATH, json={"mode": "deep"})

    assert response.status_code == 200, response.text
    existing = response.json()
    assert existing["already_in_progress"] is True
    assert existing["pipeline_run_id"] == str(pipeline_id)
    assert existing["task_id"] is None
    deep_dispatch.assert_not_called()


# ── Acceptance cases added with the TL-QA fixes ──────────────────────────────


@pytest.mark.parametrize("mode", ["rules", "DEEP", "", None, 7], ids=["rules", "upper", "empty", "null", "number"])
async def test_deep_trigger_names_the_mode_field_and_records_no_activity(client, deep_dispatch, mode):
    """TL-QA-01: the 422 points at ``body.mode``; nothing is queued and no dispatch is logged."""
    response = await client.post(DEEP_PATH, json={"mode": mode})

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "mode"]]
    deep_dispatch.assert_not_called()
    deep_investigation.record_activity.assert_not_awaited()


async def test_deep_trigger_refuses_an_unknown_mode_even_while_a_pipeline_is_active(client, deep_dispatch, monkeypatch):
    """TL-QA-01: an unsupported mode is never answered with another workflow's active pipeline."""
    active = AsyncMock(return_value={RUN_ID})
    monkeypatch.setattr(pipeline_admission, "runs_with_in_progress_pipeline", active)

    response = await client.post(DEEP_PATH, json={"mode": "rules"})

    assert response.status_code == 422, response.text
    active.assert_not_awaited()
    deep_dispatch.assert_not_called()


def test_the_published_contract_lists_the_two_deep_modes():
    """TL-QA-01: OpenAPI states the accepted values, so clients can see them."""
    from app.main import app

    mode = app.openapi()["components"]["schemas"]["TriggerDeepRequest"]["properties"]["mode"]

    assert mode["enum"] == ["deep", "offline"] and mode["default"] == "deep"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", None),
        ("?status=FAILED", ["FAILED"]),
        ("?status=broken", ["broken"]),
        ("?status=FAILED&status=BROKEN", ["FAILED", "BROKEN"]),
    ],
    ids=["no-filter", "one", "one-lower-case", "union"],
)
async def test_run_tests_route_accepts_one_status_or_several(client, auth_as, fake_db, monkeypatch, query, expected):
    """TL-QA-06: ``status`` repeats; a single value still works as before."""
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=RunRecord(id=RUN_ID, project_id=PROJECT_ID))])
    listing = AsyncMock(return_value=([], 431, 3))
    monkeypatch.setattr(runs, "list_run_test_cases", listing)

    response = await client.get(f"{RUN_PATH}/tests{query}{'&' if query else '?'}page=1&size=200")

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 431, "page": 1, "size": 200, "pages": 3}
    assert listing.await_args.args[2:] == (1, 200, expected, None)


async def test_run_tests_route_refuses_more_statuses_than_exist(client, auth_as):
    auth_as(role=UserRole.ADMIN)
    query = "&".join(f"status=S{index}" for index in range(6))

    response = await client.get(f"{RUN_PATH}/tests?{query}")

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["query", "status"]


def _sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize(
    ("status", "clause"),
    [
        (None, None),
        ("failed", "test_cases.status = 'FAILED'"),
        (["FAILED"], "test_cases.status = 'FAILED'"),
        (["FAILED", "broken", "FAILED", " "], "test_cases.status IN ('FAILED', 'BROKEN')"),
    ],
    ids=["no-filter", "one-string", "one-in-a-list", "union-deduplicated"],
)
async def test_stored_test_cases_are_filtered_by_the_union_before_ordering_and_paging(monkeypatch, status, clause):
    """TL-QA-06: the union is in the WHERE clause, so the page and ``total`` cover every match."""
    from app.services import runs_service

    paginate = AsyncMock(return_value=(["row"], 2, 1))
    monkeypatch.setattr(runs_service, "paginate_query", paginate)

    assert await runs_service.list_run_test_cases(AsyncMock(), RUN_ID, 1, 200, status) == (["row"], 2, 1)

    sql = _sql(paginate.await_args.args[1])
    where, _, order = sql.partition("ORDER BY")
    assert order.strip() == "test_cases.status, test_cases.test_name"
    if clause is None:
        assert "test_cases.status" not in where.split("WHERE")[1]
    else:
        assert clause in where


async def test_buffered_live_test_cases_are_filtered_by_the_same_union(monkeypatch):
    """TL-QA-06: an in-progress run's buffered rows answer the union as stored rows do."""
    import json

    from app.db import redis_client
    from app.services import runs_service

    events = [
        {"event_type": "test_result", "test_name": name, "class_name": "Checkout", "status": status}
        for name, status in [("test_pay", "failed"), ("test_setup", "BROKEN"), ("test_ok", "PASSED")]
    ]
    redis = SimpleNamespace(
        xrange=AsyncMock(return_value=[]),
        lrange=AsyncMock(return_value=[json.dumps(event) for event in events]),
    )
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)

    async def names(status):
        rows, total, _pages = await runs_service._live_buffer_test_cases(RUN_ID, 1, 200, status)
        return [row["test_name"] for row in rows], total

    assert await names(["FAILED", "BROKEN"]) == (["test_setup", "test_pay"], 2)
    assert await names("BROKEN") == (["test_setup"], 1)
    assert await names(None) == (["test_setup", "test_pay", "test_ok"], 3)


@pytest.mark.parametrize("filtered", [False, True], ids=["project-list", "one-run"])
async def test_investigation_list_narrows_to_a_run_inside_the_project(client, auth_as, fake_db, monkeypatch, filtered):
    """TL-QA-08: ``run_id`` narrows the count and the page; the path's project always scopes both."""
    auth_as(role=UserRole.ADMIN)
    statements = []
    execute = fake_db.execute

    async def recording(statement, params=None):
        statements.append(_sql(statement))
        return await execute(statement, params)

    monkeypatch.setattr(fake_db, "execute", recording)
    fake_db.set_execute_results([fake_execute_result(scalar=3), fake_execute_result(all_=[])])

    response = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/investigations",
        params={"run_id": str(RUN_ID), "limit": 1} if filtered else {"limit": 1},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 3}
    count, page = statements[-2:]
    for sql in (count, page):
        assert f"agent_investigations.project_id = '{PROJECT_ID.hex}'" in sql or str(PROJECT_ID) in sql
        assert ("agent_investigations.run_id =" in sql.split("WHERE")[1]) is filtered
    assert "ORDER BY agent_investigations.created_at DESC, agent_investigations.id DESC" in page


async def test_investigation_list_refuses_a_run_filter_that_is_not_a_uuid(client, auth_as):
    auth_as(role=UserRole.ADMIN)

    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/investigations", params={"run_id": "latest"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["query", "run_id"]
