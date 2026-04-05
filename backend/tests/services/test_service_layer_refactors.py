from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sys

import pytest

from app.services import (
    analytics_service,
    chat_service,
    feedback_service,
    notification_service,
    report_service,
    release_service,
    runs_service,
    search_service,
    stream_service,
    test_management_ai_service,
    test_management_query_service,
    test_management_service,
)


class FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items

    def first(self):
        return self._items[0] if self._items else None


class FakeExecuteResult:
    def __init__(self, *, scalars=None, rows=None, scalar=None):
        self._scalars = scalars or []
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return FakeScalarResult(self._scalars)

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def one(self):
        return self._rows[0]


class FakeAsyncDB:
    def __init__(self, execute_results):
        self._execute_results = list(execute_results)
        self.added = []
        self.commit = AsyncMock()
        self.delete = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, _stmt, _params=None):
        return self._execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)


def _fake_run(run_id: uuid.UUID, status: str = "passed"):
    columns = [SimpleNamespace(name="id"), SimpleNamespace(name="status"), SimpleNamespace(name="created_at")]
    return SimpleNamespace(
        __table__=SimpleNamespace(columns=columns),
        id=run_id,
        project_id=uuid.uuid4(),
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _fake_release(release_id: uuid.UUID, phases=None, project_id: uuid.UUID | None = None):
    columns = [
        SimpleNamespace(name="id"),
        SimpleNamespace(name="project_id"),
        SimpleNamespace(name="name"),
        SimpleNamespace(name="status"),
        SimpleNamespace(name="created_at"),
    ]
    return SimpleNamespace(
        __table__=SimpleNamespace(columns=columns),
        id=release_id,
        project_id=project_id or uuid.uuid4(),
        name="Release 1",
        status="planning",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        phases=phases or [],
    )


def _fake_live_session(session_id: uuid.UUID, project_id: uuid.UUID, run_id: str, **overrides):
    base = {
        "id": session_id,
        "project_id": project_id,
        "run_id": run_id,
        "client_name": "CI Agent",
        "machine_id": "machine-1",
        "build_number": "build-42",
        "framework": "pytest",
        "branch": "main",
        "commit_hash": "abc123",
        "status": "active",
        "release_name": None,
        "total_tests": 12,
        "events_received": 4,
        "extra_metadata": {},
        "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "completed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_deprecate_managed_test_case_sets_status_and_audits():
    case_id = uuid.uuid4()
    project_id = uuid.uuid4()
    test_case = SimpleNamespace(id=case_id, project_id=project_id, status="draft")
    user = SimpleNamespace(id=uuid.uuid4(), full_name="QA User", username="qa", role="QA_ENGINEER")
    db = FakeAsyncDB([])

    with (
        patch.object(test_management_service, "get_test_case_or_404", AsyncMock(return_value=test_case)),
        patch.object(test_management_service, "audit_event", AsyncMock()) as audit_mock,
    ):
        await test_management_service.deprecate_managed_test_case(db, case_id, user)

    assert test_case.status == "deprecated"
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_review_action_updates_review_and_test_case():
    case_id = uuid.uuid4()
    test_case = SimpleNamespace(id=case_id, project_id=uuid.uuid4(), status="review_requested")
    review = SimpleNamespace(status="pending", reviewer_id=None, human_notes=None, reviewed_at=None)
    user = SimpleNamespace(id=uuid.uuid4(), full_name="Lead", username="lead", role=test_management_service.UserRole.QA_LEAD)
    payload = SimpleNamespace(action="approve", notes="Looks good")
    db = FakeAsyncDB([FakeExecuteResult(scalars=[review])])

    with (
        patch.object(test_management_service, "get_test_case_or_404", AsyncMock(return_value=test_case)),
        patch.object(test_management_service, "audit_event", AsyncMock()) as audit_mock,
    ):
        result = await test_management_service.apply_review_action(db, case_id, payload, user)

    assert result is test_case
    assert test_case.status == "approved"
    assert review.status == "approved"
    assert review.reviewer_id == user.id
    assert review.human_notes == "Looks good"
    assert review.reviewed_at is not None
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(test_case)


@pytest.mark.asyncio
async def test_recompute_plan_counts_updates_all_aggregates():
    items = [
        SimpleNamespace(execution_status="not_run"),
        SimpleNamespace(execution_status="passed"),
        SimpleNamespace(execution_status="failed"),
        SimpleNamespace(execution_status="blocked"),
    ]
    plan = SimpleNamespace(id=uuid.uuid4(), total_cases=0, executed_cases=0, passed_cases=0, failed_cases=0, blocked_cases=0)
    db = FakeAsyncDB([FakeExecuteResult(scalars=items)])

    await test_management_service.recompute_plan_counts(db, plan)

    assert plan.total_cases == 4
    assert plan.executed_cases == 3
    assert plan.passed_cases == 1
    assert plan.failed_cases == 1
    assert plan.blocked_cases == 1


@pytest.mark.asyncio
async def test_list_project_runs_enriches_paginated_runs():
    run_id = uuid.uuid4()
    run = _fake_run(run_id, status="failed")
    db = FakeAsyncDB([])

    with (
        patch.object(runs_service, "paginate_query", AsyncMock(return_value=([run], 1, 1))),
        patch.object(
            runs_service,
            "fetch_release_map",
            AsyncMock(return_value={str(run_id): {"id": "rel-1", "name": "Release 1"}}),
        ),
        patch.object(
            runs_service,
            "fetch_project_name_map",
            AsyncMock(return_value={}),
        ),
    ):
        items, total, pages = await runs_service.list_project_runs(db, "project-1", 1, 20, "FAILED", None)

    assert total == 1
    assert pages == 1
    assert items[0]["release_id"] == "rel-1"
    assert items[0]["release_name"] == "Release 1"
    assert items[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_search_test_cases_query_returns_rows_and_pagination():
    rows = [
        SimpleNamespace(
            _mapping={
                "test_case_id": uuid.uuid4(),
                "test_run_id": uuid.uuid4(),
                "test_name": "test_login",
                "suite_name": "auth",
                "status": "FAILED",
                "last_run_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "failure_count": 3,
            }
        )
    ]
    db = FakeAsyncDB(
        [
            FakeExecuteResult(rows=rows),
            FakeExecuteResult(scalar=1),
        ]
    )

    items, total, pages = await search_service.search_test_cases_query(
        db,
        q="login",
        page=1,
        size=20,
        project_id="project-1",
        status="failed",
        days=30,
    )

    assert total == 1
    assert pages == 1
    assert items[0]["test_name"] == "test_login"
    assert items[0]["failure_count"] == 3


@pytest.mark.asyncio
async def test_generate_ai_cases_persists_created_cases_when_requested():
    payload = SimpleNamespace(project_id=uuid.uuid4(), requirements="Login flow", persist=True)
    user = SimpleNamespace(id=uuid.uuid4(), full_name="QA User", username="qa")
    db = FakeAsyncDB([])

    async def fake_flush():
        for obj in db.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=fake_flush)

    with (
        patch.dict(
            sys.modules,
            {
                "app.services.test_case_ai_agent": SimpleNamespace(
                    ai_generate_test_cases=AsyncMock(
                        return_value={"test_cases": [{"title": "AI Login Case", "objective": "Validate login"}]}
                    )
                )
            },
        ),
        patch.object(test_management_ai_service, "audit_event", AsyncMock()) as audit_mock,
    ):
        result = await test_management_ai_service.generate_ai_cases(db, payload, user)

    assert len(result["created_ids"]) == 1
    assert result["test_cases"][0]["title"] == "AI Login Case"
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_test_case_with_ai_creates_review_when_missing():
    case_id = uuid.uuid4()
    test_case = SimpleNamespace(
        id=case_id,
        project_id=uuid.uuid4(),
        title="Login",
        objective="Check login",
        preconditions=None,
        steps=[],
        expected_result="Success",
        test_data=None,
        test_type="functional",
        ai_quality_score=None,
        ai_review_notes=None,
    )
    user = SimpleNamespace(id=uuid.uuid4(), full_name="QA User", username="qa")
    db = FakeAsyncDB([FakeExecuteResult(scalars=[])])

    async def fake_flush():
        for obj in db.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=fake_flush)

    with (
        patch.object(test_management_ai_service, "get_test_case_or_404", AsyncMock(return_value=test_case)),
        patch.dict(
            sys.modules,
            {
                "app.services.test_case_ai_agent": SimpleNamespace(
                    ai_review_test_case=AsyncMock(return_value={"quality_score": 91, "summary": "Looks strong"})
                )
            },
        ),
        patch.object(test_management_ai_service, "audit_event", AsyncMock()) as audit_mock,
    ):
        result = await test_management_ai_service.review_test_case_with_ai(db, case_id, user)

    assert result["quality_score"] == 91
    assert test_case.ai_quality_score == 91
    assert any(type(obj).__name__ == "TestCaseReview" for obj in db.added)
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_ai_strategy_creates_strategy_and_audits():
    payload = SimpleNamespace(project_id=uuid.uuid4(), project_context="Web app checkout", strategy_name="Checkout")
    user = SimpleNamespace(id=uuid.uuid4(), full_name="Lead", username="lead")
    db = FakeAsyncDB([])

    async def fake_flush():
        for obj in db.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=fake_flush)

    with (
        patch.dict(
            sys.modules,
            {
                "app.services.test_case_ai_agent": SimpleNamespace(
                    ai_generate_strategy=AsyncMock(return_value={"objective": "Reduce checkout risk", "scope": "Payments"})
                )
            },
        ),
        patch.object(test_management_ai_service, "audit_event", AsyncMock()) as audit_mock,
    ):
        strategy = await test_management_ai_service.generate_ai_strategy(db, payload, user)

    assert strategy.name == "Checkout"
    assert strategy.objective == "Reduce checkout risk"
    assert strategy.scope == "Payments"
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()


def test_get_ai_task_status_maps_success_failure_and_pending():
    async_result = Mock()
    with patch.dict(
        sys.modules,
        {
            "celery.result": SimpleNamespace(AsyncResult=async_result),
            "app.worker.celery_app": SimpleNamespace(celery_app=object()),
        },
    ):
        async_result.return_value = SimpleNamespace(state="SUCCESS", result={"ok": True}, info=None)
        success = test_management_ai_service.get_ai_task_status("task-1")

        async_result.return_value = SimpleNamespace(state="FAILURE", result=None, info="boom")
        failure = test_management_ai_service.get_ai_task_status("task-2")

        async_result.return_value = SimpleNamespace(state="STARTED", result=None, info=None)
        pending = test_management_ai_service.get_ai_task_status("task-3")

    assert success["status"] == "success"
    assert failure["status"] == "failure"
    assert pending["status"] == "pending"


@pytest.mark.asyncio
async def test_list_audit_logs_returns_paginated_filtered_entries():
    entries = [
        SimpleNamespace(entity_type="test_case", action="updated"),
        SimpleNamespace(entity_type="test_case", action="created"),
    ]
    db = FakeAsyncDB([])

    with patch.object(
        test_management_query_service,
        "paginate_scalars",
        AsyncMock(return_value=(entries, 2, 1)),
    ) as paginate_mock:
        items, total, pages = await test_management_query_service.list_audit_logs(
            db,
            project_id=uuid.uuid4(),
            page=1,
            size=50,
            entity_type="test_case",
            action="updated",
        )

    assert items == entries
    assert total == 2
    assert pages == 1
    paginate_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_notification_preference_updates_existing_preference():
    current_user = SimpleNamespace(id=uuid.uuid4())
    pref = SimpleNamespace(
        enabled=False,
        events=[],
        failure_rate_threshold=None,
        email_override=None,
        slack_webhook_url=None,
        teams_webhook_url=None,
    )
    payload = SimpleNamespace(
        project_id=None,
        channel="email",
        model_dump=lambda: {
            "enabled": True,
            "events": ["run_failed"],
            "failure_rate_threshold": 75.0,
            "email_override": "qa@example.com",
            "slack_webhook_url": None,
            "teams_webhook_url": None,
        },
    )
    db = FakeAsyncDB([FakeExecuteResult(scalar=pref)])

    result = await notification_service.upsert_preference(db, payload, current_user)

    assert result is pref
    assert pref.enabled is True
    assert pref.events == ["run_failed"]
    assert pref.failure_rate_threshold == 75.0
    assert pref.email_override == "qa@example.com"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(pref)


@pytest.mark.asyncio
async def test_resolve_notification_overrides_returns_none_when_preference_missing():
    current_user = SimpleNamespace(id=uuid.uuid4())
    db = FakeAsyncDB([FakeExecuteResult(scalar=None)])

    overrides = await notification_service.resolve_notification_overrides(
        db,
        current_user,
        uuid.uuid4(),
    )

    assert overrides == (None, None, None)


@pytest.mark.asyncio
async def test_release_service_list_releases_enriches_run_counts():
    release_id = uuid.uuid4()
    phase = SimpleNamespace(
        __table__=SimpleNamespace(columns=[SimpleNamespace(name="id"), SimpleNamespace(name="name")]),
        id=uuid.uuid4(),
        name="QA",
    )
    release = _fake_release(release_id, phases=[phase])
    db = FakeAsyncDB(
        [
            FakeExecuteResult(scalars=[release]),
            FakeExecuteResult(rows=[(str(release_id), 3)]),
        ]
    )

    result = await release_service.list_releases(db, str(uuid.uuid4()))

    assert result["total"] == 1
    assert result["items"][0]["id"] == str(release_id)
    assert result["items"][0]["test_run_count"] == 3
    assert result["items"][0]["phases"][0]["name"] == "QA"


@pytest.mark.asyncio
async def test_release_service_link_test_run_returns_existing_link_message():
    release = SimpleNamespace(id=uuid.uuid4())
    run = SimpleNamespace(id=uuid.uuid4())
    existing = SimpleNamespace(id=uuid.uuid4())
    body = SimpleNamespace(test_run_id=str(run.id), phase_id=None)
    db = FakeAsyncDB(
        [
            FakeExecuteResult(scalar=run),
            FakeExecuteResult(scalar=existing),
        ]
    )

    with patch.object(release_service, "get_release_or_404", AsyncMock(return_value=release)):
        result = await release_service.link_test_run(db, str(release.id), body)

    assert result["message"] == "Already linked"
    assert result["id"] == str(existing.id)


def test_report_service_build_html_report_summarizes_totals():
    html = report_service.build_html_report(
        project_name="Checkout",
        days=7,
        chart_ids=["daily_breakdown", "failure_rate"],
        trend_data=[
            {
                "date": "2026-01-01",
                "passed": 8,
                "failed": 2,
                "skipped": 1,
                "broken": 0,
                "total": 11,
                "pass_rate": 72.7,
            },
            {
                "date": "2026-01-02",
                "passed": 10,
                "failed": 0,
                "skipped": 0,
                "broken": 1,
                "total": 11,
                "pass_rate": 90.9,
            },
        ],
    )

    assert "Checkout" in html
    assert "18" in html
    assert "2" in html
    assert "1" in html
    assert "81.8%" in html
    assert "Failure Rate" in html


@pytest.mark.asyncio
async def test_report_service_email_trends_report_sends_built_email():
    db = FakeAsyncDB(
        [
            FakeExecuteResult(rows=[("Project Phoenix",)]),
            FakeExecuteResult(
                rows=[
                    SimpleNamespace(
                        _mapping={
                            "date": "2026-01-01",
                            "passed": 5,
                            "failed": 1,
                            "skipped": 0,
                            "broken": 0,
                            "total": 6,
                            "pass_rate": 83.3,
                        }
                    )
                ]
            ),
        ]
    )
    payload = SimpleNamespace(
        project_id="project-1",
        days=14,
        recipient_email="qa@example.com",
        chart_ids=["status_pie"],
    )

    with patch.object(report_service, "send_email") as send_email_mock:
        result = await report_service.email_trends_report(db, payload)

    assert result == {"status": "sent", "recipient": "qa@example.com"}
    send_email_mock.assert_called_once()
    args = send_email_mock.call_args[0]
    assert args[0] == "qa@example.com"
    assert args[1] == "QA Trends Report - Project Phoenix (14d)"
    assert "Project Phoenix" in args[2]


@pytest.mark.asyncio
async def test_analytics_service_flaky_tests_returns_items_and_total():
    rows = [
        SimpleNamespace(
            _mapping={
                "test_fingerprint": "fp-1",
                "test_name": "test_checkout",
                "suite_name": "payments",
                "class_name": "CheckoutTests",
                "total_runs": 5,
                "fail_count": 2,
                "pass_count": 3,
                "failure_rate_pct": 40.0,
                "last_seen": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        )
    ]
    db = FakeAsyncDB([FakeExecuteResult(rows=rows)])

    result = await analytics_service.flaky_tests(db, "project-1", 30, 20)

    assert result["total"] == 1
    assert result["period_days"] == 30
    assert result["items"][0]["test_name"] == "test_checkout"


@pytest.mark.asyncio
async def test_analytics_service_list_defects_returns_pagination_metadata():
    rows = [
        SimpleNamespace(
            _mapping={
                "id": "def-1",
                "jira_ticket_id": "QA-123",
                "jira_ticket_url": "https://jira.example/QA-123",
                "jira_status": "OPEN",
                "failure_category": "REGRESSION",
                "resolution_status": "OPEN",
                "ai_confidence_score": 92.0,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "resolved_at": None,
                "test_name": "test_login",
                "suite_name": "auth",
            }
        )
    ]
    db = FakeAsyncDB([FakeExecuteResult(rows=rows), FakeExecuteResult(scalar=3)])

    result = await analytics_service.list_defects(db, "project-1", "open", 2, 2)

    assert result["total"] == 3
    assert result["page"] == 2
    assert result["pages"] == 2
    assert result["items"][0]["jira_ticket_id"] == "QA-123"


@pytest.mark.asyncio
async def test_stream_service_create_session_stores_token_and_initializes_live_state():
    project_id = uuid.uuid4()
    payload = SimpleNamespace(
        project_id=project_id,
        run_id=None,
        client_name="CI Agent",
        machine_id="runner-1",
        build_number="build-99",
        framework="pytest",
        branch="main",
        commit_hash="abc123",
        total_tests=25,
        release_name="Release 2",
        metadata={"env": "staging"},
    )
    db = FakeAsyncDB([])
    db.get = AsyncMock(return_value=object())
    redis = SimpleNamespace(setex=AsyncMock())
    live_state_module = SimpleNamespace(RedisLiveRunState=SimpleNamespace(start=AsyncMock()))

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.dict(sys.modules, {"app.streams.live_run_state": live_state_module}),
        patch("app.services.stream_service.secrets.token_urlsafe", return_value="token-123"),
        patch("app.services.stream_service.uuid.uuid4", side_effect=[uuid.UUID("11111111-1111-1111-1111-111111111111")]),
    ):
        result = await stream_service.create_session(db, payload)

    assert result.session_id == "11111111-1111-1111-1111-111111111111"
    assert result.session_token == "token-123"
    redis.setex.assert_awaited_once()
    live_state_module.RedisLiveRunState.start.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_service_ingest_event_batch_validates_and_refreshes_token():
    redis = SimpleNamespace(get=AsyncMock(return_value="sess-1"), expire=AsyncMock())
    batch = SimpleNamespace(session_id="sess-1", run_id="run-1", events=[{"event_type": "test_result"}])

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "publish_event_batch", AsyncMock(return_value=1)) as publish_mock,
    ):
        result = await stream_service.ingest_event_batch(batch, "token-123")

    assert result.accepted == 1
    publish_mock.assert_awaited_once()
    redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_service_list_active_sessions_combines_sources():
    project_id = uuid.uuid4()
    completed_session = _fake_live_session(
        uuid.uuid4(),
        project_id,
        "completed-run",
        status="completed",
        release_name="Release 1",
        extra_metadata={"final_state": {"total": 10, "passed": 8, "failed": 2, "skipped": 0, "broken": 0, "pass_rate": 80.0}},
        completed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    fallback_run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        build_number="build-77",
        total_tests=5,
        passed_tests=5,
        failed_tests=0,
        skipped_tests=0,
        broken_tests=0,
        pass_rate=100.0,
        start_time=datetime(2026, 1, 3, tzinfo=timezone.utc),
        end_time=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    db = FakeAsyncDB([FakeExecuteResult(scalars=[completed_session]), FakeExecuteResult(scalars=[fallback_run])])
    active = [
        {
            "run_id": "active-run",
            "project_id": str(project_id),
            "build_number": "build-1",
            "status": "running",
            "total": 3,
            "passed": 1,
            "failed": 1,
            "skipped": 1,
            "broken": 0,
            "pass_rate": 33.3,
        }
    ]

    live_state_module = SimpleNamespace(RedisLiveRunState=SimpleNamespace(get_all_active=AsyncMock(return_value=active)))
    with patch.dict(sys.modules, {"app.streams.live_run_state": live_state_module}):
        result = await stream_service.list_active_sessions(db, str(project_id))

    assert result.count == 3
    assert [session.run_id for session in result.sessions] == ["active-run", "completed-run", str(fallback_run.id)]


@pytest.mark.asyncio
async def test_stream_service_close_session_marks_complete_and_queues_followup_work():
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session = _fake_live_session(session_id, project_id, str(uuid.uuid4()), release_name="Release 5")
    db = FakeAsyncDB([])
    db.get = AsyncMock(return_value=session)
    persist_task = SimpleNamespace(apply_async=Mock())
    pipeline_task = SimpleNamespace(apply_async=Mock())
    release_linker = SimpleNamespace(auto_link_release=AsyncMock())
    live_state_module = SimpleNamespace(RedisLiveRunState=SimpleNamespace(complete=AsyncMock(return_value={"passed": 4, "failed": 1, "total": 5})))

    with (
        patch.object(stream_service, "upsert_test_run", AsyncMock()) as upsert_mock,
        patch.dict(
            sys.modules,
            {
                "app.streams.live_run_state": live_state_module,
                "app.services.release_linker": release_linker,
                "app.worker.tasks": SimpleNamespace(
                    persist_live_session=persist_task,
                    run_agent_pipeline=pipeline_task,
                ),
            },
        ),
    ):
        await stream_service.close_session(db, str(session_id))

    assert session.status == "completed"
    assert session.completed_at is not None
    assert session.extra_metadata["final_state"]["total"] == 5
    upsert_mock.assert_awaited_once()
    release_linker.auto_link_release.assert_awaited_once()
    db.commit.assert_awaited_once()
    persist_task.apply_async.assert_called_once()
    pipeline_task.apply_async.assert_called_once()


@pytest.mark.asyncio
async def test_feedback_service_submit_feedback_backpropagates_incorrect_correction():
    analysis = SimpleNamespace(
        id=uuid.uuid4(),
        test_case_id=uuid.uuid4(),
        failure_category="UNKNOWN",
        root_cause_summary="Old",
        requires_human_review=True,
    )
    body = SimpleNamespace(
        rating=feedback_service.FeedbackRating.INCORRECT,
        corrected_category="PRODUCT_BUG",
        corrected_root_cause="New cause",
        comment="Wrong label",
    )
    current_user = SimpleNamespace(id=uuid.uuid4())
    db = FakeAsyncDB([FakeExecuteResult(scalar=analysis)])

    async def fake_commit():
        for obj in db.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.commit = AsyncMock(side_effect=fake_commit)

    result = await feedback_service.submit_feedback(db, analysis.id, body, current_user)

    assert result["feedback_id"]
    assert analysis.failure_category == "PRODUCT_BUG"
    assert analysis.root_cause_summary == "New cause"
    assert analysis.requires_human_review is False


@pytest.mark.asyncio
async def test_feedback_service_jira_resolution_webhook_creates_feedback_for_invalid_resolution():
    defect = SimpleNamespace(
        id=uuid.uuid4(),
        jira_ticket_id="QA-123",
        test_case_id=uuid.uuid4(),
        jira_status="open",
        resolution_status="OPEN",
        resolved_at=None,
    )
    analysis = SimpleNamespace(id=uuid.uuid4(), test_case_id=defect.test_case_id)
    db = FakeAsyncDB([FakeExecuteResult(scalar=defect), FakeExecuteResult(scalar=analysis)])
    payload = {
        "issue": {
            "key": "QA-123",
            "fields": {
                "status": {"name": "Closed"},
                "resolution": {"name": "Duplicate"},
            },
        }
    }

    result = await feedback_service.jira_resolution_webhook(db, payload)

    assert result["defect_id"] == str(defect.id)
    assert defect.resolution_status == "INVALID"
    assert any(getattr(obj, "source", None) == "jira_invalid" for obj in db.added)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_service_get_run_summaries_merges_ai_summaries_and_stubs():
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="build-22",
        total_tests=10,
        failed_tests=2,
        pass_rate=80.0,
        start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db = FakeAsyncDB([FakeExecuteResult(scalars=[run])])
    mongo_rows = [
        {
            "test_run_id": "ai-run-1",
            "project_id": str(uuid.uuid4()),
            "build_number": "build-ai",
            "executive_summary": "AI summary",
            "markdown_report": None,
            "anomaly_count": 1,
            "is_regression": False,
            "analysis_count": 2,
            "generated_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        }
    ]
    cursor = SimpleNamespace(
        sort=lambda *args, **kwargs: cursor,
        limit=lambda *args, **kwargs: cursor,
        to_list=AsyncMock(return_value=mongo_rows),
    )
    mongo_db = {"run_summaries": SimpleNamespace(find=lambda *args, **kwargs: cursor)}
    collections = SimpleNamespace(RUN_SUMMARIES="run_summaries")

    with patch.dict(
        sys.modules,
        {"app.db.mongo": SimpleNamespace(Collections=collections, get_mongo_db=lambda: mongo_db)},
    ):
        result = await chat_service.get_run_summaries(db, str(run.project_id), 5)

    assert result[0]["test_run_id"] == "ai-run-1"
    assert result[0]["is_stub"] is False
    assert any(item["test_run_id"] == str(run.id) and item["is_stub"] for item in result)


@pytest.mark.asyncio
async def test_chat_service_send_message_updates_default_title_and_dispatches_agent():
    session_id = uuid.uuid4()
    session = SimpleNamespace(id=session_id, user_id=uuid.uuid4(), project_id=uuid.uuid4(), title="New conversation")
    current_user = SimpleNamespace(id=session.user_id)
    payload = SimpleNamespace(message="Investigate latest failures", project_id=None)
    db = FakeAsyncDB([FakeExecuteResult(scalar=session)])
    conversation_agent_cls = Mock()
    conversation_agent_cls.return_value.chat = AsyncMock(return_value={"reply": "Here is the summary", "sources": [{"type": "run"}]})

    with patch.dict(sys.modules, {"app.agents.conversation": SimpleNamespace(ConversationAgent=conversation_agent_cls)}):
        result = await chat_service.send_message(db, session_id, payload, current_user)

    assert session.title == "Investigate latest failures"
    db.commit.assert_awaited_once()
    conversation_agent_cls.return_value.chat.assert_awaited_once()
    assert result["reply"] == "Here is the summary"
