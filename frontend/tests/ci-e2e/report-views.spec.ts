/**
 * P1 (2026-10-04): the report pages' own "Views" button, now that the report
 * chrome that hosted it is gone. Flags off (the shipped state): a view saves
 * and applies the legacy scope — the top-bar release, the window and the
 * page's suite filter — plus the page's extras (Summary's aggregation mode).
 *
 * Hermetic (`tests/lib/production-pages.ts`): every request is answered by a
 * fixture or fails the test by name. The saved views live in this spec's own
 * store, so a save is read back by the next GET.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, landmark, type ApiHandlers } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, RELEASE_ID, RELEASES, SUMMARY_REPORT, TRENDS, USER } from '../visual/production/fixtures'

const P = PROJECT_ID
const R = RELEASE_ID.current // 2026.09
const R_OTHER = RELEASE_ID.august // 2026.08
/** A suite of the fixture's runs (`RUN_SPECS`), so the Trends select offers it. */
const SUITE_IN_FIXTURE = 'Payments'

test.use({ viewport: { width: 1280, height: 1600 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface Row {
  id: string
  user_id: string
  project_id: string
  name: string
  description: null
  page: string
  filters: Record<string, unknown>
  is_shared: boolean
  is_default: boolean
  created_at: string
  updated_at: null
}

/** The saved-views API over an in-test list: GET by page, POST appends (and records the body). */
function savedViews(rows: Row[] = []) {
  const posted: Record<string, unknown>[] = []
  const handlers: ApiHandlers = [
    ['/api/v1/saved-views', ({ url }) => rows.filter((row) => row.page === url.searchParams.get('page'))],
    [
      '/api/v1/saved-views',
      ({ route }) => {
        const body = route.request().postDataJSON() as Record<string, unknown>
        posted.push(body)
        const row: Row = {
          id: `view-${rows.length + 1}`,
          user_id: USER.id,
          project_id: String(body.project_id),
          name: String(body.name),
          description: null,
          page: String(body.page),
          filters: body.filters as Record<string, unknown>,
          is_shared: body.is_shared === true,
          is_default: body.is_default === true,
          created_at: '2026-10-01T00:00:00Z',
          updated_at: null,
        }
        rows.push(row)
        return row
      },
      'POST',
    ],
  ]
  return { handlers, posted }
}

const releaseList: ApiHandlers = [
  ['/api/v1/releases', () => ({ items: RELEASES, total: RELEASES.length, page: 1, size: 100, pages: 1 })],
]

const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const releaseSelect = (p: Page) => p.getByRole('combobox', { name: 'Filter by release' })
const suiteSelect = (p: Page) => p.getByRole('combobox', { name: 'Test suite' })
const windowTab = (p: Page, label: string) => p.getByRole('tablist', { name: 'Time window' }).getByRole('tab', { name: label, exact: true })
const viewsButton = (p: Page) => p.locator('[data-saved-views-trigger]')

/** The first suite the Trends suite select offers (the fixture's own spelling). */
async function firstSuite(page: Page): Promise<string> {
  const values = await suiteSelect(page).locator('option').evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value))
  const suite = values.find((value) => value !== '')
  expect(suite, 'the Trends fixture offers a suite').toBeTruthy()
  return suite as string
}

/** Every Trends read since `from` that carries the release, the suite and the window. */
function scopedTrendReads(lines: string[], suite: string) {
  return lines.filter((line) => {
    if (!line.startsWith('GET /api/v1/metrics/trends?')) return false
    const query = new URLSearchParams(line.split('?')[1])
    return query.get('release_id') === R && query.get('suite_name') === suite && query.get('days') === '30'
  })
}

test('Trends: Views saves the top-bar release, the window and the suite, and opening it sets all three', async ({ page }) => {
  const store = savedViews()
  const { api, errors } = await openRollout(page, '/trends', { handlers: [...store.handlers, ...releaseList, ...TRENDS], ready: trendsReady })
  await expect(viewsButton(page)).toHaveText('Views')
  const suite = await firstSuite(page)

  // The scope, through the page's own controls.
  await releaseSelect(page).selectOption(R)
  await windowTab(page, '30d').click()
  await suiteSelect(page).selectOption(suite)
  await expect(windowTab(page, '30d')).toHaveAttribute('aria-selected', 'true')

  await viewsButton(page).click()
  const panel = page.locator('[data-saved-views-panel]')
  await expect(panel.locator('[data-saved-views-empty]')).toBeVisible()
  await panel.locator('[data-saved-view-name]').fill('Release watch')
  await panel.locator('[data-saved-view-save]').click()
  await expect(panel.getByText('Release watch', { exact: true })).toBeVisible()
  expect(store.posted).toHaveLength(1)
  expect(store.posted[0]).toEqual({
    project_id: P,
    name: 'Release watch',
    page: 'trends',
    filters: { kind: 'report_view', page: 'trends', window: 30, release_ids: [R], release_id: R, suites: [suite] },
    is_shared: false,
    is_default: false,
  })
  await page.keyboard.press('Escape')

  // Back to no scope, then open the view.
  await releaseSelect(page).selectOption({ label: 'All releases' })
  await windowTab(page, '7d').click()
  await suiteSelect(page).selectOption('')
  await expect(releaseSelect(page)).not.toHaveValue(R)
  await networkQuiet(page, api)
  const before = api.seen.length

  await viewsButton(page).click()
  await panel.locator('[data-saved-view-open]').filter({ hasText: 'Release watch' }).click()
  await expect(releaseSelect(page)).toHaveValue(R)
  await expect(windowTab(page, '30d')).toHaveAttribute('aria-selected', 'true')
  await expect(suiteSelect(page)).toHaveValue(suite)
  await expect(page).toHaveURL(new RegExp(`[?&]release=${R}`))
  await networkQuiet(page, api)
  // The reads that follow carry all three filters.
  expect(scopedTrendReads(api.seen.slice(before), suite).length, 'a trends read with the view\'s scope').toBeGreaterThan(0)
  assertHermetic(api, errors)
})

test('Summary: the aggregation mode round-trips through a view, beside the window', async ({ page }) => {
  const store = savedViews()
  const { api, errors } = await openRollout(page, '/reports/summary', {
    handlers: [...store.handlers, ...releaseList, ...SUMMARY_REPORT],
    ready: (p) => p.getByText('Total tests', { exact: true }),
  })
  const windowMode = page.getByRole('radio', { name: 'All runs in window' })
  const latestMode = page.getByRole('radio', { name: 'Latest run per suite' })
  await windowMode.click()
  await expect(windowMode).toHaveAttribute('aria-checked', 'true')

  await viewsButton(page).click()
  const panel = page.locator('[data-saved-views-panel]')
  await panel.locator('[data-saved-view-name]').fill('Window totals')
  await panel.locator('[data-saved-view-save]').click()
  await expect(panel.getByText('Window totals', { exact: true })).toBeVisible()
  expect(store.posted[0]).toMatchObject({
    page: 'summary_report',
    filters: { kind: 'report_view', page: 'summary_report', window: 30, summary: { mode: 'window' } },
  })
  await page.keyboard.press('Escape')

  await latestMode.click()
  await expect(latestMode).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  const before = api.seen.length
  await viewsButton(page).click()
  await panel.locator('[data-saved-view-open]').filter({ hasText: 'Window totals' }).click()
  await expect(windowMode).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  // Asked again for the window totals (the report was cached by SWR, or asked anew).
  expect(requestsTo(api, '/api/v1/reports/summary').some((line) => line.includes('mode=window'))).toBe(true)
  expect(api.seen.length).toBeGreaterThanOrEqual(before)
  assertHermetic(api, errors)
})

test.describe('my default view', () => {
  const defaultRow = (suite: string): Row => ({
    id: 'view-default',
    user_id: USER.id,
    project_id: P,
    name: 'My default',
    description: null,
    page: 'trends',
    filters: { kind: 'report_view', page: 'trends', window: 30, release_ids: [R], release_id: R, suites: [suite] },
    is_shared: false,
    is_default: true,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: null,
  })

  test('opens on the first visit', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/trends', {
      handlers: [...savedViews([defaultRow(SUITE_IN_FIXTURE)]).handlers, ...releaseList, ...TRENDS],
      ready: trendsReady,
    })
    await expect(releaseSelect(page)).toHaveValue(R)
    await expect(windowTab(page, '30d')).toHaveAttribute('aria-selected', 'true')
    await expect(suiteSelect(page)).toHaveValue(SUITE_IN_FIXTURE)
    await expect(page.getByText(/Opened your default view "My default"/)).toBeVisible()
    await networkQuiet(page, api)
    expect(scopedTrendReads(api.seen, SUITE_IN_FIXTURE).length).toBeGreaterThan(0)
    expect(requestsTo(api, '/api/v1/saved-views')).toEqual([`GET /api/v1/saved-views?project_id=${P}&page=trends`])
    assertHermetic(api, errors)
  })

  test('does not open over a release in the URL (a shared link wins)', async ({ page }) => {
    const { api, errors } = await openRollout(page, `/trends?release=${R_OTHER}`, {
      handlers: [...savedViews([defaultRow(SUITE_IN_FIXTURE)]).handlers, ...releaseList, ...TRENDS],
      ready: trendsReady,
    })
    await expect(viewsButton(page)).toBeVisible()
    await networkQuiet(page, api)
    expect(requestsTo(api, '/api/v1/saved-views')).toEqual([`GET /api/v1/saved-views?project_id=${P}&page=trends`])
    await expect(releaseSelect(page)).toHaveValue(R_OTHER)
    await expect(windowTab(page, '14d')).toHaveAttribute('aria-selected', 'true')
    await expect(suiteSelect(page)).toHaveValue('')
    await expect(page.getByText(/Opened your default view/)).toHaveCount(0)
    assertHermetic(api, errors)
  })
})
