/**
 * W08 TRND-10 (contract §2.9), hermetic: chart customisations saved with a
 * view, and opened by a teammate in ANOTHER browser (a new context: empty
 * `localStorage`, so only the view can carry them).
 *
 *   - Trends › By suite › Compare › **Customise** → Metric **Failures** → Save
 *     view → the POSTed `filters.charts['trends.compare'].metric` is
 *     `failures`; a new context opens the view → Compare asks
 *     `metric=failures`;
 *   - the Explorer › **Customise** → Lines per panel **3** → every panel asks
 *     `top_n=3`; saved (`filters.explore.top`) and opened in a new context →
 *     the panels ask `top_n=3` again;
 *   - an uncustomised Trends view stores no `charts` key at all
 *     (`report-views.spec.ts` pins the exact filters, unedited).
 *
 * The saved views live in this spec's own store (`savedViewsStore`), shared
 * by both contexts. Harness: `tests/lib/rollout.ts` (fail closed).
 */
import { expect, test, type Browser, type Page } from '@playwright/test'
import { landmark, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, queryOf } from '../lib/rollout'
import { CHART_DATA_PATH, TRENDS_ON } from '../visual/production/fixtures'
import { compareCountChartData, EXPLORE, savedViewsStore, type SavedViewRow } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1440, height: 1800 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const VIEW = { timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' as const, viewport: { width: 1440, height: 1800 } }
const trendsReady = (p: Page) => landmark(p, 'Trend metrics')
const exploreReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Explorer' })
const viewsButton = (p: Page) => p.locator('[data-saved-views-trigger]')
const viewsPanel = (p: Page) => p.locator('[data-saved-views-panel]')
const compare = (p: Page) => p.locator('[data-catalogue-section="trends-compare"]')
const customisePanel = (p: Page) => p.locator('[data-chart-customise-panel]')

/** TRENDS_ON with Compare's count metrics answered (first match wins). */
function trendsHandlers(store: ApiHandlers): ApiHandlers {
  const shared = TRENDS_ON.find(([m]) => m === CHART_DATA_PATH)?.[1]
  if (!shared) throw new Error('TRENDS_ON answers chart-data')
  return [[CHART_DATA_PATH, compareCountChartData((request: ApiRequest) => shared(request))], ...store, ...TRENDS_ON]
}

async function saveView(page: Page, name: string) {
  await viewsButton(page).click()
  await viewsPanel(page).locator('[data-saved-view-name]').fill(name)
  await viewsPanel(page).locator('[data-saved-view-save]').click()
  await expect(viewsPanel(page).getByText(name, { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
}

async function openView(page: Page, name: string) {
  await viewsButton(page).click()
  await viewsPanel(page).locator('[data-saved-view-open]').filter({ hasText: name }).click()
}

async function teammate(browser: Browser) {
  const context = await browser.newContext(VIEW)
  return { context, page: await context.newPage() }
}

test('Trends: Compare customised to Failures is saved in the view, and a teammate\'s browser opens it with Failures', async ({ page, browser }) => {
  const rows: SavedViewRow[] = []
  const store = savedViewsStore(rows)
  const { api, errors } = await openRollout(page, '/trends?tab=by-suite', { handlers: trendsHandlers(store.handlers), ready: trendsReady })
  await expect(compare(page).locator('[data-chart-frame]')).toHaveAttribute('data-chart-state', /^(ready|truncated)$/, { timeout: 20_000 })
  await compare(page).locator('[data-chart-customise]').click()
  await expect(customisePanel(page)).toBeVisible()
  await customisePanel(page).locator('[data-customise="metric"]').selectOption('failures')
  await expect
    .poll(() => queryOf(api, CHART_DATA_PATH, (q) => q.get('metric') === 'failures' && q.getAll('suite_name').length > 0).length, {
      message: 'Compare asks the failures',
    })
    .toBeGreaterThan(0)
  await customisePanel(page).getByRole('button', { name: /Close/ }).click()

  await saveView(page, 'Failures compare')
  expect(store.posted).toHaveLength(1)
  const filters = store.posted[0].filters as { charts?: Record<string, { metric?: string; version?: number }> }
  expect(filters.charts?.['trends.compare']).toMatchObject({ version: 1, metric: 'failures' })
  // Only the customised chart is stored.
  expect(Object.keys(filters.charts ?? {})).toEqual(['trends.compare'])
  await networkQuiet(page, api)
  assertHermetic(api, errors)

  // A teammate, another browser: nothing in its localStorage, only the view.
  const other = await teammate(browser)
  const opened = await openRollout(other.page, '/trends?tab=by-suite', { handlers: trendsHandlers(store.handlers), ready: trendsReady })
  await networkQuiet(other.page, opened.api)
  expect(queryOf(opened.api, CHART_DATA_PATH, (q) => q.get('metric') === 'failures'), 'the default Compare before the view').toEqual([])
  await openView(other.page, 'Failures compare')
  await expect
    .poll(() => queryOf(opened.api, CHART_DATA_PATH, (q) => q.get('metric') === 'failures' && q.getAll('suite_name').length > 0).length, {
      message: 'the view\'s Compare asks the failures',
    })
    .toBeGreaterThan(0)
  await expect(compare(other.page).locator('[data-chart-frame]')).toHaveAttribute('data-chart-state', /^(ready|truncated)$/)
  await networkQuiet(other.page, opened.api)
  assertHermetic(opened.api, opened.errors)
  await other.context.close()
})

test('Trends: a view saved with nothing customised stores no charts key', async ({ page }) => {
  const store = savedViewsStore()
  const { api, errors } = await openRollout(page, '/trends?tab=by-suite', { handlers: trendsHandlers(store.handlers), ready: trendsReady })
  await saveView(page, 'Plain view')
  expect(store.posted).toHaveLength(1)
  expect(store.posted[0].filters).not.toHaveProperty('charts')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Explorer: Lines per panel 3 asks top_n=3, is saved in the view, and a teammate\'s browser opens it the same way', async ({ page, browser }) => {
  const rows: SavedViewRow[] = []
  const store = savedViewsStore(rows)
  const { api, errors } = await openRollout(page, '/explore', { handlers: [...store.handlers, ...EXPLORE], ready: exploreReady })
  await expect(page.locator('[data-explore-panel]').first()).toBeVisible()
  await networkQuiet(page, api)
  const panels = await page.locator('[data-explore-panel]').count()
  await page.getByRole('button', { name: 'Customise', exact: true }).click()
  await expect(customisePanel(page)).toBeVisible()
  await customisePanel(page).locator('[data-customise="topN"]').selectOption('3')
  await expect(page).toHaveURL(/[?&]top=3(&|$)/)
  await expect
    .poll(() => queryOf(api, CHART_DATA_PATH, (q) => q.get('top_n') === '3').length, { message: 'every panel asks top_n=3' })
    .toBe(panels)
  await customisePanel(page).getByRole('button', { name: /Close/ }).click()

  await saveView(page, 'Three lines')
  expect((store.posted[0].filters as { explore?: Record<string, unknown> }).explore).toMatchObject({ top: 3 })
  await networkQuiet(page, api)
  assertHermetic(api, errors)

  const other = await teammate(browser)
  const opened = await openRollout(other.page, '/explore', { handlers: [...store.handlers, ...EXPLORE], ready: exploreReady })
  await expect(other.page.locator('[data-explore-panel]').first()).toBeVisible()
  await networkQuiet(other.page, opened.api)
  expect(queryOf(opened.api, CHART_DATA_PATH, (q) => q.has('top_n')), 'no cap before the view').toEqual([])
  await openView(other.page, 'Three lines')
  await expect(other.page).toHaveURL(/[?&]top=3(&|$)/)
  await expect.poll(() => queryOf(opened.api, CHART_DATA_PATH, (q) => q.get('top_n') === '3').length).toBe(panels)
  await networkQuiet(other.page, opened.api)
  assertHermetic(opened.api, opened.errors)
  await other.context.close()
})
