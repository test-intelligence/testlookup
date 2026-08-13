from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
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
        # Default identity-load for project-existence guards (e.g.
        # ``create_manual_defect`` does ``db.get(Project, project_id)``
        # before staging). Returns a truthy stub so the guard passes;
        # tests that need a 404 path override ``db.get`` after construction.
        self.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))

    async def execute(self, _stmt, _params=None):
        return self._execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        # No-op SAVEPOINT context manager that does not suppress exceptions,
        # so an IntegrityError raised in the body still propagates (mirrors a
        # real SAVEPOINT). Services wrap racy inserts in db.begin_nested().
        class _SP:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *exc):
                return False

        return _SP()


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
        # launch_name / suite_name became real columns on LiveSession and are
        # read directly by build_completed_session_state. Stub them so test
        # SimpleNamespaces match the real ORM row shape.
        "launch_name": None,
        "suite_name": None,
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
    # Item #2: the service only mutates + audits. The router handler owns
    # the commit, so this unit test should not see commit called.
    db.commit.assert_not_awaited()


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
    # Item #2: the service only mutates; router handler owns commit+refresh.
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_recompute_plan_counts_updates_all_aggregates():
    # recompute_plan_counts now issues ONE aggregate query returning a single
    # row (total / not_run / passed / failed / blocked); executed is derived as
    # total - not_run. Same scenario as before: 4 items, one of each status.
    agg = SimpleNamespace(total=4, not_run=1, passed=1, failed=1, blocked=1)
    plan = SimpleNamespace(id=uuid.uuid4(), total_cases=0, executed_cases=0, passed_cases=0, failed_cases=0, blocked_cases=0)
    db = FakeAsyncDB([FakeExecuteResult(rows=[agg])])

    await test_management_service.recompute_plan_counts(db, plan)

    assert plan.total_cases == 4
    assert plan.executed_cases == 3
    assert plan.passed_cases == 1
    assert plan.failed_cases == 1
    assert plan.blocked_cases == 1


@pytest.mark.asyncio
async def test_list_project_runs_enriches_paginated_runs():
    """After the load-test pagination optimization, ``list_project_runs``
    issues two direct queries (count + items with LEFT JOIN on Project)
    instead of calling the generic ``paginate_query`` + a separate
    ``fetch_project_name_map`` round-trip. The release map is still
    fetched separately because release links aren't UNIQUE.
    """
    run_id = uuid.uuid4()
    run = _fake_run(run_id, status="failed")

    # Items query returns Row objects with the TestRun at index 0 and
    # ``project_name`` exposed as a labeled column attribute. SQLAlchemy
    # rows support both indexing (``row[0]``) and attribute access
    # (``row.project_name``); the fake mirrors that contract.
    class _FakeRow:
        def __init__(self, run_obj, project_name):
            self._run = run_obj
            self.project_name = project_name

        def __getitem__(self, idx):
            return (self._run, self.project_name)[idx]

    db = FakeAsyncDB([
        FakeExecuteResult(scalar=1),                                  # count
        FakeExecuteResult(rows=[_FakeRow(run, "Checkout")]),          # items
    ])

    with patch.object(
        runs_service,
        "fetch_release_map",
        AsyncMock(return_value={str(run_id): {"id": "rel-1", "name": "Release 1"}}),
    ), patch.object(
        # ``fetch_run_seq_map`` issues its own queries; stub it so the
        # FakeAsyncDB doesn't have to model the window-function SQL. The
        # helper's behaviour is covered by dedicated tests in
        # tests/services/test_run_seq_map.py.
        runs_service,
        "fetch_run_seq_map",
        AsyncMock(return_value={str(run_id): 1}),
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
    # Item #2: service stages only, router handler commits.
    db.commit.assert_not_awaited()


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
    # role=ADMIN so the new tenant guard (resolve_project_scope) bypasses —
    # this test exercises the review-creation flow, not access control.
    user = SimpleNamespace(id=uuid.uuid4(), full_name="QA User", username="qa", role="ADMIN")
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
    # Item #2: service stages only, router handler commits.
    db.commit.assert_not_awaited()


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
    # Item #2: service stages only, router handler commits + refreshes.
    db.commit.assert_not_awaited()


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
    # After the item-#2 refactor, the service only mutates the attached
    # row. The router handler owns commit+refresh, so this unit test
    # should not see either called.
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


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
        # After the item-#2 refactor link_test_run returns (link, is_new) so
        # the router can decide whether to commit. For an existing link,
        # is_new is False and the returned ``link`` is the existing row.
        link, is_new = await release_service.link_test_run(db, str(release.id), body)

    assert is_new is False
    assert link is existing


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
    # flaky_tests now issues a SECOND query for manually-triaged FLAKY_TEST
    # tests (merged so /failures agrees with /flaky-coach). No manual triage
    # here → empty second result.
    db = FakeAsyncDB([FakeExecuteResult(rows=rows), FakeExecuteResult(rows=[])])

    result = await analytics_service.flaky_tests(db, "project-1", 30, 20)

    assert result["total"] == 1
    assert result["period_days"] == 30
    assert result["items"][0]["test_name"] == "test_checkout"
    assert result["items"][0]["source"] == "auto"


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


# ── create_manual_defect — manual Defect Intake ───────────────────────────────
#
# Covers the service added for the "New defect" flow on the Defects page.
# Verified behaviours:
#   * P0–P3 severity maps to the defects.severity column's CRITICAL/HIGH/MEDIUM/LOW
#     vocabulary (not the raw P-codes).
#   * promotion_source is always 'manual' and resolution_status defaults to 'OPEN'
#     regardless of what the caller passed.
#   * When a test_name is supplied AND a matching TestCase exists, the new defect
#     row's test_case_id is attached.
#   * When no test_name is supplied OR no matching TestCase is found, test_case_id
#     stays NULL — intake must not fail on missing test linkage.
#   * Jira browse URL → jira_ticket_id auto-extracted; a malformed URL gives None.


@pytest.mark.asyncio
async def test_create_manual_defect_maps_p0_to_critical_and_links_test_case():
    project_id = uuid.uuid4()
    matched_test_case_id = uuid.uuid4()
    # First execute() resolves the recent-matching-test-case lookup.
    db = FakeAsyncDB([FakeExecuteResult(scalar=matched_test_case_id)])

    defect = await analytics_service.create_manual_defect(
        db,
        project_id,
        {
            "title": "Checkout fails on second attempt",
            "description": "Repro: add to cart, pay, retry.",
            "severity": "P0",
            "failure_category": "PRODUCT_BUG",
            "component": "checkout-service",
            "test_name": "checkout.test_payment_retry",
            "suite_name": "checkout-smoke",
            "jira_ticket_url": "https://example.atlassian.net/browse/ABC-1234",
        },
    )

    assert len(db.added) == 1
    assert defect is db.added[0]
    assert defect.severity == "CRITICAL"
    assert defect.resolution_status == "OPEN"
    assert defect.promotion_source == "manual"
    assert defect.test_case_id == matched_test_case_id
    assert defect.jira_ticket_id == "ABC-1234"
    assert defect.jira_ticket_url == "https://example.atlassian.net/browse/ABC-1234"
    assert defect.component == "checkout-service"
    db.flush.assert_awaited_once()
    # Single-owner commit rule (see backend/CLAUDE.md): services with an
    # injected session must NOT commit. The router / get_db dependency
    # owns the transaction lifecycle.
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_manual_defect_falls_back_to_null_test_case_id_when_no_match():
    project_id = uuid.uuid4()
    # The lookup runs and returns no row.
    db = FakeAsyncDB([FakeExecuteResult(scalar=None)])

    defect = await analytics_service.create_manual_defect(
        db,
        project_id,
        {
            "title": "Latency spike in /search",
            "severity": "P2",
            "failure_category": "INFRASTRUCTURE",
            "test_name": "search.test_latency_p99",
            "suite_name": "search-perf",
            "jira_ticket_url": None,
        },
    )

    assert defect.severity == "MEDIUM"
    assert defect.test_case_id is None
    assert defect.jira_ticket_id is None
    assert defect.jira_ticket_url is None
    assert defect.promotion_source == "manual"


@pytest.mark.asyncio
async def test_create_manual_defect_without_test_name_skips_lookup():
    """When no test_name is supplied the service must NOT query for a TestCase
    — that would emit an unbounded scan. We assert by giving the FakeAsyncDB
    zero pre-staged results: any call to .execute() would IndexError.
    """
    project_id = uuid.uuid4()
    db = FakeAsyncDB([])  # zero stubbed executes — calling execute would error

    defect = await analytics_service.create_manual_defect(
        db,
        project_id,
        {
            "title": "Standalone defect, no test linkage",
            "severity": "P3",
            "failure_category": "UNKNOWN",
            "test_name": None,
            "suite_name": None,
        },
    )

    assert defect.severity == "LOW"
    assert defect.test_case_id is None
    assert defect.promotion_source == "manual"


@pytest.mark.asyncio
async def test_create_manual_defect_handles_malformed_jira_url():
    project_id = uuid.uuid4()
    db = FakeAsyncDB([])

    defect = await analytics_service.create_manual_defect(
        db,
        project_id,
        {
            "title": "Bad jira url",
            "severity": "P1",
            "jira_ticket_url": "https://example.com/no-key-here",
        },
    )

    assert defect.severity == "HIGH"
    assert defect.jira_ticket_url == "https://example.com/no-key-here"
    assert defect.jira_ticket_id is None


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
    # ``resolve_project`` returns a Project row whose ``.id`` is used as the
    # canonical project UUID for every downstream write — must be a real
    # UUID, not a bare ``object()``.
    db.get = AsyncMock(return_value=SimpleNamespace(id=project_id))
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
    # Item #2: service stages + flushes so Redis ops can see the row, but
    # the router handler owns the commit so an aborted transaction never
    # leaves a dangling Redis session token.
    db.commit.assert_not_awaited()
    # Two flushes: the LiveSession row, then the companion TestRun stub
    # (SAVEPOINT-wrapped). The stub path runs now that FakeAsyncDB supports
    # begin_nested(); previously it AttributeError'd into the broad except.
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_stream_service_ingest_event_batch_validates_and_refreshes_token():
    pipe_mock = AsyncMock()
    pipe_mock.rpush = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis = SimpleNamespace(get=AsyncMock(return_value="sess-1"), expire=AsyncMock(), pipeline=MagicMock(return_value=pipe_mock))
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
async def test_stream_service_ingest_via_api_key_creates_session_on_first_call():
    project_id = uuid.uuid4()
    new_session_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")

    request = SimpleNamespace(
        run_id="ci-build-42",
        events=[SimpleNamespace(model_dump=lambda: {"event_type": "test_result", "test_name": "t1", "status": "PASSED"})],
        meta=SimpleNamespace(
            build_number="42",
            branch="main",
            commit_hash="abc",
            framework="pytest",
            total_tests=10,
            machine_id="runner-1",
            release_name="Release 5",
            # ``ingest_via_api_key`` reads these directly off meta; the real
            # Pydantic model defines them as Optional with None default, so
            # the SimpleNamespace mock has to mirror that.
            launch_name=None,
            metadata={"env": "ci"},
        ),
    )

    # No existing active session for (project_id, run_id) → service creates one.
    db = FakeAsyncDB([FakeExecuteResult(scalar=None)])
    db.get = AsyncMock(return_value=object())  # Project exists.

    pipe_mock = AsyncMock()
    pipe_mock.rpush = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.hincrby = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis = SimpleNamespace(setex=AsyncMock(), pipeline=MagicMock(return_value=pipe_mock))

    live_state_module = SimpleNamespace(RedisLiveRunState=SimpleNamespace(start=AsyncMock()))
    release_linker_module = SimpleNamespace(resolve_or_create_release=AsyncMock())

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "publish_event_batch", AsyncMock(return_value=1)) as publish_mock,
        patch.dict(sys.modules, {
            "app.streams.live_run_state": live_state_module,
            "app.services.release_linker": release_linker_module,
        }),
        patch("app.services.stream_service.uuid.uuid4", return_value=new_session_uuid),
        patch("app.services.stream_service.secrets.token_urlsafe", return_value="auto-token"),
    ):
        result = await stream_service.ingest_via_api_key(
            db=db,
            project_id=project_id,
            api_key_name="ci-runner-key",
            request=request,
        )

    assert result.created_session is True
    assert result.session_id == str(new_session_uuid)
    assert result.run_id == "ci-build-42"
    assert result.accepted == 1
    publish_mock.assert_awaited_once()

    # New LiveSession was staged with the meta + API key name as client_name.
    assert len(db.added) == 1
    staged = db.added[0]
    assert staged.client_name == "ci-runner-key"
    assert staged.build_number == "42"
    assert staged.framework == "pytest"
    assert staged.release_name == "Release 5"
    assert staged.run_id == "ci-build-42"

    # Redis token registered + live run state started + release auto-created.
    redis.setex.assert_awaited_once()
    live_state_module.RedisLiveRunState.start.assert_awaited_once()
    release_linker_module.resolve_or_create_release.assert_awaited_once()
    db.commit.assert_not_awaited()  # Handler owns the commit.


@pytest.mark.asyncio
async def test_stream_service_ingest_via_api_key_reuses_existing_session():
    project_id = uuid.uuid4()
    existing_session_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    existing_session = SimpleNamespace(
        id=existing_session_id,
        project_id=project_id,
        run_id="ci-build-99",
        status="active",
    )

    request = SimpleNamespace(
        run_id="ci-build-99",
        events=[SimpleNamespace(model_dump=lambda: {"event_type": "test_result", "test_name": "t2", "status": "FAILED"})],
        meta=None,
    )

    db = FakeAsyncDB([FakeExecuteResult(scalar=existing_session)])
    db.get = AsyncMock(return_value=object())

    pipe_mock = AsyncMock()
    pipe_mock.rpush = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.hincrby = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis = SimpleNamespace(setex=AsyncMock(), pipeline=MagicMock(return_value=pipe_mock))

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "publish_event_batch", AsyncMock(return_value=1)),
    ):
        result = await stream_service.ingest_via_api_key(
            db=db,
            project_id=project_id,
            api_key_name="ci-runner-key",
            request=request,
        )

    assert result.created_session is False
    assert result.session_id == str(existing_session_id)
    assert result.run_id == "ci-build-99"
    # No new session was added; no Redis token registered.
    assert db.added == []
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_service_ingest_via_api_key_run_complete_triggers_close():
    """A batch carrying a run_complete event must finalize the session so the
    TestRun row gets created and downstream pipelines fire — otherwise the run
    stays invisible in Runs / Overview / Coverage / Failures / Trends.
    """
    project_id = uuid.uuid4()
    existing_session_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    existing_session = SimpleNamespace(
        id=existing_session_id,
        project_id=project_id,
        run_id="ci-build-final",
        status="active",
    )

    request = SimpleNamespace(
        run_id="ci-build-final",
        events=[
            SimpleNamespace(model_dump=lambda: {"event_type": "test_result", "test_name": "t", "status": "PASSED"}),
            SimpleNamespace(model_dump=lambda: {"event_type": "run_complete"}),
        ],
        meta=None,
    )

    db = FakeAsyncDB([FakeExecuteResult(scalar=existing_session)])
    db.get = AsyncMock(return_value=object())

    pipe_mock = AsyncMock()
    pipe_mock.rpush = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.hincrby = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis = SimpleNamespace(setex=AsyncMock(), pipeline=MagicMock(return_value=pipe_mock))

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "publish_event_batch", AsyncMock(return_value=2)),
        patch.object(stream_service, "close_session", AsyncMock()) as close_mock,
    ):
        result = await stream_service.ingest_via_api_key(
            db=db,
            project_id=project_id,
            api_key_name="ci-runner-key",
            request=request,
        )

    assert result.created_session is False
    # close_session now receives the API key's bound project for defense-in-depth
    # scope re-assertion (parity with the JWT path).
    close_mock.assert_awaited_once_with(
        db, str(existing_session_id), bound_project_id=project_id
    )


@pytest.mark.asyncio
async def test_stream_service_ingest_via_api_key_no_close_without_run_complete():
    """Sanity check: a regular batch (no run_complete) must NOT close the session."""
    project_id = uuid.uuid4()
    existing_session = SimpleNamespace(
        id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        project_id=project_id,
        run_id="ci-build-active",
        status="active",
    )
    request = SimpleNamespace(
        run_id="ci-build-active",
        events=[SimpleNamespace(model_dump=lambda: {"event_type": "test_result", "test_name": "t", "status": "PASSED"})],
        meta=None,
    )
    db = FakeAsyncDB([FakeExecuteResult(scalar=existing_session)])
    db.get = AsyncMock(return_value=object())

    pipe_mock = AsyncMock()
    pipe_mock.rpush = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.hincrby = MagicMock()
    pipe_mock.hset = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis = SimpleNamespace(setex=AsyncMock(), pipeline=MagicMock(return_value=pipe_mock))

    with (
        patch.object(stream_service, "get_redis", return_value=redis),
        patch.object(stream_service, "publish_event_batch", AsyncMock(return_value=1)),
        patch.object(stream_service, "close_session", AsyncMock()) as close_mock,
    ):
        await stream_service.ingest_via_api_key(
            db=db,
            project_id=project_id,
            api_key_name="ci-runner-key",
            request=request,
        )

    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_service_ingest_via_api_key_409s_finalised_run_id():
    """Reusing a run_id whose session has already finalised must 409 — not
    silently create a new active session that would conflate two runs under
    the same display id."""
    project_id = uuid.uuid4()
    completed_session = SimpleNamespace(
        id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
        project_id=project_id,
        run_id="ci-build-1",
        status="completed",
    )
    request = SimpleNamespace(
        run_id="ci-build-1",
        events=[SimpleNamespace(model_dump=lambda: {"event_type": "test_result"})],
        meta=None,
    )

    db = FakeAsyncDB([FakeExecuteResult(scalar=completed_session)])
    db.get = AsyncMock(return_value=object())  # project exists

    from fastapi import HTTPException as _HTTPException

    with pytest.raises(_HTTPException) as exc_info:
        await stream_service.ingest_via_api_key(
            db=db,
            project_id=project_id,
            api_key_name="ci-runner-key",
            request=request,
        )
    assert exc_info.value.status_code == 409
    assert "ci-build-1" in str(exc_info.value.detail)
    # No new session was added to the DB — the request was rejected pre-write.
    assert db.added == []


@pytest.mark.asyncio
async def test_stream_service_ingest_via_api_key_404s_unknown_project():
    db = FakeAsyncDB([])
    db.get = AsyncMock(return_value=None)  # Project does not exist.

    request = SimpleNamespace(
        run_id="run-x",
        events=[SimpleNamespace(model_dump=lambda: {"event_type": "test_result"})],
        meta=None,
    )

    from fastapi import HTTPException as _HTTPException

    with pytest.raises(_HTTPException) as exc_info:
        await stream_service.ingest_via_api_key(
            db=db,
            project_id=uuid.uuid4(),
            api_key_name="key",
            request=request,
        )
    assert exc_info.value.status_code == 404


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
    # Three canned results, in call order. The FIRST is the live-project
    # lookup ``list_active_sessions`` now runs before anything else: the
    # endpoint unions Redis + completed LiveSessions + live_stream TestRuns,
    # and all three must exclude soft-deleted projects (an ADMIN with no
    # project pinned previously matched neither of the conditional filters and
    # saw sessions from projects that no longer exist). Returning this test's
    # project keeps it "live" so the rest of the assertions are unchanged.
    db = FakeAsyncDB([
        FakeExecuteResult(scalars=[project_id]),
        FakeExecuteResult(scalars=[completed_session]),
        FakeExecuteResult(scalars=[fallback_run]),
    ])
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
    # ``list_active_sessions`` now also decorates each session with its
    # per-(project, suite) ``run_seq`` via ``runs_service.fetch_run_seq_map``.
    # The helper fires its own SELECTs which would drain FakeAsyncDB's
    # canned-result list; stub it to an empty dict for this test, which
    # only asserts on session presence + ordering. Dedicated coverage
    # for the helper lives in tests/services/test_run_seq_map.py.
    with patch.dict(sys.modules, {"app.streams.live_run_state": live_state_module}), \
         patch("app.services.runs_service.fetch_run_seq_map", AsyncMock(return_value={})):
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
    # ``_close_session`` now goes through ``link_run_or_default`` so it can
    # fall back to the project's default release when session.release_name
    # is blank. The mock exposes that entry point.
    release_linker = SimpleNamespace(link_run_or_default=AsyncMock())
    live_state_module = SimpleNamespace(RedisLiveRunState=SimpleNamespace(complete=AsyncMock(return_value={"passed": 4, "failed": 1, "total": 5})))
    # Phase 3 (2026-05-16) — close_session now enqueues the AI pipeline
    # via the debouncer instead of calling ``run_agent_pipeline.apply_async``
    # directly. Stub the debouncer so we can assert on its call instead
    # of the legacy direct dispatch.
    debouncer_module = SimpleNamespace(enqueue_pipeline_for_run=AsyncMock(return_value="debounced"))

    with (
        patch.object(stream_service, "upsert_test_run", AsyncMock()) as upsert_mock,
        patch.dict(
            sys.modules,
            {
                "app.streams.live_run_state": live_state_module,
                "app.services.release_linker": release_linker,
                "app.services.ai_pipeline_debouncer": debouncer_module,
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
    release_linker.link_run_or_default.assert_awaited_once()
    # Item #2: service stages; router handler commits.
    db.commit.assert_not_awaited()
    persist_task.apply_async.assert_called_once()
    # Phase 3 — pipeline trigger now routes through the debouncer.
    # ``pipeline_task.apply_async`` is NOT called directly here; the
    # beat task drains the SortedSet later.
    debouncer_module.enqueue_pipeline_for_run.assert_awaited_once()
    pipeline_task.apply_async.assert_not_called()


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
    # Item #2: service stages only, router handler commits.
    db.commit.assert_not_awaited()


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
    """After the pilot refactor ``send_message`` receives a pre-authorized
    :class:`ChatSession` from the ``require_session_access`` dependency and
    stages the title mutation without committing. The router handler owns
    the commit, so this unit test should not see ``db.commit`` called."""
    session = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="New conversation",
        # Report-grounding anchors: unset for an ordinary (non-grounded) chat.
        active_test_run_id=None,
        active_report_id=None,
        active_report_version=None,
    )
    current_user = SimpleNamespace(id=session.user_id)
    payload = SimpleNamespace(message="Investigate latest failures", project_id=None)
    db = FakeAsyncDB([])
    conversation_agent_cls = Mock()
    conversation_agent_cls.return_value.chat = AsyncMock(return_value={"reply": "Here is the summary", "sources": [{"type": "run"}]})

    with patch.dict(sys.modules, {"app.agents.conversation": SimpleNamespace(ConversationAgent=conversation_agent_cls)}):
        result = await chat_service.send_message(db, session, payload, current_user)

    assert session.title == "Investigate latest failures"
    db.commit.assert_not_awaited()
    conversation_agent_cls.return_value.chat.assert_awaited_once()
    assert result["reply"] == "Here is the summary"
