/**
 * Settings › Performance › Latency Budgets at 1280 and 1440 px. The table had a Description column beside the
 * operation and five number columns; the descriptions name endpoints ("GET /analytics/execution/suite-history, …")
 * that cannot wrap, so the table could not get narrower than ~800 px and `#main-content` scrolled sideways at
 * 1280 (the W29e homelab walk, F-W29e-1). The description now sits under the operation and wraps anywhere.
 *
 * The rows are the codified budgets (`backend/app/services/performance_budgets.py`) that set the widths: the
 * longest operation name, the largest budget, the longest handler, the descriptions with the longest endpoint
 * paths, a route with no requests, a breach, and the harness-only operations with no HTTP route.
 *
 * Hermetic and fail closed (`production-pages.ts`): every request is mocked.
 */
import { expect, test, type Page } from '@playwright/test'
import { inPageLine, minContentSlack } from '../lib/inPageGeometry'
import { assertHermetic, expectNoHorizontalOverflow, networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT } from '../visual/production/fixtures'

const budget = (operation: string, p50_ms: number, p95_ms: number, p99_ms: number, description: string) =>
  ({ operation, p50_ms, p95_ms, p99_ms, description })

const LATENCY = [
  budget('run_list', 50, 250, 500,
    'Paginated run list (page_size=20). Budget bumped from 200ms → 250ms after load-test wave gave 220ms p95 under 10-way concurrent on dev VMs.'),
  budget('flaky_coach', 100, 600, 1200,
    'Project flaky leaderboard. First call per project populates a cache via dedicated write session (item #4); steady state is fast.'),
  budget('run_intelligence', 200, 800, 2000,
    'Run intelligence snapshot (warm cache). Cold cache populates via dedicated write session; this budget covers steady-state reads.'),
  budget('execution_suite_history', 60, 300, 600,
    'GET /analytics/execution/suite-history, 90 d, one suite: an index range on suite_executions (project_id, suite_key, run_created_at).'),
  budget('coverage_untagged_tests', 120, 600, 1200,
    "GET /analytics/coverage/untagged-tests, 90 d, one project: the unlabelled executions grouped by test, counted and paged in one statement, with each page row's catalogue suite."),
  budget('run_timing', 80, 400, 800,
    "GET /runs/{run_id}/timing: one run's aggregate, suites, slowest 20 (ix_test_cases_run_duration), lanes, a timeline of at most 2,000 tests and the run's duration findings."),
  budget('search_reindex_incremental', 5000, 15000, 30000, 'Incremental search reindex (up to 5000 docs)'),
  budget('search_reindex_full', 30000, 120000, 300000, 'Full search reindex for 100K test cases'),
]

const observedRow = (operation: string, http_handler: string, p: [number, number, number] | null, count: number) => {
  const b = LATENCY.find((l) => l.operation === operation)
  if (!b) throw new Error(`no budget for ${operation}`)
  return {
    operation, http_handler, http_method: 'GET',
    budget_p50_ms: b.p50_ms, budget_p95_ms: b.p95_ms, budget_p99_ms: b.p99_ms,
    observed_p50_ms: p?.[0] ?? null, observed_p95_ms: p?.[1] ?? null, observed_p99_ms: p?.[2] ?? null,
    count, measured: p !== null, breach: p !== null && p[1] > b.p95_ms,
  }
}

const OBSERVED = {
  window: '1h', source: 'in_app', measured: true, slot_seconds: 300,
  budgets: [
    observedRow('run_list', '/api/v1/runs', [42, 310, 470], 123456),
    observedRow('flaky_coach', '/api/v1/projects/{project_id}/flaky-coach', null, 0),
    observedRow('run_intelligence', '/api/v1/runs/{run_id}/intelligence', [180, 760, 1990], 9876),
  ],
}

const SETTINGS = { index_batch_size: 500, incremental_limit: 5000, query_timeout_ms: 1500, max_results: 100, pg_pool_size: 20, pg_max_overflow: 10, pg_pool_recycle: 1800, celery_worker_concurrency: 4 }

const HANDLERS = [
  ...LAYOUT,
  ['/api/v1/performance/budgets', () => ({ latency_budgets: LATENCY, throughput_budgets: [], scale_scenarios: [] })],
  ['/api/v1/performance/search-config', () => SETTINGS],
  ['/api/v1/performance/observed', () => OBSERVED],
] as const

const table = (page: Page) => page.getByTestId('latency-budgets')

for (const width of [1280, 1440]) {
  test(`at ${width} px the latency budgets fit the settings column`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/settings/performance', {
      handlers: HANDLERS,
      ready: (p) => p.locator('[data-operation="search_reindex_full"]'),
    })
    await networkQuiet(page, api)
    await page.evaluate(() => document.fonts.ready)

    // Not vacuous: every budget is a row, the measured ones show their numbers, the rest say why they have none.
    await expect(table(page).locator('tbody tr')).toHaveCount(LATENCY.length)
    await expect(page.locator('[data-operation="run_list"]')).toContainText('123,456')
    await expect(page.locator('[data-operation="run_list"] [data-breach="true"]')).toHaveText('310 ms')
    await expect(page.locator('[data-operation="flaky_coach"]')).toContainText('No requests')
    await expect(page.locator('[data-operation="search_reindex_full"]')).toContainText('120000 ms')
    await expect(page.locator('[data-operation="search_reindex_full"]')).toContainText('Not an HTTP route')

    // The description is under its operation, in the operation's cell: there is no Description column.
    await expect(table(page).locator('thead th')).toHaveText(['Operation', 'Budget p95', 'Observed p50', 'Observed p95', 'Observed p99', 'Requests'])
    const description = page.locator('[data-operation="execution_suite_history"] td').first().getByTestId('budget-description')
    await expect(description).toHaveText(LATENCY[3].description)

    // Against the settings column the table sits in (the nearest box that scrolls is `#main-content`, wider).
    const { tableWidth, column } = await table(page).evaluate((el: HTMLElement) => ({
      tableWidth: Math.round(el.getBoundingClientRect().width),
      column: (el.parentElement as HTMLElement).clientWidth,
    }))
    const slack = await minContentSlack(table(page))
    inPageLine('/settings/performance latency budgets', width, { table: tableWidth, column, slack })
    expect(tableWidth, 'the table is no wider than its column').toBeLessThanOrEqual(column)
    await expectNoHorizontalOverflow(page, `/settings/performance at ${width} px`)
    assertHermetic(api, errors)
  })
}
