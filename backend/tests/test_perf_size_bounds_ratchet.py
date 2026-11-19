"""Every page-size query parameter is bounded, and no bound grows unnoticed (GAP-C09, GAP-C12).

Walks the live ``app.openapi()`` schema, so a route added anywhere is seen without a list to keep:

* every size-like query parameter (``limit``, ``size``, ``page_size``, ``top_n``, ``*_limit``, ``max_*`` ...)
  declares a ``maximum``, or sits in ``REVIEWED_CLAMPS`` with the place its handler clamps or refuses it;
* the declared maxima equal a sorted snapshot, so raising ``le=500`` to ``le=5000`` (or dropping it) fails
  here and has to be argued for in review;
* GET routes that answer a bare JSON array with no paging parameter are frozen at today's set, so a new
  unbounded list endpoint fails until it takes a limit or is reviewed into the set.

Static: no database, no server, no clock. Three past pagination fixes (SCIM) were each found in production.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.main import app

SIZE_NAMES = frozenset(
    {
        "limit",
        "size",
        "page_size",
        "per_page",
        "top",
        "top_n",
        "count",
        "max_results",
        "k",
        "n",
        "take",
        "max_items",
        "max_rows",
    }
)
PAGING_NAMES = SIZE_NAMES | {"page", "cursor", "offset"}


def _is_size_like(name: str) -> bool:
    return (
        name in SIZE_NAMES
        or name.endswith("_limit")
        or name.endswith("_size")
        or name.startswith("max_")
    )


# Size-like params with no schema ``maximum``, each checked by hand at the pin (75a61f6a): where the handler
# clamps or refuses an out-of-range value. A new entry needs the same: name the clamp, or add ``le=``.
REVIEWED_CLAMPS: dict[str, str] = {
    "GET /api/v1/scim/v2/Users count": "routers/scim.py scim_list -> normalize_scim_pagination (200-item cap)",
    "POST /api/v1/admin/maintenance/backfill-placeholder-test-cases max_runs_per_project": "instance-admin maintenance trigger; the work runs in the Celery task, never in the request",
    "POST /api/v1/admin/maintenance/backfill-unassigned-failures max_runs_per_project": "instance-admin maintenance trigger; the work runs in the Celery task, never in the request",
    "GET /api/v1/projects/{project_id}/export-estimate max_runs": "routers/project_data.py estimate_project_export: 1 <= max_runs <= vocab.HISTORY_MAX_RUNS else 422",
    "GET /api/v1/analytics/chart-data top_n": "services/chart_data_service.py _parse_top_n (MAX_TOP_N_SERIES/POINTS)",
    "GET /api/v1/analytics/chart-data/rows top_n": "services/chart_rows_service.py -> charts.parse_chart_spec -> _parse_top_n",
    "GET /api/v1/analytics/chart-data/rows size": "services/chart_rows_service.py: 1 <= size <= MAX_PAGE_SIZE (200) else size_range",
    "GET /api/v1/analytics/test-scatter limit": "services test_scatter parse_scatter_spec: _int_param(limit, 1, MAX_LIMIT)",
    "GET /api/v1/analytics/execution/suite-history limit": "services/execution_metrics_service.py suite_history: _int_param(limit, 1, SUITE_HISTORY_MAX_LIMIT)",
    "GET /api/v1/analytics/execution-metrics top": "services/execution_metrics_service.py parse_metrics_request: 1..METRICS_MAX_TOP (100)",
    "GET /api/v1/analytics/execution-metrics suite_limit": "services/execution_metrics_service.py parse_metrics_request: 1..METRICS_MAX_SUITE_LIMIT (200)",
    "GET /api/v1/analytics/execution-metrics.csv suite_limit": "services/execution_metrics_service.py parse_metrics_request: 1..METRICS_MAX_SUITE_LIMIT (200)",
    "GET /api/v1/deep-investigate/{run_id}/estimate max_failures_analyzed": "lower-only override of the project's cap (agent_config_service.py: le=1000, _lower_only); no rows per unit",
    "GET /api/v1/activity/mine limit": "routers/activity.py my_activity_filters: 1..MINE_MAX_LIMIT else 422",
    "GET /api/v1/projects/{project_id}/llm-usage/history limit": "routers/llm_cost_budget.py: max(1, min(limit, 60))",
    "GET /api/v1/quarantine limit": "routers/flaky_quarantine.py: min(max(1, limit), 500)",
    "GET /api/v1/projects/{project_id}/investigations limit": "routers/agent_investigations.py: max(1, min(limit, 100))",
    "GET /api/v1/projects/{project_id}/agent-runs limit": "routers/agent_investigations.py: max(1, min(limit, 200))",
    "GET /api/v1/projects/{project_id}/fixer/attempts limit": "routers/fixer.py: max(1, min(limit, 100))",
}

# Declared maxima at the pin. Lowering a bound or adding a bounded param: update the entry. Raising one: argue
# for it in review (a list that pages 500 rows on a 15 s poll is a different product from one that pages 5,000).
MAXIMA: dict[str, int] = {
    "GET /api/v1/admin/dlq limit": 500,
    "GET /api/v1/admin/maintenance/dlq limit": 500,
    "GET /api/v1/admin/storage/deleted-projects limit": 200,
    "GET /api/v1/agents/pipelines limit": 100,
    "GET /api/v1/ai-eval/agent-stack-release-gate/runs limit": 100,
    "GET /api/v1/ai-eval/report-cycles limit": 100,
    "GET /api/v1/ai-eval/reviewer-quality/runs limit": 100,
    "GET /api/v1/ai-eval/runs limit": 100,
    "GET /api/v1/ai-eval/runs/{eval_run_id}/items limit": 100,
    "GET /api/v1/ai-eval/shadow-pairs limit": 100,
    "GET /api/v1/ai-eval/tier-comparison/runs limit": 100,
    "GET /api/v1/analytics/coverage/untagged-tests size": 200,
    "GET /api/v1/analytics/defects size": 100,
    "GET /api/v1/analytics/flaky-scores limit": 200,
    "GET /api/v1/analytics/flaky-tests limit": 100,
    "GET /api/v1/analytics/top-failing limit": 50,
    "GET /api/v1/audit-dashboard/events page_size": 200,
    "GET /api/v1/auth/users limit": 500,
    "GET /api/v1/canonical-test-cases size": 200,
    "GET /api/v1/canonical-test-cases/orphaned size": 200,
    "GET /api/v1/chat/sessions/{session_id}/messages limit": 200,
    "GET /api/v1/compliance-packs limit": 50,
    "GET /api/v1/deep-investigate/defects/pending-review page_size": 100,
    "GET /api/v1/deep-investigate/history size": 50,
    "GET /api/v1/deep-investigate/{run_id}/pre-scan limit": 50,
    "GET /api/v1/feature-flags/{key}/history limit": 100,
    "GET /api/v1/identity/events page_size": 200,
    "GET /api/v1/knowledge-sources page_size": 200,
    "GET /api/v1/knowledge-sources/{source_id}/sync-history limit": 100,
    "GET /api/v1/me/assigned-failures size": 100,
    "GET /api/v1/memory/projects/{project_id} size": 200,
    "GET /api/v1/notifications/history limit": 200,
    "GET /api/v1/onboarding/events limit": 500,
    "GET /api/v1/performance/slow-queries limit": 200,
    "GET /api/v1/project-imports limit": 10,
    "GET /api/v1/projects/{project_id}/activity limit": 200,
    "GET /api/v1/projects/{project_id}/activity/entity/{entity_type}/{entity_id} limit": 200,
    "GET /api/v1/projects/{project_id}/activity/export limit": 200,
    "GET /api/v1/projects/{project_id}/agent-actions limit": 200,
    "GET /api/v1/projects/{project_id}/data-jobs limit": 10,
    "GET /api/v1/projects/{project_id}/deletion/jobs limit": 200,
    "GET /api/v1/projects/{project_id}/duplicate-candidates size": 200,
    "GET /api/v1/projects/{project_id}/flaky-coach limit": 200,
    "GET /api/v1/projects/{project_id}/insights limit": 100,
    "GET /api/v1/projects/{project_id}/retention-holds limit": 200,
    "GET /api/v1/projects/{project_id}/reviews limit": 200,
    "GET /api/v1/runs size": 500,
    "GET /api/v1/runs/failed-ids limit": 5000,
    "GET /api/v1/runs/{run_id}/ai-history limit": 100,
    "GET /api/v1/runs/{run_id}/decision-reports limit": 100,
    "GET /api/v1/runs/{run_id}/likely-affected-tests limit": 50,
    "GET /api/v1/runs/{run_id}/tests size": 200,
    "GET /api/v1/runs/{run_id}/tests/{test_id}/ai-history limit": 50,
    "GET /api/v1/runs/{run_id}/timing timeline_limit": 2000,
    "GET /api/v1/scim/v2/Groups count": 500,
    "GET /api/v1/search size": 100,
    "GET /api/v1/search/global size": 100,
    "GET /api/v1/search/similar/{test_case_id} limit": 20,
    "GET /api/v1/settings/audit-log limit": 200,
    "GET /api/v1/test-management/audit size": 200,
    "GET /api/v1/test-management/cases size": 200,
    "GET /api/v1/test-management/cases/evidence-gaps size": 200,
    "GET /api/v1/test-management/cases/needs-review limit": 500,
    "GET /api/v1/test-management/cases/stale size": 100,
    "GET /api/v1/test-management/cases/{case_id}/timeline limit": 100,
    "GET /api/v1/test-management/plans size": 100,
    "GET /api/v1/test-management/suite-reviews limit": 500,
    "GET /api/v1/test-management/suites/{suite_name}/cases limit": 500,
    "GET /api/v1/test-management/suites/{suite_name}/cases size": 500,
    "GET /api/v1/test-management/suites/{suite_name}/changes limit": 200,
    "GET /api/v1/test-management/traceability/gaps size": 100,
    "GET /api/v1/users page_size": 200,
    "GET /api/v1/users/{user_id}/access-audit limit": 200,
    "GET /api/v1/webhooks/{subscription_id}/deliveries limit": 200,
    "POST /api/v1/projects/{project_id}/attribution-rules/preview limit": 200,
}

# GET routes answering a bare JSON array with no paging parameter, at the pin. A new one should take a
# ``limit`` (and a bound); one that is really fixed-size (a catalogue, one user's preferences) is added here.
BARE_LIST_GETS: frozenset[str] = frozenset(
    {
        "GET /api/v1/agents/catalog",
        "GET /api/v1/agents/pipelines/{pipeline_id}/stages",
        "GET /api/v1/ai-eval/datasets",
        "GET /api/v1/chat/sessions",
        "GET /api/v1/deep-investigate/{run_id}/clusters",
        "GET /api/v1/deep-investigate/{run_id}/findings",
        "GET /api/v1/digests/subscriptions",
        "GET /api/v1/feature-flags",
        "GET /api/v1/integration-health/status",
        "GET /api/v1/keys",
        "GET /api/v1/notifications/preferences",
        "GET /api/v1/projects",
        "GET /api/v1/projects/{project_id}/members",
        "GET /api/v1/projects/{project_id}/ownership/rules",
        "GET /api/v1/projects/{project_id}/ownership/rules/export",
        "GET /api/v1/projects/{project_id}/ownership/team-channels",
        "GET /api/v1/release-gate-policies",
        "GET /api/v1/release-gate-policies/history/{project_id}",
        "GET /api/v1/release-gate-policies/system-history",
        "GET /api/v1/releases/{release_id}/compliance-packs",
        "GET /api/v1/reports/runs/{run_id}/share-links",
        "GET /api/v1/reports/summary/exports",
        "GET /api/v1/scim-tokens",
        "GET /api/v1/sso/configs",
        "GET /api/v1/test-management/batches/{batch_id}/coverage",
        "GET /api/v1/test-management/cases/{case_id}/allowed-transitions",
        "GET /api/v1/test-management/cases/{case_id}/comments",
        "GET /api/v1/test-management/cases/{case_id}/history",
        "GET /api/v1/test-management/cases/{case_id}/reviews",
        "GET /api/v1/test-management/plans/{plan_id}/items",
        "GET /api/v1/test-management/strategies",
        "GET /api/v1/test-management/suite-owners",
        "GET /api/v1/test-management/suite-reviews/by-run/{test_run_id}",
        "GET /api/v1/users/invitations",
        "GET /api/v1/webhooks",
    }
)


def _schema_maximum(schema: dict[str, Any]) -> Optional[float]:
    for key in ("maximum", "exclusiveMaximum"):
        if key in schema:
            return schema[key]
    for combinator in ("anyOf", "oneOf", "allOf"):
        for alternative in schema.get(combinator, []):
            bound = _schema_maximum(alternative)
            if bound is not None:
                return bound
    return None


def _operations():
    schema = app.openapi()
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict) and "responses" in operation:
                yield method.upper(), path, operation


def _size_params() -> dict[str, Optional[float]]:
    found: dict[str, Optional[float]] = {}
    for method, path, operation in _operations():
        for param in operation.get("parameters", []):
            if param.get("in") == "query" and _is_size_like(param["name"]):
                found[f"{method} {path} {param['name']}"] = _schema_maximum(
                    param.get("schema", {})
                )
    return found


def _bare_list_gets() -> set[str]:
    bare = set()
    for method, path, operation in _operations():
        if method != "GET":
            continue
        body = (
            operation["responses"]
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if body.get("type") != "array":
            continue
        query = {
            p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"
        }
        if not query & PAGING_NAMES:
            bare.add(f"GET {path}")
    return bare


@pytest.fixture(scope="module")
def size_params() -> dict[str, Optional[float]]:
    found = _size_params()
    # Positive control: the walk reaches the hot routes (a schema change that hid params would pass vacuously).
    for key in (
        "GET /api/v1/runs size",
        "GET /api/v1/runs/{run_id}/tests size",
        "GET /api/v1/search size",
    ):
        assert key in found, f"{key} not found in app.openapi(); the walk is broken"
    assert len(found) >= 90, f"only {len(found)} size-like query params found"
    return found


def test_every_size_param_is_bounded_or_reviewed(size_params):
    unbounded = sorted(
        key
        for key, bound in size_params.items()
        if bound is None and key not in REVIEWED_CLAMPS
    )
    assert not unbounded, (
        "size-like query params with no `le=` and no reviewed clamp (an unbounded read per request): "
        f"{unbounded}. Add `le=` to the Query, or add the param to REVIEWED_CLAMPS naming its clamp."
    )


def test_reviewed_clamps_are_still_needed(size_params):
    stale = sorted(
        key
        for key in REVIEWED_CLAMPS
        if key not in size_params or size_params[key] is not None
    )
    assert not stale, (
        f"REVIEWED_CLAMPS entries that are gone or now declare a maximum (remove them): {stale}"
    )


def test_declared_maxima_match_the_snapshot(size_params):
    declared = {key: bound for key, bound in size_params.items() if bound is not None}
    raised = {
        k: (MAXIMA[k], v) for k, v in declared.items() if k in MAXIMA and v > MAXIMA[k]
    }
    lowered = {
        k: (MAXIMA[k], v) for k, v in declared.items() if k in MAXIMA and v < MAXIMA[k]
    }
    new = {k: v for k, v in declared.items() if k not in MAXIMA}
    gone = sorted(k for k in MAXIMA if k not in declared)
    assert not raised, f"page-size bounds RAISED (snapshot -> now): {raised}"
    assert not gone, f"bounded params that lost their maximum or disappeared: {gone}"
    assert not new, f"new bounded size params; add them to MAXIMA: {new}"
    assert not lowered, f"bounds lowered; update MAXIMA: {lowered}"


def test_hot_list_bounds_are_pinned(size_params):
    # The lists the SPA polls (runs 15 s, run tests 5 s) and the search pages, named so the failure is plain.
    assert size_params["GET /api/v1/runs size"] == 500
    assert size_params["GET /api/v1/runs/{run_id}/tests size"] == 200
    assert size_params["GET /api/v1/runs/failed-ids limit"] == 5000
    assert size_params["GET /api/v1/search size"] == 100
    assert size_params["GET /api/v1/search/global size"] == 100


def test_bare_list_gets_are_frozen():
    current = _bare_list_gets()
    assert len(current) >= 1, (
        "no bare-list GET found; the response-shape walk is broken"
    )
    added = sorted(current - BARE_LIST_GETS)
    removed = sorted(BARE_LIST_GETS - current)
    assert not added, (
        f"new GET routes return an unpaged JSON array: {added}. Give them a bounded `limit`, or add them to "
        "BARE_LIST_GETS if the list is fixed-size."
    )
    assert not removed, (
        f"BARE_LIST_GETS entries that now page or are gone (remove them): {removed}"
    )
