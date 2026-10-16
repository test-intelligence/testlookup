/**
 * W06 DEF-04 + DEF-03, hermetic (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.4, §2.7), on `/defects`:
 *
 *  - the window picker (7d / 14d / 30d / 90d; W07 WIN-01 added 14d) drives the trend and the escape rate: 7d → both are asked with `days=7`;
 *  - Open defects, P0 / P1 open and Time to resolve each draw a sparkline (three `[data-metric-card] svg`) and read
 *    their VALUES from the trend's totals; each measured point carries a `<title>` naming its day;
 *  - the banner's "Net this week" is the trend's `totals.net_last_7d` (+2 in the fixture), not a count of rows;
 *  - Escape rate reads the endpoint (41.7 %), and below its 5-defect floor "Not measured" with the server's
 *    reason, never 0 %.
 *
 * The rows are the base fixture's with its two open P0s resolved: the banner states "Net this week" only while
 * no P0 is open (it names the oldest open P0 otherwise).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-defects.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { daysAgo, defectTrendBody, trendTotalsOf } from '../visual/production/fixtures'
import { defectsWorld, rowsWithoutOpenP0 } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Defect KPIs')
const kpis = (page: Page) => landmark(page, 'Defect KPIs')
const tile = (page: Page, title: string): Locator => kpis(page).locator('[data-metric-card]').filter({ hasText: title })
const tileValue = (page: Page, title: string) => tile(page, title).locator('p.tabular-nums')
const windowRadio = (page: Page, label: string) =>
  page.getByRole('radiogroup', { name: 'Time window' }).getByRole('radio', { name: label, exact: true })
const daysOf = (lines: string[]) => lines.map((line) => new URLSearchParams(line.split('?')[1] ?? '').get('days'))
const fact = (page: Page, label: string) =>
  landmark(page, 'Defect queue verdict').locator('[data-banner-fact]').filter({ hasText: label })

const TREND = '/api/v1/analytics/defects/trend'
const ESCAPE = '/api/v1/analytics/defects/escape-rate'

test('7d: the trend and the escape rate are asked for 7 days; three sparklines, values from the totals, a point names its day', async ({ page }) => {
  const rows = rowsWithoutOpenP0()
  const { handlers } = defectsWorld({ rows })
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready })
  // The stored window is 30 days, one of the page's own (7, 14, 30, 90).
  await expect(windowRadio(page, '30d')).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('radiogroup', { name: 'Time window' }).getByRole('radio')).toHaveText(['7d', '14d', '30d', '90d'])
  await networkQuiet(page, api)
  expect(daysOf(requestsTo(api, TREND))).toContain('30')

  const before = api.seen.length
  await windowRadio(page, '7d').click()
  await expect(windowRadio(page, '7d')).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  const after = { ...api, seen: api.seen.slice(before) }
  expect(daysOf(requestsTo(after, TREND)), 'the trend, for the new window').toContain('7')
  expect(daysOf(requestsTo(after, ESCAPE)), 'the escape rate, for the new window').toContain('7')

  // The three trend tiles draw one sparkline each; the escape rate and the oldest P0 draw none.
  await expect(kpis(page).locator('[data-metric-card] svg')).toHaveCount(3)
  for (const title of ['Open defects', 'P0 / P1 open', 'Time to resolve']) {
    await expect(tile(page, title).locator('svg'), `${title}: a sparkline`).toHaveCount(1)
  }
  await expect(tile(page, 'Escape rate').locator('svg')).toHaveCount(0)

  // The values are the server's totals over every defect in scope.
  const body = defectTrendBody(7, trendTotalsOf(rows))
  const { totals, points } = body
  expect(totals.open, 'the fixture: four active rows').toBe(4)
  await expect(tileValue(page, 'Open defects')).toHaveText(String(totals.open))
  await expect(tileValue(page, 'P0 / P1 open')).toHaveText(`${totals.p0_open} / ${totals.p1_open}`)
  await expect(tileValue(page, 'Time to resolve')).toHaveText(`${(totals.mttr_days as number).toFixed(1)}d`)

  // One <title> per measured point, the newest last: its day, what opened and closed, what stayed open.
  const today = points[points.length - 1]
  expect(today.date).toBe(daysAgo(0))
  const openTitles = await tile(page, 'Open defects').locator('svg title').allTextContents()
  expect(openTitles).toHaveLength(7)
  expect(openTitles[openTitles.length - 1]).toBe(`${today.date}: +${today.opened} opened, −${today.closed} closed, ${today.open_backlog} open`)
  const hiTitles = await tile(page, 'P0 / P1 open').locator('svg title').allTextContents()
  expect(hiTitles[hiTitles.length - 1]).toBe(`${today.date}: +${today.opened} opened, −${today.closed} closed, ${today.p0p1_open} P0/P1 open`)
  const mttrTitles = await tile(page, 'Time to resolve').locator('svg title').allTextContents()
  expect(mttrTitles, 'a day with nothing resolved in its 7 days draws no point').toHaveLength(points.filter((p) => p.mttr_days != null).length)
  expect(mttrTitles[mttrTitles.length - 1]).toContain(`${today.date}: `)
  expect(mttrTitles[mttrTitles.length - 1]).toContain('to resolve (7-day mean)')

  // Net this week is the endpoint's last-7-day net (+2), not the loaded rows' (6 created, 3 resolved: +3).
  expect(totals.net_last_7d).toBe(2)
  await expect(fact(page, 'Net this week')).toHaveText('Net this week +2')
  assertHermetic(api, errors)
})

test('Escape rate reads the endpoint: 41.7%, with what it counted in the hover', async ({ page }) => {
  const { handlers } = defectsWorld()
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready })
  const card = kpis(page).locator('[data-escape-measured]')
  await expect(card).toHaveAttribute('data-escape-measured', 'true')
  await expect(card.locator('p.tabular-nums')).toHaveText('41.7%')
  const hint = await card.locator('p[title]').first().getAttribute('title')
  expect(hint).toContain('5 of 12')
  expect(hint).toContain('Production outcomes in the window: 1 incident(s), 1 rollback(s).')
  // HON-07's tile is gone; the unlinked count is still the banner's.
  await expect(kpis(page).getByText('Unlinked to Jira')).toHaveCount(0)
  await expect(fact(page, 'No Jira ticket')).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Escape rate below its floor reads "Not measured" with the server\'s reason, never 0%', async ({ page }) => {
  const { handlers } = defectsWorld({ escapeBelowFloor: true })
  const { api, errors } = await openRollout(page, '/defects', { handlers, ready })
  const card = kpis(page).locator('[data-escape-measured]')
  await expect(card).toHaveAttribute('data-escape-measured', 'false')
  await expect(card.locator('p.tabular-nums')).toHaveText('—')
  await expect(card.locator('[data-metric-trend="none"]')).toHaveText('Not measured')
  await expect(card).not.toContainText('0%')
  const hint = await card.locator('p[title]').first().getAttribute('title')
  expect(hint).toContain('3 defect(s) reported in the last 30 days affect a release that has shipped; 5 are needed.')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

// "Not measured" is the tile's small line under a "—" value: as the 24 px value it ran into the tile's
// padding at 1280 px on Windows' fonts (CI's DejaVu is wider). The line must stay inside the tile, uncut.
for (const width of [1280, 1440]) {
  test(`"Not measured" stays inside its tile at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { handlers } = defectsWorld({ escapeBelowFloor: true })
    const { api, errors } = await openRollout(page, '/defects', { handlers, ready })
    const card = kpis(page).locator('[data-escape-measured="false"] [data-metric-card]')
    await expect(card.locator('[data-metric-trend="none"]')).toHaveText('Not measured')
    await page.evaluate(() => document.fonts.ready)
    const fit = await card.evaluate((tile) => {
      const box = tile.getBoundingClientRect()
      const line = tile.querySelector('[data-metric-trend="none"] span') as HTMLElement
      const r = line.getBoundingClientRect()
      return { tile: [Math.round(box.left), Math.round(box.right)], line: [Math.round(r.left), Math.round(r.right)], cut: line.scrollWidth > line.clientWidth }
    })
    console.log(`ESCAPE-FIT ${width} ${JSON.stringify(fit)}`)
    expect(fit.line[0], 'the line starts inside its tile').toBeGreaterThanOrEqual(fit.tile[0])
    expect(fit.line[1], 'the line ends inside its tile').toBeLessThanOrEqual(fit.tile[1])
    expect(fit.cut, '"Not measured" is not truncated').toBe(false)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}
