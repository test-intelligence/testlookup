"""Prove M17 saved-view regressions kill unsafe behavior."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "shared-view-project-scope",
        "backend/app/routers/saved_views.py",
        "    await resolve_project_scope(db, current_user, str(view.project_id))\n",
        "    pass  # unsafe: shared UUID bypasses project membership\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_shared_view_uuid_does_not_cross_project_membership",
    ),
    Mutation(
        "projectless-shared-view-privacy",
        "backend/app/routers/saved_views.py",
        "        if view.user_id != current_user.id:\n",
        "        if False and view.user_id != current_user.id:\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_projectless_shared_view_is_private_to_its_owner",
    ),
    Mutation(
        "all-projects-saved-view-scope",
        "backend/app/routers/saved_views.py",
        "    elif allowed is not None:\n",
        "    elif False and allowed is not None:\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_all_projects_scope_queries_accessible_and_owned_global_views",
    ),
    Mutation(
        "removed-member-update-scope",
        "backend/app/routers/saved_views.py",
        "    await _require_view_project_access(db, current_user, view)\n    if view.user_id != current_user.id:\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Only the owner can edit\")\n",
        "    if view.user_id != current_user.id:\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Only the owner can edit\")\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_removed_member_cannot_mutate_an_owned_project_view",
    ),
    Mutation(
        "removed-member-delete-scope",
        "backend/app/routers/saved_views.py",
        "    await _require_view_project_access(db, current_user, view)\n    if view.user_id != current_user.id:\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Only the owner can delete\")\n",
        "    if view.user_id != current_user.id:\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Only the owner can delete\")\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_removed_member_cannot_mutate_an_owned_project_view",
    ),
    Mutation(
        "saved-layout-server-limit",
        "backend/app/models/schemas.py",
        "MAX_SAVED_VIEW_INSTANCES = 12\n",
        "MAX_SAVED_VIEW_INSTANCES = 120\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_saved_view_contract_rejects_layouts_over_the_widget_limit",
    ),
    Mutation(
        "saved-layout-shape",
        "backend/app/models/schemas.py",
        '        if not isinstance(value, list):\n            raise ValueError(f"filters.{key} must be a list")\n',
        "        if False and not isinstance(value, list):\n            raise ValueError(f\"filters.{key} must be a list\")\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_saved_view_contract_rejects_malformed_analytics_layouts",
    ),
    Mutation(
        "saved-view-page-vocabulary",
        "backend/app/models/schemas.py",
        'SAVED_VIEW_PAGES = Literal["dashboard", "trends", "coverage", "defects", "failures"]\n',
        "SAVED_VIEW_PAGES = str\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_saved_view_contract_rejects_unknown_pages",
    ),
    Mutation(
        "saved-view-page-query",
        "frontend/src/hooks/useAnalyticsView.ts",
        "        params: { ...(projectId ? { project_id: projectId } : {}), page },\n",
        "        params: { ...(projectId ? { project_id: projectId } : {}) },\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#requests one page",
    ),
    Mutation(
        "legacy-saved-view-page-query",
        "backend/app/routers/saved_views.py",
        '                    SavedView.filters["page"].as_string() == page,\n',
        '                    SavedView.filters["page"].as_string() == "never-match",\n',
        "backend/tests/test_exploratory_m17_analytics_views.py::test_page_filter_keeps_pre_0049_filter_only_layouts_discoverable",
    ),
    Mutation(
        "widget-selection-persisted-value",
        "frontend/src/hooks/useAnalyticsView.ts",
        "    await persist(next)\n    setDirty(false)\n",
        "    await persist(instances)\n    setDirty(false)\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#persists the newly selected widgets",
    ),
    Mutation(
        "project-switch-write-scope",
        "frontend/src/hooks/useAnalyticsView.ts",
        "  const scopeKey = `${projectId ?? 'all'}:${page}`\n",
        "  const scopeKey = page // unsafe: project changes retain the previous PATCH target\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#never patches the previous project",
    ),
    Mutation(
        "late-create-response-scope",
        "frontend/src/hooks/useAnalyticsView.ts",
        "      if (currentScopeRef.current === scopeKey) setSavedViewId(created.id)\n",
        "      setSavedViewId(created.id)\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#discards a create response",
    ),
    Mutation(
        "owned-view-persistence-authority",
        "frontend/src/hooks/useAnalyticsView.ts",
        "    const layoutView = ownedView ?? scopedViews[0]\n",
        "    const layoutView = scopedViews[0]\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#uses a shared layout",
    ),
    Mutation(
        "failed-load-does-not-hydrate",
        "frontend/src/hooks/useAnalyticsView.ts",
        "  } else if (!isLoading && !error && hydratedScope !== scopeKey) {\n",
        "  } else if (!isLoading && hydratedScope !== scopeKey) {\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#hydrates after a failed request",
    ),
    Mutation(
        "pre-hydration-write-fence",
        "frontend/src/hooks/useAnalyticsView.ts",
        "  const setWidgets = useCallback(async (ids: string[]) => {\n    if (hydratedScope !== scopeKey) return\n",
        "  const setWidgets = useCallback(async (ids: string[]) => {\n    if (false && hydratedScope !== scopeKey) return\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#does not create a duplicate view",
    ),
    Mutation(
        "stored-layout-client-limit",
        "frontend/src/components/analytics/widgetRegistry.ts",
        "  for (const entry of raw.slice(0, MAX_INSTANCES_PER_PAGE)) {\n",
        "  for (const entry of raw) {\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#requests one page",
    ),
    Mutation(
        "removed-widget-filter",
        "frontend/src/components/analytics/widgetRegistry.ts",
        "    if (!template) continue\n",
        "    if (false && !template) continue\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#requests one page",
    ),
    Mutation(
        "duplicate-instance-repair",
        "frontend/src/components/analytics/widgetRegistry.ts",
        "    if (seen.has(instanceId)) instanceId = randomUUID()\n",
        "    if (false && seen.has(instanceId)) instanceId = randomUUID()\n",
        "frontend/src/hooks/useAnalyticsView.test.tsx#requests one page",
    ),
    Mutation(
        "offered-widget-renderer-contract",
        "frontend/src/components/analytics/widgetRegistry.ts",
        "  { id: 'total_executions_kpi', label: 'Total Executions',",
        "  { id: 'phantom_widget', label: 'Total Executions',",
        "frontend/src/hooks/useAnalyticsView.test.tsx#offers only widget IDs",
    ),
    Mutation(
        "trend-panel-visibility",
        "frontend/src/pages/TrendsPage.tsx",
        "          {analyticsView.widgetIds.includes('daily_breakdown') && (\n",
        "          {true && (\n",
        "frontend/src/pages/TrendsPage.test.tsx#removes deselected trend panels",
    ),
    Mutation(
        "coverage-panel-visibility",
        "frontend/src/pages/CoveragePage.tsx",
        "              {analyticsView.widgetIds.includes('pass_rate_by_suite') && (\n",
        "              {true && (\n",
        "frontend/src/pages/CoveragePage.test.tsx#removes the suite panel",
    ),
    Mutation(
        "defect-panel-visibility",
        "frontend/src/pages/DefectsPage.tsx",
        "          {analyticsView.widgetIds.includes('defect_category_bar') && (\n",
        "          {true && (\n",
        "frontend/src/pages/DefectsPage.test.tsx#removes the category panel",
    ),
    Mutation(
        "failure-panel-visibility",
        "frontend/src/pages/FailureAnalysisPage.tsx",
        "          {analyticsView.widgetIds.includes('failure_category_pie') && (\n",
        "          {true && (\n",
        "frontend/src/pages/FailureAnalysisPage.test.tsx#removes deselected failure panels",
    ),
    Mutation(
        "calendar-window-comparison",
        "frontend/src/pages/CoveragePage.tsx",
        "    const currentStart = shiftDayIso(utcDayIso(), -(days - 1))\n",
        "    const currentStart = points[Math.floor(points.length / 2)].date\n",
        "frontend/src/pages/CoveragePage.test.tsx#does not invent a prior period",
    ),
    Mutation(
        "value-metrics-error-state",
        "frontend/src/pages/ValueMetricsPage.tsx",
        "  if (error) {\n",
        "  if (false && error) {\n",
        "frontend/src/pages/ValueMetricsPage.test.tsx#renders a retryable request error",
    ),
    Mutation(
        "billing-error-state",
        "frontend/src/pages/settings/BillingPage.tsx",
        "  if (isError || !overview) {\n",
        "  if (false) {\n",
        "frontend/src/pages/settings/BillingPage.test.tsx#renders a retryable error",
    ),
    Mutation(
        "billing-half-open-period",
        "frontend/src/pages/settings/BillingPage.tsx",
        "        subtitle={`Current period: ${formatDate(overview.period_start)} — ${formatPeriodEnd(overview.period_end)}`}\n",
        "        subtitle={`Current period: ${formatDate(overview.period_start)} — ${formatDate(overview.period_end)}`}\n",
        "frontend/src/pages/settings/BillingPage.test.tsx#renders the backend UTC half-open month",
    ),
    Mutation(
        "trend-calendar-boundary",
        "backend/app/services/metrics_service.py",
        "        days=days - 1\n",
        "        days=days\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_trend_window_starts_at_utc_midnight_and_covers_exactly_n_days",
    ),
    Mutation(
        "trend-utc-bucket",
        "backend/app/services/metrics_service.py",
        "DATE_TRUNC('day', tr.created_at AT TIME ZONE 'UTC') AS day",
        "DATE_TRUNC('day', tr.created_at) AS day",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_trend_window_starts_at_utc_midnight_and_covers_exactly_n_days",
    ),
    Mutation(
        "all-project-cache-invalidation",
        "backend/app/services/cache_service.py",
        "            (f\"analytics:*:{project_id}:*\", \"analytics:*:all:*\")\n",
        "            (f\"analytics:*:{project_id}:*\",)\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_project_cache_invalidation_also_clears_all_projects_analytics",
    ),
    Mutation(
        "sentinel-cache-invalidation",
        "backend/app/services/ingestion.py",
        "            await invalidate_analytics_cache(str(run.project_id))\n",
        "            pass  # unsafe: stale analytics caches survive ingestion\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_file_and_unified_ingestion_invalidate_analytics_only_after_commit",
    ),
    Mutation(
        "unified-cache-invalidation",
        "backend/app/services/ingestion_pipeline.py",
        "            await invalidate_analytics_cache(str(pid))\n",
        "            pass  # unsafe: stale analytics caches survive ingestion\n",
        "backend/tests/test_exploratory_m17_analytics_views.py::test_file_and_unified_ingestion_invalidate_analytics_only_after_commit",
    ),
    Mutation(
        "terminal-cache-invalidation",
        "backend/app/services/ingestion_pipeline.py",
        '    await invalidate_analytics_cache(str(pid))\n\n    logger.info(\n        "post_ingestion_operations_staged",\n',
        '    logger.info(\n        "post_ingestion_operations_staged",\n',
        "backend/tests/test_exploratory_m17_analytics_views.py::test_file_and_unified_ingestion_invalidate_analytics_only_after_commit",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    if test.startswith("frontend/"):
        npm = "npm.cmd" if os.name == "nt" else "npm"
        path, separator, pattern = test.removeprefix("frontend/").partition("#")
        command = [npm, "run", "test", "--", path]
        if separator:
            command.extend(["-t", pattern])
        return subprocess.run(
            command,
            cwd=ROOT / "frontend",
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m17-mutation-{suffix}",
            test,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def restore_source(path: Path, content: bytes) -> None:
    """Retry transient Windows file-handle races after a test subprocess exits."""
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        source = originals[path].decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            restore_source(path, originals[path])
    print(f"M17 mutation check passed: {len(MUTATIONS)} unsafe changes were killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
