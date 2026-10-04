/**
 * /reports/summary with the catalogue ON (Wave 2.6, VIZ-408; plan 2.2
 * "Summary report", 5.3). Four charts: a status donut and results-by-suite
 * bars (both from the report the page already holds, so they follow its
 * Aggregation toggle), a pass-rate trend (the one new request,
 * `/metrics/trends`, lazy), and failures by test above the existing table.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
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
  SHELL_ON,
  SHORT_VIEWPORT,
} from '../lib/rollout'
import { CATALOGUE_ON, HOSTILE_NAME, PROJECT_ID, SUMMARY_REPORT_ON } from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => p.getByText('Total tests', { exact: true })

const SECTIONS: [string, string][] = [
  ['summary-donut', 'Status breakdown'],
  ['summary-suites', 'Results by suite'],
  ['summary-trend', 'Pass rate trend'],
  ['summary-top-failing', 'Failures by test'],
]

/** The flag-off list (the report), plus the seam's lookup and the trend; the markers reuse the cached release list. */
const inventoryOn = (days: number, mode = 'latest') => [
  ...SHELL_ON,
  `GET /api/v1/reports/summary?project_id=${P}&days=${days}&mode=${mode}`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=${days}`,
  // VIZ-607: the reader's background exports.
  `GET /api/v1/reports/summary/exports?project_id=${P}`,
]

test.describe('Summary report, catalogue on, everything on screen (1280 x 2400)', () => {
  test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the four sections render with their headings and draw, beside the page’s own tables', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      flags: CATALOGUE_ON,
      ready,
    })
    for (const [id, title] of SECTIONS) {
      await expect(section(page, id), id).toHaveCount(1)
      await expect(section(page, id).getByRole('heading', { level: 2, name: title, exact: true })).toBeVisible()
      await expectDrawn(sectionFrame(page, id, title), id)
    }
    // The tables stay, and "Top failing tests" is still one heading (the chart is "Failures by test").
    await expect(cardByHeading(page, /^Per-suite breakdown/).locator('tbody tr')).toHaveCount(6)
    await expect(cardByHeading(page, /^Top failing tests/).locator('tbody tr')).toHaveCount(12)
    // The trend names its population: executions, not the tiles' unique tests.
    await expect(sectionFrame(page, 'summary-trend', 'Pass rate trend').locator('[data-chart-takeaway]')).toContainText(
      'percent of test executions, not of unique tests',
    )
    // Donut, in the page's default aggregation: the tiles' 186 tests.
    await expect(section(page, 'summary-donut')).toContainText('186')
    await expect(page.locator('[data-catalogue-section] img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expect(cardByHeading(page, /^Top failing tests/).getByText(HOSTILE_NAME, { exact: true })).toBeVisible()
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Summary at 1280')
    await networkQuiet(page, api)
    expectInventory(api, errors, inventoryOn(30), 'Summary, catalogue on')
  })

  test('the donut follows the Aggregation toggle (the report’s own totals)', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      flags: CATALOGUE_ON,
      ready,
    })
    const donut = sectionFrame(page, 'summary-donut', 'Status breakdown')
    await expectDrawn(donut, 'donut')
    await expect(donut).toContainText('186')
    await page.getByRole('radio', { name: 'All runs in window' }).click()
    await expect(page.getByRole('radio', { name: 'All runs in window' })).toHaveAttribute('aria-checked', 'true')
    // Window totals of the fixture: every suite's latest counts x its runs (2 + i % 3) = 532 tests.
    await expect(donut).toContainText('532')
    await expect(donut).not.toContainText('186')
    await networkQuiet(page, api)
    // The toggle asks for the report again; the trend does not follow it (its caption says so).
    expect(requestsTo(api, '/api/v1/metrics/trends')).toHaveLength(1)
    expectInventory(
      api,
      errors,
      [...inventoryOn(30), `GET /api/v1/reports/summary?project_id=${P}&days=30&mode=window`],
      'Summary, toggled to the window',
    )
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // new sections' subtree only (the page's own pre-existing findings are not
  // this wave's; see plan 5.5 on the lab muted token), in both themes the
  // harness renders. No allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/reports/summary', {
        handlers: SUMMARY_REPORT_ON,
        flags: CATALOGUE_ON,
        ready,
        theme,
      })
      for (const [id, title] of SECTIONS) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      flags: CATALOGUE_ON,
      ready,
      days: 365,
    })
    await expectDrawn(sectionFrame(page, 'summary-trend', 'Pass rate trend'), 'trend')
    await networkQuiet(page, api)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
    expectInventory(api, errors, inventoryOn(90), 'Summary at a stored 365 days')
  })
})

test.describe('Summary report, catalogue on, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no trend request until the trend is near, and it is asked before it is visible', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/reports/summary', {
      handlers: SUMMARY_REPORT_ON,
      flags: CATALOGUE_ON,
      ready,
    })
    // The two charts drawn from the report are not lazy.
    await expectDrawn(sectionFrame(page, 'summary-donut', 'Status breakdown'), 'donut')
    await proveLazyMount(page, api, {
      label: 'summary-trend',
      section: 'summary-trend',
      asked: () => requestsTo(api, '/api/v1/metrics/trends').length,
      before: 0,
    })
    await expectDrawn(sectionFrame(page, 'summary-trend', 'Pass rate trend'), 'trend')
    await networkQuiet(page, api)
    expectInventory(api, errors, inventoryOn(30), 'Summary, scrolled to the trend')
  })
})
