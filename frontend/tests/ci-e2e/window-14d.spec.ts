/**
 * W07 WIN-01 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.1), hermetic: a 14-day window chosen on
 * one page stays 14 days on every other windowed page. No page shows or queries a neighbour (the "silent snap"
 * of a 14 to 7 the six pages below did before WIN-01, `snapToAllowed`).
 *
 *  (a) `/trends` opened with the stored window at 30: picking `14d` asks the trend for `days=14` and stores 14
 *      (`localStorage['testlookup-time-window']`).
 *  (b) Summary, Defects, My failures, Value metrics, Activity and the Audit dashboard opened with 14 stored: each
 *      shows 14d selected (a `14d` radio / pressed button, `Last 14 days` in the Audit select), asks its own API
 *      for 14 days (`days=14`; Activity sends `since` = now − 14 d), and after the network is quiet the stored
 *      window is still 14.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock: `NOW`). Fixtures: `TRENDS_ON`, `SUMMARY_REPORT_ON`,
 * `DEFECTS`, `inboxHandlers()`, `VALUE_METRICS` (`fixtures.ts`, `fixtures-pages.ts`) and `ACTIVITY_PAGE`,
 * `AUDIT_PAGE` (`fixtures-runs-failures.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { landmark, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { DEFECTS, NOW, PROJECT_ID, SUMMARY_REPORT_ON, TRENDS_ON, VALUE_METRICS } from '../visual/production/fixtures'
import { inboxHandlers } from '../visual/production/fixtures-pages'
import { ACTIVITY_PAGE, AUDIT_PAGE } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const DAY_MS = 86_400_000

/** The stored global window (`store/timeWindowStore.ts`, zustand `persist`). */
async function storedDays(page: Page): Promise<number | null> {
  return page.evaluate(() => {
    const raw = localStorage.getItem('testlookup-time-window')
    return raw ? ((JSON.parse(raw) as { state?: { days?: number } }).state?.days ?? null) : null
  })
}

const windowRadio = (page: Page, label: string): Locator =>
  page.getByRole('radiogroup', { name: 'Time window' }).first().getByRole('radio', { name: label, exact: true })

/** The `days` of every request to `path` (in order). */
const daysOf = (lines: string[]) => lines.map((line) => new URLSearchParams(line.split('?')[1] ?? '').get('days'))

/** A client-side navigation (the in-memory store is kept; a `goto` would re-seed the stored window). */
async function goInApp(page: Page, path: string) {
  await page.evaluate((to) => {
    window.history.pushState({}, '', to)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, path)
}

interface WindowedPage {
  name: string
  path: string
  handlers: () => ApiHandlers
  ready: (page: Page) => Locator
  /** The page's own control for 14 days, and how it says "selected". */
  selected: (page: Page) => Promise<void>
  /** The page's own API line(s) for the window. */
  api: string
  /** What the window looks like on that line. */
  windowOf: (query: URLSearchParams) => string | null
  expected: string
}

const RADIO_14 = async (page: Page) => {
  await expect(windowRadio(page, '14d')).toHaveAttribute('aria-checked', 'true')
  await expect(windowRadio(page, '7d')).toHaveAttribute('aria-checked', 'false')
}
const DAYS = (query: URLSearchParams) => query.get('days')

const PAGES: WindowedPage[] = [
  {
    name: 'Summary',
    path: '/reports/summary',
    handlers: () => SUMMARY_REPORT_ON,
    ready: (p) => p.getByText('Total tests', { exact: true }),
    selected: RADIO_14,
    api: '/api/v1/reports/summary',
    windowOf: DAYS,
    expected: '14',
  },
  {
    name: 'Defects',
    path: '/defects',
    handlers: () => DEFECTS,
    ready: (p) => landmark(p, 'Defect KPIs'),
    selected: RADIO_14,
    api: '/api/v1/analytics/defects/trend',
    windowOf: DAYS,
    expected: '14',
  },
  {
    name: 'My failures',
    path: '/my-failures',
    handlers: () => inboxHandlers(),
    ready: (p) => p.locator('[data-page-header] h1'),
    selected: RADIO_14,
    api: '/api/v1/me/assigned-failures',
    windowOf: DAYS,
    expected: '14',
  },
  {
    name: 'Value metrics',
    path: '/value-metrics',
    handlers: () => VALUE_METRICS,
    ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    selected: RADIO_14,
    api: '/api/v1/value-metrics',
    windowOf: DAYS,
    expected: '14',
  },
  {
    name: 'Activity',
    path: '/activity',
    handlers: () => ACTIVITY_PAGE,
    ready: (p) => p.locator('[data-page-header] h1'),
    selected: async (page) => {
      await expect(page.getByRole('button', { name: '14d', exact: true })).toHaveAttribute('aria-pressed', 'true')
      await expect(page.getByRole('button', { name: '7d', exact: true })).toHaveAttribute('aria-pressed', 'false')
    },
    api: `/api/v1/projects/${PROJECT_ID}/activity`,
    windowOf: (query) => query.get('since'),
    // `since` is resolved at fetch time from the pinned clock.
    expected: new Date(NOW.getTime() - 14 * DAY_MS).toISOString(),
  },
  {
    name: 'Audit dashboard',
    path: '/settings/audit',
    handlers: () => AUDIT_PAGE,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Audit Dashboard' }),
    selected: async (page) => {
      const select = page.locator('select').filter({ has: page.locator('option', { hasText: 'Last 14 days' }) })
      await expect(select).toHaveValue('14')
      await expect(select.locator('option')).toHaveText(['Last 7 days', 'Last 14 days', 'Last 30 days', 'Last 90 days'])
    },
    api: '/api/v1/audit-dashboard/events',
    windowOf: DAYS,
    expected: '14',
  },
]

/** Every answer the journey needs: Trends first (first match wins; the shared chrome is the same everywhere). */
const JOURNEY: () => ApiHandlers = () => [
  ...TRENDS_ON,
  ...SUMMARY_REPORT_ON,
  ...DEFECTS,
  ...inboxHandlers(),
  ...VALUE_METRICS,
  ...ACTIVITY_PAGE,
  ...AUDIT_PAGE,
]

test('(a) 14d picked on Trends stays 14d on Summary, Defects, My failures, Value metrics, Activity and Audit', async ({ page }) => {
  test.setTimeout(90_000)
  const { api, errors } = await openRollout(page, '/trends', {
    handlers: JOURNEY(),
    ready: (p) => landmark(p, 'Trend metrics'),
    days: 30,
  })
  // Trends lands on its own default (14, `TRENDS_DEFAULT_WINDOW`); pick 30d, then 14d, and read the wire each time.
  await networkQuiet(page, api)
  let mark = api.seen.length
  await windowRadio(page, '30d').click()
  await expect(windowRadio(page, '30d')).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  expect(daysOf(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/metrics/trends'))).toContain('30')
  mark = api.seen.length
  await windowRadio(page, '14d').click()
  await expect(windowRadio(page, '14d')).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  expect(daysOf(requestsTo({ ...api, seen: api.seen.slice(mark) }, '/api/v1/metrics/trends')), 'the trend, for 14 days').toContain('14')
  expect(await storedDays(page)).toBe(14)

  for (const target of PAGES) {
    mark = api.seen.length
    await goInApp(page, target.path)
    await expect(target.ready(page), `${target.name}: rendered`).toBeVisible({ timeout: 15_000 })
    await target.selected(page)
    await networkQuiet(page, api)
    const lines = requestsTo({ ...api, seen: api.seen.slice(mark) }, target.api)
    expect(lines.length, `${target.name}: its API was asked`).toBeGreaterThan(0)
    const windows = lines.map((line) => target.windowOf(new URLSearchParams(line.split('?')[1] ?? '')))
    expect(windows, `${target.name}: the wire carries 14 days, never a neighbour`).toEqual(windows.map(() => target.expected))
    expect(await storedDays(page), `${target.name}: the stored window is still 14`).toBe(14)
  }
  assertHermetic(api, errors)
})

for (const target of PAGES) {
  test(`(b) ${target.name} opens on the stored 14 days, asks for 14 and leaves the window at 14`, async ({ page }) => {
    const { api, errors } = await openRollout(page, target.path, { handlers: target.handlers(), ready: target.ready, days: 14 })
    await target.selected(page)
    await networkQuiet(page, api)
    const lines = requestsTo(api, target.api)
    expect(lines.length, `${target.name}: its API was asked`).toBeGreaterThan(0)
    const windows = lines.map((line) => target.windowOf(new URLSearchParams(line.split('?')[1] ?? '')))
    expect(windows, `${target.name}: every request for the window asks 14 days`).toEqual(windows.map(() => target.expected))
    expect(await storedDays(page), `${target.name}: the stored window is untouched`).toBe(14)
    await target.selected(page)
    assertHermetic(api, errors)
  })
}
