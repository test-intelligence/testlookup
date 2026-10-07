/**
 * Reports › Value's fold budget (UX redesign P3, design spec §2 and §5): at
 * 1440 x 900 the primary content (`[data-primary]`: Hours saved per month)
 * starts at most 300 px below the top of `#main-content` — measured from the
 * scroller's own top, so its padding and the Reports section tabs above the
 * page count. Above it: the header, the one-line hours-saved hero and one
 * strip of five counters. Model breakdown and the assumptions sit in
 * disclosures under it.
 *
 * `#main-content`'s scrollHeight is recorded (an annotation and the log):
 * 1359 px before P3 on this fixture, with the chart's top at 344 px.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { frameByHeading, openProductionPage, waitForCharts } from '../lib/production-pages'
import { MAIN, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, USER, VALUE_METRICS } from '../visual/production/fixtures'

/** Spec §2: the primary content's top, px below `#main-content`'s top. */
const FOLD_BUDGET_PX = 300

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('Value: the monthly chart starts within 300 px of the content top at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openProductionPage(page, '/value-metrics', {
    theme: 'signal',
    now: NOW,
    me: USER,
    user: USER,
    projectId: PROJECT_ID,
    handlers: VALUE_METRICS,
    ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
  })
  const chart = frameByHeading(page, 'Hours saved per month')
  await waitForCharts(chart)

  const primary = page.locator('[data-primary]')
  await expect(primary).toHaveCount(1)
  await expect(primary.locator('[data-chart-frame]')).toHaveCount(1)
  await expect(page.locator('[data-value-hero]')).toContainText('31.5h')
  await expect(page.locator('[data-kpi-strip] [data-metric-card="compact"]')).toHaveCount(5)
  // The window picker offers a year here (`options`), and the stored 30 days is selected.
  await expect(page.getByRole('radio', { name: '365d' })).toBeVisible()
  await expect(page.getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')

  const m = await page.evaluate((sel) => {
    const main = document.querySelector(sel) as HTMLElement
    const top = main.getBoundingClientRect().top
    const first = document.querySelector('[data-primary]') as HTMLElement
    const secondary = Array.from(document.querySelectorAll('#main-content [data-disclosure], #main-content [data-tabs]'))
    return {
      primaryTop: Math.round(first.getBoundingClientRect().top - top),
      scrollHeight: main.scrollHeight,
      clientHeight: main.clientHeight,
      secondaryAfter: secondary.map((el) => Boolean(first.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)),
    }
  }, MAIN)
  test.info().annotations.push({ type: 'fold', description: `/value-metrics ${JSON.stringify(m)}` })
  console.log(`[fold] /value-metrics ${JSON.stringify(m)}`)
  expect(m.primaryTop, 'the primary content top, px below #main-content').toBeLessThanOrEqual(FOLD_BUDGET_PX)
  expect(m.secondaryAfter.length, 'Model breakdown and the assumptions are disclosures').toBeGreaterThanOrEqual(2)
  expect(m.secondaryAfter.every(Boolean), 'every disclosure follows the primary content').toBe(true)

  // Picking a year asks the API for 365 days.
  await page.getByRole('radio', { name: '365d' }).click()
  await expect.poll(() => requestsTo(api, '/api/v1/value-metrics').some((line) => line.includes('days=365'))).toBe(true)
  await expect(page.getByRole('radio', { name: '365d' })).toHaveAttribute('aria-checked', 'true')
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
