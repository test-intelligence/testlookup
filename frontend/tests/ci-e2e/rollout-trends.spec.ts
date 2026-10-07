/**
 * /trends and its catalogue (Wave 2.6, VIZ-408; plan 2.2 "Trends", 5.3). Since
 * Phase D the page asks no chart flag at all (S3: the page itself; S4: the
 * heatmap): the pass-rate frame always has trend analysis, zoom and release
 * markers.
 *
 * UX redesign P3 (the page template): the pass-rate trend is the page's hero
 * (`data-primary`, under the KPI strip), and the catalogue's sections moved
 * into the page's tabs (`?tab=`), each section mounted (and asking) only in
 * its own tab, and only once it is near:
 *
 *   - Volume (the default): no catalogue section at all — the page loads with
 *     no chart-data, no heatmap and no probe request;
 *   - By suite: Pass rate by suite (`chart-data` pass_rate, day x suite, top
 *     7 + Other) and Compare (C1: its own `chart-data` read of the three
 *     busiest suites of the series, no `top_n`);
 *   - Durations: Test duration p50 / p95, two `chart-data` day series;
 *   - Heatmap: Suite pass rate by day, its own read (Wave 3, VIZ-501:
 *     `/analytics/heatmap?kind=suite_day`, the server's top suites by
 *     failures, the cut stated, no "Other" row).
 *
 * Each catalogue tab also asks the unfiltered "ever had a run?" probe
 * (`/runs?page=1&size=1`, no days) once. The four sections were one stack
 * before P3 (one load asked all of them); each tab's inventory is now that
 * stack's share. Fail-closed harness: `tests/lib/production-pages.ts`;
 * helpers: `rollout.ts`.
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
/** Compare (C1): the busiest three of the suite series, sorted; the page scope first, as the card builds it. */
const COMPARE_Q = `project_id=${P}&days=14&metric=pass_rate&group_by=day&group_by=suite&suite_name=Auth&suite_name=Checkout&suite_name=Payments`
const P50_Q = `metric=duration_p50&group_by=day&project_id=${P}&days=14`
const P95_Q = `metric=duration_p95&group_by=day&project_id=${P}&days=14`
/** The heatmap's own read (Wave 3): the page's 14 days, the page scope. */
const HEATMAP_Q = `kind=suite_day&project_id=${P}&days=14`
/** The existence probe: unfiltered, so no `days` (K6). */
const PROBE = `GET /api/v1/runs?project_id=${P}&page=1&size=1`

/**
 * One cold load on the default tab (Volume). The shell (`SHELL_BASE`: nothing
 * on the page asks a flag since S4) and the page's own reads (one window: the
 * Wave 2.6 double-fetch fix). The hero's markers read the top bar's cached
 * release list (`useReleases(..., { cached: true })`), so it is asked once.
 * No catalogue read: Volume shows no catalogue section (P3).
 */
const LOAD = [
  ...SHELL_BASE,
  `GET /api/v1/saved-views?project_id=${P}&page=trends`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=14`,
  `GET /api/v1/metrics/summary?project_id=${P}&days=14`,
  `GET /api/v1/analytics/coverage?project_id=${P}&days=14`,
  // No analytics/flaky-tests since the UX redesign P2: it fed only the
  // provenance footer's count, and the footer was removed.
  `GET /api/v1/runs?project_id=${P}&page=1&size=1&days=14`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=14`,
]

/** What each catalogue tab adds to a load, every section of the tab near. */
const TAB_READS = {
  'by-suite': [PROBE, `GET ${CHART_DATA_PATH}?${SUITES_Q}`, `GET ${CHART_DATA_PATH}?${COMPARE_Q}`],
  durations: [PROBE, `GET ${CHART_DATA_PATH}?${P50_Q}`, `GET ${CHART_DATA_PATH}?${P95_Q}`],
  heatmap: [PROBE, `GET ${HEATMAP_PATH}?${HEATMAP_Q}`],
} as const
type CatalogueTab = keyof typeof TAB_READS

const PASS_RATE: [string, string] = ['trends-pass-rate', 'Pass rate trend']
const SUITES: [string, string] = ['trends-multi-series', 'Pass rate by suite']
const COMPARE: [string, string] = ['trends-compare', 'Compare']
const DURATION: [string, string] = ['trends-duration', 'Test duration (p50 / p95)']
const HEATMAP: [string, string] = ['trends-heatmap', 'Suite pass rate by day']

/** Each catalogue tab's sections (the catalogue's `sections`: the others' are not rendered there at all). */
const TAB_SECTIONS: Record<CatalogueTab, [string, string][]> = {
  'by-suite': [SUITES, COMPARE],
  durations: [DURATION],
  heatmap: [HEATMAP],
}
const CATALOGUE_SECTIONS = [SUITES, COMPARE, DURATION, HEATMAP]
const TAB_LABEL: Record<CatalogueTab, string> = { 'by-suite': 'By suite', durations: 'Durations', heatmap: 'Heatmap' }

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

async function openTrends(page: Page, { days, tab }: { days?: number; tab?: CatalogueTab } = {}) {
  return openRollout(page, tab ? `/trends?tab=${tab}` : '/trends', { handlers: TRENDS_ON, ready, days })
}

const pageTab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name, exact: true })

test.describe('Trends, everything on screen (1280 x 4000)', () => {
  test.use({ viewport: { ...TALL_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('a load opens on Volume: the hero draws with its analysis, and no catalogue section asks anything', async ({ page }) => {
    const echarts = watchEcharts(page)
    const { api, errors } = await openTrends(page)
    await expect(pageTab(page, 'Volume')).toHaveAttribute('aria-selected', 'true')
    // The hero: the page's one primary content, its heading the first under the title (P3).
    const passRate = sectionFrame(page, ...PASS_RATE)
    await expect(section(page, PASS_RATE[0])).toHaveAttribute('data-primary', '')
    await expect(section(page, PASS_RATE[0]).getByRole('heading', { level: 2, name: PASS_RATE[1], exact: true })).toBeVisible()
    await expectDrawn(passRate, 'pass rate')
    // Trend analysis on the existing frame: the overlay row and the brush.
    await expect(passRate.locator('[data-trend-controls]')).toBeVisible()
    await expect(passRate.locator('[data-chart-brush]')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Daily breakdown', exact: true })).toBeVisible()
    await networkQuiet(page, api)
    for (const [id] of CATALOGUE_SECTIONS) await expect(section(page, id), id).toHaveCount(0)
    await expect(page.locator('[data-lazy-section]')).toHaveCount(0)
    expect(echarts, 'no heatmap engine on Volume').toEqual([])
    await expectNoErrorFrame(page)
    expectInventory(api, errors, LOAD, 'Trends on Volume')
  })

  for (const tab of Object.keys(TAB_READS) as CatalogueTab[]) {
    test(`?tab=${tab}: its sections draw with their headings and grain, the others never mount; exactly its reads`, async ({ page }) => {
      const echarts = watchEcharts(page)
      const { api, errors } = await openTrends(page, { tab })
      await expect(pageTab(page, TAB_LABEL[tab])).toHaveAttribute('aria-selected', 'true')
      for (const [id, title] of TAB_SECTIONS[tab]) {
        await expect(section(page, id), id).toHaveCount(1)
        await expect(section(page, id).getByRole('heading', { level: 3, name: title, exact: true })).toBeVisible()
        await expectDrawn(sectionFrame(page, id, title), id)
      }
      await networkQuiet(page, api)
      // The other tabs' sections: not rendered at all — no section, and no
      // placeholder (none is hidden by CSS any more: the tab composes its own).
      for (const [id] of CATALOGUE_SECTIONS.filter(([id]) => !TAB_SECTIONS[tab].some(([own]) => own === id))) {
        await expect(section(page, id), `${id} mounted on ${tab}`).toHaveCount(0)
        await expect(page.locator(`[data-lazy-section="${id}"]`), `${id}'s placeholder on ${tab}`).toHaveCount(0)
      }
      // Each chart-data or heatmap frame states its grain (Compare's words are its own).
      for (const [id] of TAB_SECTIONS[tab].filter(([id]) => id !== COMPARE[0])) {
        await expect(section(page, id).locator('[data-catalogue-grain]'), id).toHaveText('Counted per test execution.')
      }
      if (tab === 'by-suite') {
        // The hostile suite name is text: no element made from it.
        await expect(page.locator('[data-trends-catalogue] img')).toHaveCount(0)
        expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
        await expect(section(page, SUITES[0])).toContainText(HOSTILE_NAME)
        // C1: Compare draws the busiest three suites, one line each, with its own pickers.
        const compare = sectionFrame(page, ...COMPARE)
        await expect(compare).toContainText('The busiest suites in the window; choose others with Suites.')
        await expect(compare.locator('[data-compare-picker="suite"]')).toHaveAccessibleName('Suites, 3 selected')
        expect(requestsTo(api, CHART_DATA_PATH).filter((line) => line.includes('suite_name='))).toEqual([`GET ${CHART_DATA_PATH}?${COMPARE_Q}`])
        expect(requestsTo(api, CHART_DATA_PATH).filter((line) => line.includes('top_n=7'))).toHaveLength(1)
      }
      if (tab === 'heatmap') {
        // The heatmap is a canvas (the lazy ECharts engine), and it says which rows it shows.
        const heatmap = sectionFrame(page, ...HEATMAP)
        await expect(heatmap.locator('canvas').first()).toBeVisible()
        // The server's cut, stated: its top 7 of 11 suites by failures, never an "Other" row.
        await expect(heatmap.locator('[data-heatmap-rows]')).toContainText('Top 7 of 11 suites by failures.')
        await expect(heatmap).not.toContainText('combined in Other')
        expect(echarts.length, 'the ECharts engine loaded for the heatmap').toBeGreaterThan(0)
        expect(requestsTo(api, HEATMAP_PATH)).toEqual([`GET ${HEATMAP_PATH}?${HEATMAP_Q}`])
      } else {
        expect(echarts, `no heatmap engine on ${tab}`).toEqual([])
      }
      await expectNoErrorFrame(page)
      await expectNoTextEscapes(page, `Trends ${tab} at 1280`)
      expectInventory(api, errors, [...LOAD, ...TAB_READS[tab]], `Trends ${tab}`)
    })
  }

  test('opening a tab makes its requests, and only then', async ({ page }) => {
    const { api, errors } = await openTrends(page)
    await networkQuiet(page, api)
    expect(requestsTo(api, CHART_DATA_PATH), 'chart-data on Volume').toEqual([])
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap on Volume').toEqual([])
    expect(requestsTo(api, '/api/v1/runs').filter((l) => l === PROBE), 'the probe on Volume').toEqual([])
    const asked = (query: string) => () => observedTo(api, query)
    await pageTab(page, 'By suite').click()
    await expect(page).toHaveURL(/[?&]tab=by-suite(&|$)/)
    await expect.poll(asked(SUITES_Q), { message: 'the suite series once By suite opens' }).toBe(1)
    await expectDrawn(sectionFrame(page, ...SUITES), 'suites')
    expect(asked(P50_Q)() + asked(P95_Q)(), 'durations asked on By suite').toBe(0)
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap asked on By suite').toEqual([])
    await pageTab(page, 'Durations').click()
    await expect.poll(() => asked(P50_Q)() + asked(P95_Q)(), { message: 'p50 and p95 once Durations opens' }).toBe(2)
    await expectDrawn(sectionFrame(page, ...DURATION), 'duration')
    await expect(section(page, SUITES[0]), 'By suite\'s sections leave with their tab').toHaveCount(0)
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap asked on Durations').toEqual([])
    await pageTab(page, 'Heatmap').click()
    await expect.poll(() => requestsTo(api, HEATMAP_PATH).length, { message: 'the heatmap once its tab opens' }).toBe(1)
    await expectDrawn(sectionFrame(page, ...HEATMAP), 'heatmap')
    await pageTab(page, 'Volume').click()
    await expect(page).not.toHaveURL(/[?&]tab=/)
    for (const [id] of CATALOGUE_SECTIONS) await expect(section(page, id), id).toHaveCount(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // catalogue sections' subtree only (the page's own pre-existing findings
  // are not this wave's; see plan 5.5 on the lab muted token), in both themes
  // the harness renders, the heatmap included — each in its tab since P3, the
  // hero (a catalogue section) with every one. No allowlist: R2 measured 0
  // violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/trends?tab=by-suite', { handlers: TRENDS_ON, ready, theme })
      for (const tab of Object.keys(TAB_READS) as CatalogueTab[]) {
        await pageTab(page, TAB_LABEL[tab]).click()
        for (const [id, title] of [PASS_RATE, ...TAB_SECTIONS[tab]]) await expectDrawn(sectionFrame(page, id, title), id)
        await networkQuiet(page, api)
        await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
      }
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openTrends(page, { days: 365 })
    await networkQuiet(page, api)
    // Trends opens on its own 14 days whatever was stored: one window, no double fetch.
    expectInventory(api, errors, LOAD, 'Trends at a stored 365 days')
    for (const tab of Object.keys(TAB_READS) as CatalogueTab[]) {
      await pageTab(page, TAB_LABEL[tab]).click()
      for (const [id, title] of TAB_SECTIONS[tab]) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
    }
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  })
})

/** How many requests to `/chart-data` end with `query`. */
function observedTo(api: Parameters<typeof requestsTo>[0], query: string): number {
  return requestsTo(api, CHART_DATA_PATH).filter((line) => line.endsWith(query)).length
}

test.describe('Trends, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: in the By suite tab, no chart-data and no probe until a section is near, and asked before it is visible', async ({ page }) => {
    const { api, errors } = await openTrends(page, { tab: 'by-suite' })
    const chartData = (query: string) => () => observedTo(api, query)
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
    await expectDrawn(sectionFrame(page, ...SUITES), 'suites')
    // The other tabs' sections: never asked, however far the reader scrolls.
    await page.locator('#main-content').evaluate((main) => {
      main.scrollTop = main.scrollHeight
    })
    await networkQuiet(page, api)
    expect(chartData(P50_Q)() + chartData(P95_Q)(), 'duration asked on By suite').toBe(0)
    expect(requestsTo(api, HEATMAP_PATH), 'the heatmap asked on By suite').toEqual([])
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})
