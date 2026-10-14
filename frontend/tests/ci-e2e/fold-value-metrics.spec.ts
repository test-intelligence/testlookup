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
 * HON-04: **Export** fetches the report through the app's API client (the
 * session's Bearer token goes with it) and saves a JSON file; a refusal is a
 * toast, not a saved error body. TRND-05: the hours-saved headline and its
 * label follow the window picker.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`.
 */
import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { frameByHeading, openProductionPage, respond, respondRaw, waitForCharts, type ApiHandlers } from '../lib/production-pages'
import { MAIN, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, USER, VALUE_METRICS, valueMetrics } from '../visual/production/fixtures'

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

/** Open /value-metrics with `extra` handlers ahead of the fixture's (first match wins). */
async function openValue(page: import('@playwright/test').Page, extra: ApiHandlers) {
  return openProductionPage(page, '/value-metrics', {
    theme: 'signal',
    now: NOW,
    me: USER,
    user: USER,
    projectId: PROJECT_ID,
    handlers: [...extra, ...VALUE_METRICS],
    ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
  })
}

test('Value: Export downloads the window as JSON, sent with the session token (HON-04)', async ({ page }) => {
  const authorization: (string | undefined)[] = []
  const { api, errors } = await openValue(page, [
    [
      '/api/v1/value-metrics/export',
      ({ url, route }) => {
        authorization.push(route.request().headers()['authorization'])
        const days = Number(url.searchParams.get('days'))
        return respondRaw(JSON.stringify({ report_type: 'value_metrics', ...valueMetrics(), period_days: days }), 'application/json')
      },
    ],
  ])

  const download = page.waitForEvent('download')
  await page.getByTestId('value-metrics-export').click()
  const file = await download
  expect(file.suggestedFilename()).toBe('value-metrics-30d.json')
  const body = JSON.parse(await readFile(await file.path(), 'utf8')) as Record<string, unknown>
  expect(body.report_type).toBe('value_metrics')
  expect(body.period_days).toBe(30)
  for (const key of ['defects_auto_grouped', 'duplicate_tickets_avoided', 'headline', 'monthly']) expect(body).toHaveProperty(key)
  expect(body).not.toHaveProperty('detail')
  // The old `<a href download>` navigation carried no Authorization header.
  expect(authorization).toEqual(['Bearer access'])
  expect(requestsTo(api, '/api/v1/value-metrics/export')).toEqual([
    `GET /api/v1/value-metrics/export?project_id=${PROJECT_ID}&days=30`,
  ])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Value: a refused export is a toast, and nothing is saved (HON-04)', async ({ page }) => {
  const downloads: string[] = []
  page.on('download', (d) => downloads.push(d.suggestedFilename()))
  const { api, errors } = await openValue(page, [
    ['/api/v1/value-metrics/export', () => respond(403, { detail: 'You do not have access to this project' })],
  ])

  await page.getByTestId('value-metrics-export').click()
  await expect(page.getByText('Export failed: You do not have access to this project')).toBeVisible()
  await expect(page.getByTestId('value-metrics-export')).toBeEnabled()
  expect(downloads).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Value: the hours-saved headline follows the window picker (TRND-05)', async ({ page }) => {
  const { api, errors } = await openValue(page, [
    [
      '/api/v1/value-metrics',
      ({ url }) => {
        const days = Number(url.searchParams.get('days'))
        const base = valueMetrics()
        const hours = days === 90 ? 94.5 : base.headline.hours_saved_30d
        return {
          ...base,
          period_days: days,
          headline: { ...base.headline, hours_saved: hours, fte_equivalent: 0.2, window_days: days },
        }
      },
    ],
  ])

  const hero = page.locator('[data-value-hero]')
  await expect(hero).toContainText('Eng-hours saved · last 30 days', { ignoreCase: true })
  await expect(hero).toContainText('31.5h')
  await expect(hero).not.toContainText('window picker does not change it')

  await page.getByRole('radio', { name: '90d' }).click()
  await expect(hero).toContainText('Eng-hours saved · last 90 days', { ignoreCase: true })
  await expect(hero).toContainText('94.5h')
  await expect.poll(() => requestsTo(api, '/api/v1/value-metrics').some((line) => line.includes('days=90'))).toBe(true)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
