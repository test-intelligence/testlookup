/**
 * D-0001 (GPT-6 exploratory, E2E-0001) on `/overview`, hermetic: a project whose newest run is 36 days old,
 * viewed at 14 days, gets the Dashboard with the empty-window banner ("No runs in the last 14 days … 36 days ago",
 * "Show last 90 days"), never the first-run setup guide. Both existence probes ask ALL TIME (`days=0`); an
 * omitted `days` is the server's 30-day window, which is how a project quiet for a month read as never having run.
 * "Show last 90 days" then fills the KPIs. Control: a project with no runs at all still gets the first-run guide.
 *
 * The `/runs` handler here is server-faithful (omitted `days` = 30, `0` = all time, `N` = the last N days). The
 * shared `runListOf` now treats an omitted `days` the same way (P11); before, it read it as all time, which hid
 * exactly this regression.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock `NOW` = 2026-09-18T12:00Z). Fixtures: `OVERVIEW_ON`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import type { ApiHandlers, ApiRequest } from '../lib/production-pages'
import { NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const DAY_MS = 86_400_000
const OLD_RUN_AT = new Date(NOW.getTime() - 36 * DAY_MS - 3 * 3_600_000).toISOString()

const OLD_RUN = {
  id: '44444444-4444-4444-8444-0000000036d0',
  project_id: PROJECT_ID,
  project_name: 'Checkout',
  build_number: '118',
  jenkins_job: 'checkout-ci',
  branch: 'main',
  status: 'PASSED',
  passed_tests: 180,
  failed_tests: 0,
  skipped_tests: 2,
  broken_tests: 0,
  unknown_tests: 0,
  total_tests: 182,
  pass_rate: 100,
  duration_ms: 420_000,
  start_time: OLD_RUN_AT,
  end_time: OLD_RUN_AT,
  created_at: OLD_RUN_AT,
  trigger_source: 'ci',
  ingestion_source: 'sdk',
  primary_suite_name: 'Auth',
  suite_names: ['Auth'],
  run_seq: 1,
}

/** `GET /runs` as `list_project_runs` answers it: omitted `days` = 30, `0` = all time. */
function serverRuns(source: typeof OLD_RUN[]) {
  return ({ url }: ApiRequest) => {
    const raw = url.searchParams.get('days')
    const days = raw === null ? 30 : Number(raw)
    const since = NOW.getTime() - days * DAY_MS
    const all = source.filter((run) => days === 0 || Date.parse(run.created_at) >= since)
    const size = Number(url.searchParams.get('size') ?? 100)
    const items = all.slice(0, size)
    return { items, total: all.length, page: 1, size, pages: all.length > 0 ? Math.ceil(all.length / size) : 0 }
  }
}

/** The window holds the run only from 37 days up: zero executions below that. */
const windowDays = (url: URL) => Number(url.searchParams.get('days') ?? 30)
const runInWindow = (url: URL, source: typeof OLD_RUN[]) => source.length > 0 && windowDays(url) >= 37

function summary(source: typeof OLD_RUN[]) {
  return ({ url }: ApiRequest) => {
    const has = runInWindow(url, source)
    return {
      release_readiness: 'GREEN',
      release_readiness_band: 'green',
      release_readiness_downgrades: [],
      total_executions_7d: { value: has ? 182 : 0, trend: 0, trend_direction: 'flat' },
      avg_pass_rate_7d: { value: has ? 100 : 0, trend: 0, trend_direction: 'flat', pass_rate_delta_pp: 0, basis: 'executions', basis_label: 'per test execution' },
      active_defects: { value: 0, trend: 0, trend_direction: 'flat' },
      flaky_test_count: { value: 0, trend: 0, trend_direction: 'flat' },
      new_failures_24h: { value: 0, trend: 0, trend_direction: 'flat' },
      avg_duration_ms: { value: has ? 420_000 : 0 },
    }
  }
}

function trends(source: typeof OLD_RUN[]) {
  return ({ url }: ApiRequest) => ({
    data: runInWindow(url, source)
      ? [{ date: OLD_RUN_AT.slice(0, 10), passed: 180, failed: 0, skipped: 2, broken: 0, total: 182, pass_rate: 100 }]
      : [],
    period_days: windowDays(url),
  })
}

function world(source: typeof OLD_RUN[], runs: (request: ApiRequest) => unknown = serverRuns(source)): ApiHandlers {
  return [
    ['/api/v1/runs', runs],
    ['/api/v1/metrics/summary', summary(source)],
    ['/api/v1/metrics/trends', trends(source)],
    // The first-run guide's CLI step (`useCliWheel`): no wheel served here.
    ['/api/v1/sdk/cli/info', () => ({ available: false, version: null, filename: null, download_url: null })],
    ...OVERVIEW_ON,
  ]
}

const heading = (page: Page) => page.getByRole('heading', { level: 1, name: 'Dashboard' })

test('a project whose newest run is 36 days old gets the Dashboard and the run\'s age, never the first-run guide', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/overview', {
    handlers: world([OLD_RUN]),
    days: 14,
    ready: (p) => p.getByTestId('overview-empty-window'),
  })
  const banner = page.getByTestId('overview-empty-window')
  await expect(banner).toContainText('No runs in the last 14 days')
  await expect(banner).toContainText('36 days ago')
  await expect(banner.getByRole('button', { name: 'Show last 90 days' })).toBeVisible()
  await expect(page.getByText(/Welcome to TestLookup/)).toHaveCount(0)
  await expect(page.getByText(/No test runs here yet/)).toHaveCount(0)

  await networkQuiet(page, api)
  // Both existence probes asked all time.
  const probes = requestsTo(api, '/api/v1/runs').filter(
    (line) => new URL(line.slice(line.indexOf(' ') + 1), 'http://overview.test').searchParams.get('size') === '1',
  )
  expect(probes.length).toBeGreaterThan(0)
  expect(probes.every((line) => line.includes('days=0'))).toBe(true)

  // The way out works: the 90-day window holds the run.
  await banner.getByRole('button', { name: 'Show last 90 days' }).click()
  await expect(page.getByTestId('overview-empty-window')).toHaveCount(0)
  await expect(heading(page)).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a project with no runs at all still gets the first-run guide', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/overview', {
    handlers: world([]),
    days: 14,
    ready: (p) => p.getByText(/Welcome to TestLookup/),
  })
  await expect(page.getByText(/Welcome to TestLookup — Checkout/)).toBeVisible()
  await expect(page.getByText(/No test runs here yet/)).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
