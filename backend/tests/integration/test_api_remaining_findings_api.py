"""The last findings of the API/MCP launch QA sweep: test-management writes, the search and plan
status filters, and the number of matches on the two bare-list routes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core import test_library_vocab as vocab
from app.models.postgres import TestStatus as CaseStatus, UserRole
from app.models.schemas import ManagedTestCaseCreate, ManagedTestCaseUpdate, TestPlanUpdate as PlanUpdate
from app.routers import flaky_quarantine, search
from app.services import flaky_quarantine_service

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _create(**fields):
    return ManagedTestCaseCreate(project_id=PROJECT_ID, title="Checkout total", **fields)


# ── test-management case writes follow the import's rules ────────────────────


@pytest.mark.parametrize(
    ("field", "sent", "stored"),
    [
        ("priority", "P1", "high"), ("priority", " Critical ", "critical"), ("priority", "low", "low"),
        ("severity", "Major", "major"), ("severity", "medium", "medium"),
        ("test_type", "API Smoke", "api smoke"), ("test_type", "contract-v2", "contract-v2"),
        ("automation_status", "Automated", "automated"),
    ],
)
async def test_case_fields_are_normalised_on_create_and_on_an_edit(edit_case, field, sent, stored):
    assert getattr(_create(**{field: sent}), field) == stored
    assert getattr(await edit_case(**{field: sent}), field) == stored


@pytest.mark.parametrize(
    ("field", "sent", "said"),
    [
        ("priority", "urgent", vocab.PRIORITY_INVALID_SENTENCE),
        ("priority", "P4", vocab.PRIORITY_INVALID_SENTENCE),
        ("severity", "x" * 21, "severity must be 1 to 20 characters."),
        ("severity", "  ", "severity must be 1 to 20 characters."),
        ("test_type", "smoke/api", "test_type must be 1 to 50 letters"),
        ("test_type", "t" * 51, "test_type must be 1 to 50 letters"),
        ("automation_status", "flaky", "automation_status must be not_automated, in_progress, automated or broken."),
    ],
)
async def test_case_fields_refuse_a_value_the_library_cannot_use(edit_case, field, sent, said):
    with pytest.raises(ValidationError) as refused:
        _create(**{field: sent})
    (error,) = refused.value.errors()
    assert error["loc"] == (field,) and said in error["msg"]

    with pytest.raises(HTTPException) as refused_edit:
        await edit_case(**{field: sent})
    assert refused_edit.value.status_code == 422 and said in refused_edit.value.detail


def test_case_defaults_and_omitted_fields_are_unchanged():
    created = _create()
    update = ManagedTestCaseUpdate(expected_version=3, title="Checkout total v2")

    assert (created.priority, created.severity, created.test_type, created.automation_status) == (
        "medium", "major", "functional", "not_automated",
    )
    assert update.model_dump(exclude_unset=True) == {"expected_version": 3, "title": "Checkout total v2"}


# A case as the demo seed and older builds stored it: values from before the rules.
LEGACY = {"priority": "showstopper", "severity": "Sev-1 (customer reported)", "test_type": "Smoke/API",
          "automation_status": "manual"}


@pytest.fixture
def edit_case(monkeypatch):
    """``update_managed_test_case`` on one stored case, with persistence stubbed out."""
    from app.services import test_management_service as service

    case = SimpleNamespace(
        id=PROJECT_ID, project_id=PROJECT_ID, title="Checkout total", status="draft", version=4,
        edited_since_approval_version=None, parameters=None, **LEGACY,
    )
    monkeypatch.setattr(service, "get_test_case_or_404", AsyncMock(return_value=case))
    monkeypatch.setattr(service, "stage_test_case_snapshot", Mock())
    monkeypatch.setattr(service, "audit_event", AsyncMock())

    async def edit(change_type="updated", **fields):
        payload = ManagedTestCaseUpdate(expected_version=case.version, **fields)
        return await service.update_managed_test_case(
            AsyncMock(), case.id, payload, SimpleNamespace(id=PROJECT_ID), change_type=change_type,
        )

    edit.case = case
    return edit


@pytest.mark.parametrize("field", sorted(LEGACY))
async def test_an_edit_that_sends_a_stored_value_back_keeps_it(edit_case, field):
    """A client that sends the whole case back must not be refused for a value it did not change."""
    edited = await edit_case(title="Checkout total v2", **{field: LEGACY[field]})

    assert getattr(edited, field) == LEGACY[field] and edited.title == "Checkout total v2" and edited.version == 5


@pytest.mark.parametrize("change_type", ["restored", "bulk_updated"])
async def test_a_restore_or_bulk_edit_can_put_back_a_value_from_before_the_rules(edit_case, change_type):
    """An old version holding a seeded "manual" automation status must stay restorable."""
    edit_case.case.automation_status, edit_case.case.priority = "automated", "high"

    restored = await edit_case(change_type=change_type, automation_status="manual", priority="showstopper")

    assert (restored.automation_status, restored.priority) == ("manual", "showstopper")


async def test_an_edit_cannot_introduce_a_value_from_before_the_rules(edit_case):
    edit_case.case.automation_status = "automated"

    with pytest.raises(HTTPException) as refused:
        await edit_case(automation_status="manual")

    assert refused.value.status_code == 422 and "automation_status must be" in refused.value.detail
    assert edit_case.case.automation_status == "automated" and edit_case.case.version == 4


def test_the_update_model_itself_accepts_any_stored_value():
    """The model carries restores too, so it does not judge the vocabulary."""
    assert ManagedTestCaseUpdate(expected_version=1, **LEGACY).model_dump(exclude_unset=True) == {
        "expected_version": 1, **LEGACY,
    }


def test_the_write_rules_are_the_imports_rules():
    """One vocabulary: what the API accepts is what an import row may carry."""
    for priority in (*vocab.CASE_PRIORITIES, *vocab.PRIORITY_ALIASES):
        assert _create(priority=priority).priority in vocab.CASE_PRIORITIES
    for status in vocab.AUTOMATION_STATUSES:
        assert _create(automation_status=status).automation_status == status
    for severity in vocab.CASE_SEVERITIES:
        assert _create(severity=severity).severity == severity


# ── status filters that answered an empty page ───────────────────────────────


async def test_plan_list_refuses_a_status_no_plan_can_have(client, auth_as):
    auth_as(role=UserRole.ADMIN)

    response = await client.get("/api/v1/test-management/plans", params={"status": "done"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "status must be one of: " + ", ".join(PlanUpdate.PLAN_STATUSES)


@pytest.mark.parametrize("status", ["error", "FAILED,BROKEN"])
async def test_search_refuses_a_status_no_test_can_have(client, auth_as, status):
    auth_as(role=UserRole.ADMIN)

    response = await client.get("/api/v1/search", params={"q": "checkout", "status": status})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "status must be one of PASSED, FAILED, SKIPPED, BROKEN, UNKNOWN."


def test_the_search_status_list_is_the_test_status_enum():
    assert search._TEST_STATUSES == tuple(status.value for status in CaseStatus.__members__.values())


# ── the number of matches on the bare-list routes ────────────────────────────


@pytest.mark.parametrize(("matches", "header"), [(120, "120"), (0, "2")], ids=["more-than-the-page", "never-below-the-page"])
async def test_quarantine_list_states_how_many_requests_match(client, auth_as, monkeypatch, matches, header):
    auth_as(role=UserRole.ADMIN)
    monkeypatch.setattr(flaky_quarantine.svc, "list_requests", AsyncMock(return_value=["row", "row"]))
    monkeypatch.setattr(flaky_quarantine, "_with_owner_names", AsyncMock(return_value=[]))
    count = AsyncMock(return_value=matches)
    monkeypatch.setattr(flaky_quarantine.svc, "count_requests", count)

    response = await client.get("/api/v1/quarantine", params={"status_filter": "proposed", "live_only": True, "limit": 2})

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == header
    assert count.await_args.kwargs == {"project_ids": None, "status_filter": "PROPOSED", "live_only": True}


def _no_rows(count):
    """One ``db.execute`` answer: no rows for a list read, ``count`` for a count read."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar.return_value = count
    return result


def _where(statement) -> str:
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    return sql.split("WHERE", 1)[1].split("ORDER BY")[0].strip()


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"project_ids": {PROJECT_ID}},
        {"status_filter": "QUARANTINED"},
        {"live_only": True},
        {"project_ids": {PROJECT_ID}, "status_filter": "PROPOSED", "live_only": True},
    ],
    ids=["none", "project", "status", "live", "all"],
)
async def test_the_quarantine_count_uses_the_lists_filters(filters):
    """The count repeats the list's four filters; this holds the two WHERE clauses equal."""
    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return _no_rows(count=7)

    assert await flaky_quarantine_service.list_requests(Session(), limit=5, **filters) == []
    assert await flaky_quarantine_service.count_requests(Session(), **filters) == 7

    listed, counted = statements
    assert _where(listed) == _where(counted)
    assert "LIMIT" in str(listed.compile(compile_kwargs={"literal_binds": True})).upper()
    assert "LIMIT" not in str(counted.compile(compile_kwargs={"literal_binds": True})).upper()


@pytest.mark.parametrize(("matches", "header"), [(80, "80"), (None, "0")], ids=["counted", "no-count-answer"])
async def test_review_queue_states_how_many_reviews_match(matches, header):
    from fastapi import Response

    from app.routers import reviews

    statements = []

    class Session:
        async def execute(self, statement):
            statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
            return _no_rows(count=matches)

    response = Response()
    rows = await reviews.list_reviews(
        project_id=PROJECT_ID, state="pending_review", limit=2, response=response,
        db=Session(), _=None,
    )

    listed, counted = statements
    assert rows == [] and response.headers["X-Total-Count"] == header
    assert "LIMIT 2" in listed and "count(" in counted.lower() and "LIMIT" not in counted
    assert "pending_review" in counted and str(PROJECT_ID).replace("-", "") in counted.replace("-", "")


async def test_an_empty_membership_counts_nothing_without_a_query():
    session = AsyncMock()

    assert await flaky_quarantine_service.count_requests(session, project_ids=set()) == 0
    session.execute.assert_not_awaited()
