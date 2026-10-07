/**
 * /reports/summary with the catalogue (Wave 2.6, VIZ-408; plan 2.2
 * "Summary report", 5.3).
 *
 * UX redesign P3 (`02-design-spec.md` §5): the primary content is the
 * results-by-suite bars (drawn from the report the page already holds, so
 * they follow its Aggregation toggle) with the per-suite table under them.
 * The pass-rate trend — the one chart with a request of its own,
 * `/metrics/trends` — is in a collapsed "Trend" disclosure: not rendered at
 * load, so not asked; opened, it asks once it is near (its lazy section).
 * Deleted: the status donut (its four counts are the KPI row's, from the same
 * totals) and "Failures by test" (a duplicate of the Top failing tests table
 * under it, which lists every one of its rows).
 *
 * Phase D S2: the page no longer asks `viz_chart_data_api` (migration 0195
 * turned it on everywhere and the flag-off path is deleted), so every load
 * here runs with every flag OFF in the harness and the inventory has no seam
 * lookup (`SHELL_BASE`).
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { cardByHeading } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectInventory,
  expectNoErrorFrame,
  expectNoTextEscapes,
  networkQuiet,
  openRollout,
  proveLazyMount,
  requestsTo,
  section,
  sectionFrame,
  SHELL_BASE,
  SHORT_VIEWPORT,
} from '../lib/rollout'
import { HOSTILE_NAME, PROJECT_ID, SUMMARY_REPORT_ON } from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => p.getByText('Total tests', { exact: true })

/** The section at load (the primary's), and the trend once "Trend" is opened. */
const SUITES: [string, string] = ['summary-suites', 'Results by suite']
const TREND: [string, string] = ['summary-trend', 'Pass rate trend']
const TRENDS_PATH = '/api/v1/metrics/trends'

/** The shell and the report's own reads; the trend is asked only once "Trend" is opened (`trendRead`). */
const inventoryOn = (days: number, mode = 'latest') => [
  ...SHELL_BASE,
  `GET /api/v1/reports/summary?project_id=${P}&days=${days}&mode=${mode}`,
  // P1: the header's Views menu.
  `GET /api/v1/saved-views?project_id=${P}&page=summary_report`,
  // VIZ-607: the reader's background exports.
  `GET /api/v1/reports/summary/exports?project_id=${P}`,
]
/** The trend's one read; its markers reuse the cached release list. */
const trendRead = (days: number) => `GET ${TRENDS_PATH}?project_id=${P}&days=${days}`

/** The "Trend" disclosure's toggle. */
const trendToggle = (page: Page) => page.getByRole('button', { name: /^Trend/ })

/** A suite's cells (passed, failed, …) in the suite bars' data table. */
function suiteCells(frame: Locator, suite: string): Locator {
  return frame
    .getByRole('table', { name: /data table/i })
    .locator('tr', { has: frame.page().getByRole('rowheader', { name: suite, exact: true }) })
    .locator('td')
}

test.describe('Summary report, everything on screen (1280 x 2400)', () => {
  test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the suite bars draw beside the page’s tables; the trend is collapsed and asks only once opened', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      ready,
    })
    const [id, title] = SUITES
    await expect(section(page, id), id).toHaveCount(1)
    await expect(section(page, id).getByRole('heading', { level: 2, name: title, exact: true })).toBeVisible()
    await expectDrawn(sectionFrame(page, id, title), id)
    await expect(page.locator('[data-primary]').locator(`[data-catalogue-section="${id}"]`)).toHaveCount(1)
    // P3: no donut (the KPI row carries its counts: the tiles' 186 tests), no failing-test chart.
    await expect(section(page, 'summary-donut')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Status breakdown' })).toHaveCount(0)
    await expect(page.getByRole('region', { name: 'Summary KPIs' })).toContainText('186')
    await expect(section(page, 'summary-top-failing')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Failures by test' })).toHaveCount(0)
    await expect(page.locator('[data-catalogue-section]')).toHaveCount(1)
    // The tables stay; the failing-test chart's twelve rows are the table's.
    await expect(cardByHeading(page, /^Per-suite breakdown/).locator('tbody tr')).toHaveCount(6)
    await expect(cardByHeading(page, /^Top failing tests/).locator('tbody tr')).toHaveCount(12)
    await expect(page.locator('[data-catalogue-section] img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expect(cardByHeading(page, /^Top failing tests/).getByText(HOSTILE_NAME, { exact: true })).toBeVisible()
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Summary at 1280')

    // The trend is collapsed: not rendered (not even its placeholder), not asked.
    await expect(trendToggle(page)).toHaveAttribute('aria-expanded', 'false')
    await expect(section(page, TREND[0])).toHaveCount(0)
    await expect(page.locator('[data-lazy-section]')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, TRENDS_PATH), 'no trend read while it is collapsed').toEqual([])
    expectInventory(api, errors, inventoryOn(30), 'Summary, every flag off')

    // Opened: the trend mounts, asks once, and draws, naming its population.
    await trendToggle(page).click()
    await expect(section(page, TREND[0]).getByRole('heading', { level: 2, name: TREND[1], exact: true })).toBeVisible()
    await expectDrawn(sectionFrame(page, ...TREND), 'trend')
    await expect(sectionFrame(page, ...TREND).locator('[data-chart-takeaway]')).toContainText(
      'percent of test executions, not of unique tests',
    )
    await networkQuiet(page, api)
    expect(requestsTo(api, TRENDS_PATH)).toEqual([trendRead(30)])
    expectInventory(api, errors, [...inventoryOn(30), trendRead(30)], 'Summary, Trend opened')
  })

  test('the suite bars and the KPI row follow the Aggregation toggle (the report’s own counts)', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      ready,
    })
    const bars = sectionFrame(page, ...SUITES)
    /** The bars' data table, opened (the frame remounts while the other mode's report loads). */
    const openTable = async () => {
      await expectDrawn(bars, 'suites')
      if (!(await bars.getByRole('table', { name: /data table/i }).isVisible())) {
        await bars.getByRole('button', { name: 'View as table' }).click()
      }
    }
    await openTable()
    // Payments, latest run: 20 passed, 11 failed. The KPI row: the tiles' 186 tests.
    await expect(suiteCells(bars, 'Payments').nth(0)).toHaveText('20')
    await expect(suiteCells(bars, 'Payments').nth(1)).toHaveText('11')
    const kpis = page.getByRole('region', { name: 'Summary KPIs' })
    await expect(kpis).toContainText('186')
    await page.getByRole('radio', { name: 'All runs in window' }).click()
    await expect(page.getByRole('radio', { name: 'All runs in window' })).toHaveAttribute('aria-checked', 'true')
    await expect(kpis).toContainText('532')
    await expect(kpis).not.toContainText('186')
    // Window counts of the fixture: every suite's latest counts x its runs (2 + i % 3); Payments is i = 2.
    await openTable()
    await expect(suiteCells(bars, 'Payments').nth(0)).toHaveText('80')
    await expect(suiteCells(bars, 'Payments').nth(1)).toHaveText('44')
    await networkQuiet(page, api)
    // The toggle asks for the report again; the collapsed trend asks nothing.
    expect(requestsTo(api, TRENDS_PATH)).toEqual([])
    expectInventory(
      api,
      errors,
      [...inventoryOn(30), `GET /api/v1/reports/summary?project_id=${P}&days=30&mode=window`],
      'Summary, toggled to the window',
    )
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // catalogue sections' subtree only (the page's own pre-existing findings are
  // not this wave's; see plan 5.5 on the lab muted token), in both themes the
  // harness renders — with "Trend" opened, so both sections are checked. No
  // allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/reports/summary', {
        handlers: SUMMARY_REPORT_ON,
        ready,
        theme,
      })
      await trendToggle(page).click()
      for (const [id, title] of [SUITES, TREND]) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      ready,
      days: 365,
    })
    await trendToggle(page).click()
    await expectDrawn(sectionFrame(page, ...TREND), 'trend')
    await networkQuiet(page, api)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
    expectInventory(api, errors, [...inventoryOn(90), trendRead(90)], 'Summary at a stored 365 days')
  })
})

/**
 * The trend's own lazy section still holds inside the disclosure: opened far
 * below a short screen, it asks only once it is near. The toggle is pressed
 * without scrolling to it (`dispatchEvent`), so the opened placeholder starts
 * well past the 200 px margin; a real click scrolls the toggle into view and
 * the trend mounts at once (which is what a click is for, and proves nothing
 * about the lazy section).
 */
const SHORTER_VIEWPORT = { ...SHORT_VIEWPORT, height: 480 } as const

test.describe('Summary report, a short screen (1280 x 480)', () => {
  test.use({ viewport: { ...SHORTER_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: opened, the trend asks nothing until it is near, and asks before it is visible', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      ready,
    })
    // The suite bars are drawn from the report: not lazy.
    await expectDrawn(sectionFrame(page, ...SUITES), 'suites')
    await networkQuiet(page, api)
    expect(requestsTo(api, TRENDS_PATH), 'collapsed: no trend read').toEqual([])
    await trendToggle(page).dispatchEvent('click')
    await expect(trendToggle(page)).toHaveAttribute('aria-expanded', 'true')
    await proveLazyMount(page, api, {
      label: 'summary-trend',
      section: 'summary-trend',
      asked: () => requestsTo(api, TRENDS_PATH).length,
      before: 0,
    })
    await expectDrawn(sectionFrame(page, ...TREND), 'trend')
    await networkQuiet(page, api)
    expectInventory(api, errors, [...inventoryOn(30), trendRead(30)], 'Summary, the trend opened and scrolled to')
  })
})
