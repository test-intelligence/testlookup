/**
 * /trends and its catalogue (Wave 2.6, VIZ-408; plan 2.2 "Trends", 5.3). Since
 * Phase D the page asks no chart flag at all (S3: the page itself; S4: the
 * heatmap): the pass-rate frame always has trend analysis, zoom and release
 * markers, and three sections always sit below the body grid, each mounted
 * (and asking) only once it is near:
 *
 *   - Pass rate by suite: `chart-data` pass_rate, day x suite, top 7 + Other;
 *   - Test duration p50 / p95: two `chart-data` day series;
 *   - Suite pass rate by day: the heatmap. Wave 3 (VIZ-501, FK1) gave it its
 *     own read, `/analytics/heatmap?kind=suite_day` (the server's top suites
 *     by failures, the cut stated, no "Other" row), so the page makes 3
 *     chart-data requests and 1 heatmap request; the suite series' request
 *     feeds the multi-series only.
 *
 * Plus the unfiltered "ever had a run?" probe (`/runs?page=1&size=1`, no days).
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
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
import { TALL_VIEWPORT } from '../lib/production-pages'
import {
  CHART_DATA_PATH,
  HEATMAP_PATH,
  HOSTILE_NAME,
  PROJECT_ID,
  TRENDS_ON,
} from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Trend metrics')

const SUITES_Q = `metric=pass_rate&group_by=day&group_by=suite&top_n=7&project_id=${P}&days=14`
const P50_Q = `metric=duration_p50&group_by=day&project_id=${P}&days=14`
const P95_Q = `metric=duration_p95&group_by=day&project_id=${P}&days=14`
/** The heatmap's own read (Wave 3): the page's 14 days, the page scope. */
const HEATMAP_Q = `kind=suite_day&project_id=${P}&days=14`
/** The existence probe: unfiltered, so no `days` (K6). */
const PROBE = `GET /api/v1/runs?project_id=${P}&page=1&size=1`

/**
 * One cold load, every section near. The shell (`SHELL_BASE`: nothing on the
 * page asks a flag since S4), the page's own reads (one window: the Wave 2.6
 * double-fetch fix), the three chart-data requests, the heatmap's own request
 * and the probe. The markers read the top bar's cached release list
 * (`useReleases(..., { cached: true })`), so it is asked once.
 */
const INVENTORY = [
  ...SHELL_BASE,
  `GET /api/v1/saved-views?project_id=${P}&page=trends`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=14`,
  `GET /api/v1/metrics/summary?project_id=${P}&days=14`,
  `GET /api/v1/analytics/coverage?project_id=${P}&days=14`,
  `GET /api/v1/analytics/flaky-tests?project_id=${P}&days=14`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=1&days=14`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=14`,
  PROBE,
  `GET ${CHART_DATA_PATH}?${SUITES_Q}`,
  `GET ${CHART_DATA_PATH}?${P50_Q}`,
  `GET ${CHART_DATA_PATH}?${P95_Q}`,
  `GET ${HEATMAP_PATH}?${HEATMAP_Q}`,
]

const SECTIONS: [string, string][] = [
  ['trends-pass-rate', 'Pass rate trend'],
  ['trends-multi-series', 'Pass rate by suite'],
  ['trends-duration', 'Test duration (p50 / p95)'],
]
const HEATMAP: [string, string] = ['trends-heatmap', 'Suite pass rate by day']

/**
 * Requests for the ECharts LIBRARY (the heatmap engine's lazy chunk pulls it
 * in): Vite serves it as pre-bundled `deps/echarts_*.js` (and `zrender`).
 * The kit's own option builders under `engines/echarts/*Option.ts` import
 * only ECharts TYPES and are statically imported by the frames; they are not
 * the engine and are not counted.
 */
function watchEcharts(page: Page): string[] {
  const seen: string[] = []
  page.on('request', (request) => {
    if (/\/(echarts|zrender)[^/]*\.js$|\/deps\/(echarts|zrender)/i.test(new URL(request.url()).pathname)) {
      seen.push(request.url())
    }
  })
  return seen
}

async function openTrends(page: Page, days?: number) {
  return openRollout(page, '/trends', { handlers: TRENDS_ON, ready, days })
}

test.describe('Trends, everything on screen (1280 x 4000)', () => {
  test.use({ viewport: { ...TALL_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('four sections with their headings, drawn; the heatmap asks /analytics/heatmap once, the series chart-data once', async ({
    page,
  }) => {
    const echarts = watchEcharts(page)
    const { api, errors } = await openTrends(page)
    for (const [id, title] of [...SECTIONS, HEATMAP]) {
      await expect(section(page, id), id).toHaveCount(1)
      await expect(section(page, id).getByRole('heading', { level: 3, name: title, exact: true })).toBeVisible()
      await expectDrawn(sectionFrame(page, id, title), id)
    }
    // Trend analysis on the existing frame: the overlay row and the brush.
    const passRate = sectionFrame(page, 'trends-pass-rate', 'Pass rate trend')
    await expect(passRate.locator('[data-trend-controls]')).toBeVisible()
    await expect(passRate.locator('[data-chart-brush]')).toBeVisible()
    // The heatmap is a canvas (the lazy ECharts engine), and it says which rows it shows.
    const heatmap = sectionFrame(page, ...HEATMAP)
    await expect(heatmap.locator('canvas').first()).toBeVisible()
    // The server's cut, stated: its top 7 of 11 suites by failures, never an "Other" row.
    await expect(heatmap.locator('[data-heatmap-rows]')).toContainText('Top 7 of 11 suites by failures.')
    await expect(heatmap).not.toContainText('combined in Other')
    expect(echarts.length, 'the ECharts engine loaded for the heatmap').toBeGreaterThan(0)
    // Each chart-data frame states its grain.
    for (const [id] of [SECTIONS[1], SECTIONS[2], HEATMAP]) {
      await expect(section(page, id).locator('[data-catalogue-grain]'), id).toHaveText('Counted per test execution.')
    }
    // The hostile suite name is text: no element made from it.
    await expect(page.locator('[data-trends-catalogue] img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expect(section(page, 'trends-multi-series')).toContainText(HOSTILE_NAME)
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Trends at 1280')
    await networkQuiet(page, api)
    expect(requestsTo(api, CHART_DATA_PATH).filter((line) => line.includes('group_by=suite'))).toHaveLength(1)
    expect(requestsTo(api, HEATMAP_PATH)).toEqual([`GET ${HEATMAP_PATH}?${HEATMAP_Q}`])
    expectInventory(api, errors, INVENTORY, 'Trends')
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // new sections' subtree only (the page's own pre-existing findings are not
  // this wave's; see plan 5.5 on the lab muted token), in both themes the
  // harness renders, the heatmap included. No
  // allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/trends', { handlers: TRENDS_ON, ready, theme })
      for (const [id, title] of [...SECTIONS, HEATMAP]) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openTrends(page, 365)
    for (const [id, title] of [...SECTIONS, HEATMAP]) await expectDrawn(sectionFrame(page, id, title), id)
    await networkQuiet(page, api)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
    // Trends opens on its own 14 days whatever was stored: one window, no double fetch.
    expectInventory(api, errors, INVENTORY, 'Trends at a stored 365 days')
  })
})

test.describe('Trends, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no chart-data and no probe until a section is near, and asked before it is visible', async ({ page }) => {
    const { api, errors } = await openTrends(page)
    const chartData = (query: string) => () => requestsTo(api, CHART_DATA_PATH).filter((l) => l.endsWith(query)).length
    await networkQuiet(page, api)
    expect(requestsTo(api, CHART_DATA_PATH), 'chart-data before any section is near').toEqual([])
    expect(requestsTo(api, '/api/v1/runs').filter((l) => l === PROBE), 'the probe before any section is near').toEqual([])
    await proveLazyMount(page, api, {
      label: 'trends-multi-series',
      section: 'trends-multi-series',
      asked: chartData(SUITES_Q),
      before: 0,
    })
    await expect.poll(() => requestsTo(api, '/api/v1/runs').filter((l) => l === PROBE).length).toBe(1)
    // The duration section is further down: still a placeholder, still unasked.
    await networkQuiet(page, api)
    await expect(page.locator('[data-lazy-section="trends-duration"]')).toHaveCount(1)
    expect(chartData(P50_Q)() + chartData(P95_Q)(), 'duration asked while far below the fold').toBe(0)
    // The heatmap (Wave 3) is further down still: its own read waits for it to be near.
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap asked while far below the fold').toEqual([])
    await expectDrawn(sectionFrame(page, 'trends-multi-series', 'Pass rate by suite'), 'suites')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})
