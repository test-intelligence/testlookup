"""W08 TRND-07 -- ``GET /api/v1/analytics/coverage/untagged-tests`` (the Fix
tagging panel's list): the scope policy, the paging bounds, the read layer and
the exact body. The rows on a real database are
``tests/integration/test_coverage_snapshot_postgres.py``.
"""
from __future__ import annotations

import uuid

import pytest

from app.routers import analytics_coverage_map as router
from app.services import coverage_snapshot_service as svc
from app.services.analytics_scope import AnalyticsScope

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PATH = "/api/v1/analytics/coverage/untagged-tests"


def _route():
    from app.main import app

    return next(r for r in app.routes if getattr(r, "path", None) == PATH)


def test_the_policy_is_the_coverage_page_s_window():
    policy = router.UNTAGGED_TESTS_POLICY
    assert (policy.default_days, policy.max_days, policy.project_required) == (30, 90, True)
    assert policy.on_denied == "raise"


def test_page_and_size_are_bounded_by_the_route():
    from fastapi.dependencies.utils import get_flat_dependant

    params = {p.alias: p for p in get_flat_dependant(_route().dependant).query_params}

    def bounds(name):
        found = {}
        for item in params[name].field_info.metadata:
            for attr in ("ge", "le"):
                if hasattr(item, attr):
                    found[attr] = getattr(item, attr)
        return found

    assert bounds("page") == {"ge": 1} and params["page"].field_info.default == 1
    assert bounds("size") == {"ge": 1, "le": 200} and params["size"].field_info.default == 50
    assert svc.UNTAGGED_PAGE_MAX == 200


def test_the_route_is_cached_by_the_analytics_layer_under_its_own_namespace():
    route = _route()
    assert route.methods == {"GET"}
    assert getattr(route.endpoint, "__analytics_read__", None) == "coverage_untagged"


@pytest.mark.asyncio
async def test_the_body_is_exactly_the_contract_s(monkeypatch):
    seen: dict = {}

    async def untagged(db, scope, *, page, size):
        seen.update(page=page, size=size)
        return {"items": [], "total": 0, "page": page, "size": size, "window_days": scope.window_days}

    async def fake_meta(db, scope, **kwargs):
        return {}

    monkeypatch.setattr(svc, "untagged_tests", untagged)
    monkeypatch.setattr(router, "build_meta", fake_meta)
    scope = AnalyticsScope(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)  # type: ignore[arg-type]
    body = await router.coverage_untagged_tests.__wrapped__(page=3, size=20, scope=scope, db=object())
    assert set(body) == {"items", "total", "page", "size", "window_days", "meta"}
    assert seen == {"page": 3, "size": 20}
    assert set(body["meta"]["definitions"]) >= {"untagged", "catalogue_suite", "moving", "total"}
