"""``GET /api/v1/canonical-test-cases`` pages, and reports the full total (re-audit M7).

The SQL itself is pinned in ``tests/regression/test_canonical_cases_are_paged.py``.
These pin the route: that it passes a bounded page to the service by default,
reports the service's full ``total`` rather than the page length, rejects
out-of-range paging, still refuses a project the caller cannot see -- and that
the suite view, which selects across a whole suite, is NOT paged.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

URL = "/api/v1/canonical-test-cases"


@pytest.fixture
def scope(monkeypatch):
    """``routers/suites.py`` imports ``get_accessible_project_ids`` by name."""
    state = {"accessible": None}

    async def _accessible(_db, _user):
        return state["accessible"]

    monkeypatch.setattr("app.routers.suites.get_accessible_project_ids", _accessible)
    return state


@pytest.fixture
def service(monkeypatch):
    calls: list[dict] = []

    async def _list(_db, **kwargs):
        calls.append(kwargs)
        return [], 42

    monkeypatch.setattr("app.routers.suites.svc.list_canonical_test_cases", _list)
    return calls


async def test_the_route_pages_and_reports_the_full_total(client, auth_as, scope, service):
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(URL, params={"page": 2, "size": 10})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 42, "total is the page length, so a caller cannot page"
    assert (body["page"], body["size"]) == (2, 10)
    assert (service[-1]["page"], service[-1]["size"]) == (2, 10)


async def test_the_default_is_a_bounded_page(client, auth_as, scope, service):
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(URL)

    assert resp.status_code == 200, resp.text
    assert (service[-1]["page"], service[-1]["size"]) == (1, 25), (
        "an unpaged request still reads every row the caller can see"
    )


@pytest.mark.parametrize("params", [{"size": 201}, {"size": 0}, {"page": 0}])
async def test_out_of_range_paging_is_rejected(client, auth_as, scope, service, params):
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(URL, params=params)

    assert resp.status_code == 422, resp.text
    assert service == []


async def test_a_project_the_caller_cannot_see_is_still_forbidden(client, auth_as, scope, service):
    auth_as(role=UserRole.QA_ENGINEER)
    scope["accessible"] = {uuid.uuid4()}

    resp = await client.get(URL, params={"project_id": str(uuid.uuid4())})

    assert resp.status_code == 403, resp.text
    assert service == [], "the service ran for a project the caller cannot see"


async def test_the_suite_view_is_not_paged(client, auth_as, service, monkeypatch):
    """SuiteCasesPage selects and bulk-links across the whole suite. Handing it
    one page would silently act on a fraction of the suite."""
    suite = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), name="checkout")

    async def _suite(_db, _suite_id):
        return suite

    async def _allowed(*_args, **_kwargs):
        return None

    async def _legacy(_db, _suite):
        return []

    monkeypatch.setattr("app.routers.suites.svc.get_suite_or_404", _suite)
    monkeypatch.setattr("app.routers.suites._enforce_project_access", _allowed)
    monkeypatch.setattr("app.routers.suites.svc.list_legacy_suite_test_cases", _legacy)
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(f"/api/v1/suites/{suite.id}/test-cases")

    assert resp.status_code == 200, resp.text
    assert service, "the suite view did not read the canonical list"
    assert service[-1].get("page") is None and service[-1].get("size") is None, (
        f"the suite view was handed one page of its suite: {service[-1]}"
    )
