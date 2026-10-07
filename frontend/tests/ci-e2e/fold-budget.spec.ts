/**
 * The fold budget of every top-level route (UX redesign P6,
 * `03-implementation-plan.md` § P6, `02-design-spec.md` §2), hermetic.
 *
 * Each route opens on its own spec's fixtures and `ready` probe (the page's
 * `fold-<page>.spec.ts` or rollout spec; the answers that lived inside a spec
 * are in `fixtures-pages.ts`), and at 1440 x 900:
 *
 *  - exactly one `[data-primary]`, its top at most 320 px below the top of
 *    `#main-content` (the scroller: the section tabs above the page count);
 *  - `#main-content`'s `scrollHeight` at most the route's budget in `ROUTES`
 *    — the measurement rounded UP to the next 50 px, the larger of Windows'
 *    fonts and CI's DejaVu Sans. A page that grows past it fails here, so the
 *    growth is a decision (raise the budget in the same change, and say why),
 *    never an accident. Each run prints a FOLD line per route;
 *  - where the primary content is a table, it is no wider than its box
 *    (`primaryOverflow`) — also at 1280 x 900 — and the scroller never scrolls
 *    sideways on any route.
 *
 * The plan's height targets (dashboards ≤ 1.5 viewports, Home ≤ 1,400,
 * Failures / Trends ≤ 1,600, list pages ≤ 1,100 + pagination) are in the table
 * as `target`; a route measured over its target says so beside its budget.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, frameByHeading, landmark, waitForCharts, type ApiHandlers } from '../lib/production-pages'
import { expectDrawn, MAIN, networkQuiet, openRollout, primaryOverflow, sectionFrame } from '../lib/rollout'
import {
  COVERAGE_ON,
  DEFECTS,
  FAILURES_ON,
  GATE_CLUSTERS,
  LAYOUT,
  OVERVIEW_ON,
  releaseGateOn,
  RUN_ID,
  RUNS_PAGE,
  SUITE_DETAIL_ON,
  SUITE_ID,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
  VALUE_METRICS,
} from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY, RUN_PAGE } from '../visual/production/fixtures-run'
import {
  agentsHandlers,
  flakyHandlers,
  inboxHandlers,
  RELEASES_PAGE,
  TEST_CASE,
  TEST_CASE_NAME,
  TEST_CASE_PATH,
  TEST_CASE_ROOT_CAUSE,
  TEST_CASES,
  withGateBlockers,
} from '../visual/production/fixtures-pages'

/** Spec §2 (P6): the primary content's top, px below `#main-content`'s top. */
const PRIMARY_TOP_PX = 320

interface FoldRoute {
  /** The route as the plan names it. */
  route: string
  /** What is opened (a route with an id opens the fixtures' entity). */
  path: string
  /** Fresh answers per test (some fixtures keep state). */
  handlers: () => ApiHandlers
  ready: (page: Page) => Locator
  /** What else the page's own fold spec waits for before it measures (charts drawn). */
  settle?: (page: Page) => Promise<void>
  /** `#main-content`'s scrollHeight budget at 1440 x 900, px. */
  budget: number
  /** The plan's height target, px, where it gives one (§2: list pages 1,100 + pagination). */
  target?: number
  /** The primary content is (or holds) a table: its width is checked. */
  table: boolean
}

/**
 * Every top-level route. Each budget's comment is what it was set from: the
 * scrollHeight measured at 1440 x 900 with Windows' fonts / with CI's DejaVu
 * Sans (P6, 2026-10-07), and, where the page is OVER its plan target, where
 * the height goes (FOLD_BLOCKS=1 prints it).
 */
const ROUTES: FoldRoute[] = [
  {
    route: '/overview',
    path: '/overview',
    handlers: () => OVERVIEW_ON,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    settle: async (p) => {
      await expectDrawn(sectionFrame(p, 'overview-trend', 'Pass rate trend'), 'trend')
      await expectDrawn(sectionFrame(p, 'overview-donut', 'Status breakdown'), 'donut')
    },
    // 1,204 / 1,241.
    budget: 1_250,
    target: 1_400,
    table: false,
  },
  {
    route: '/runs',
    path: '/runs',
    handlers: () => RUNS_PAGE,
    ready: (p) => p.locator('[data-status-banner]'),
    // 1,348 / 1,357. OVER the list-page target (1,100 + pagination): the 25-row table
    // with its pager is 248-1,147 by itself, then two collapsed disclosures.
    budget: 1_400,
    target: 1_100,
    table: true,
  },
  {
    route: '/runs/:id',
    path: `/runs/${RUN_ID}`,
    handlers: () => RUN_PAGE,
    ready: (p) => p.locator('[data-primary] table'),
    // 1,892 / 1,892 (single-entity page: the answer is in the first viewport).
    budget: 1_900,
    table: true,
  },
  {
    route: '/runs/:id/tests/:testId',
    path: TEST_CASE_PATH,
    handlers: () => TEST_CASE,
    ready: (p) => p.getByRole('heading', { level: 1, name: TEST_CASE_NAME }),
    settle: (p) => expect(p.locator('[data-primary]').getByText(TEST_CASE_ROOT_CAUSE)).toBeVisible(),
    // 1,877 / 1,887 (single-entity page).
    budget: 1_900,
    table: false,
  },
  {
    route: '/failures',
    path: '/failures',
    handlers: () => FAILURES_ON,
    ready: (p) => landmark(p, 'Failure verdict'),
    // 1,826 / 1,838. OVER the plan's 1,600: the Top failing table ends at 711; the
    // Groups / By suite / Scatter tab section under it is 727-1,625, then two disclosures.
    budget: 1_850,
    target: 1_600,
    table: true,
  },
  {
    route: '/defects',
    path: '/defects',
    handlers: () => DEFECTS,
    ready: (p) => landmark(p, 'Defect KPIs'),
    // 1,321 / 1,321. OVER the list-page target: the defects table ends at 893; the
    // section tabs under it run to 1,180, then a disclosure.
    budget: 1_350,
    target: 1_100,
    table: true,
  },
  {
    route: '/trends',
    path: '/trends',
    handlers: () => TRENDS_ON,
    ready: (p) => landmark(p, 'Trend metrics'),
    // 1,733 / 1,765. OVER the plan's 1,600: the hero ends at 809; the By suite /
    // Durations / Heatmap tab section under it is 885-1,669.
    budget: 1_800,
    target: 1_600,
    table: false,
  },
  {
    route: '/coverage',
    path: '/coverage',
    handlers: () => COVERAGE_ON,
    ready: (p) => landmark(p, 'Run cadence'),
    // 1,350 / 1,350.
    budget: 1_400,
    table: true,
  },
  {
    route: '/reports/summary',
    path: '/reports/summary',
    handlers: () => SUMMARY_REPORT_ON,
    ready: (p) => p.getByText('Total tests', { exact: true }),
    // 1,599 / 1,599.
    budget: 1_600,
    table: true,
  },
  {
    route: '/value-metrics',
    path: '/value-metrics',
    handlers: () => VALUE_METRICS,
    ready: (p) => p.getByRole('heading', { name: 'Hours saved per month' }),
    settle: (p) => waitForCharts(frameByHeading(p, 'Hours saved per month')),
    // 844 / 844: shorter than the viewport.
    budget: 850,
    table: false,
  },
  {
    route: '/flaky',
    path: '/flaky',
    handlers: () => flakyHandlers(),
    ready: (p) => p.locator('[data-page-header] h1'),
    // 852 / 852.
    budget: 900,
    target: 1_100,
    table: true,
  },
  {
    route: '/my-failures',
    path: '/my-failures',
    handlers: () => inboxHandlers(),
    ready: (p) => p.locator('[data-page-header] h1'),
    // 1,612 / 1,587. OVER the list-page target: the page is the table, 25 two-line
    // rows (52 px) and the pager, 179-1,588.
    budget: 1_650,
    target: 1_100,
    table: true,
  },
  {
    // `/release-gate` itself redirects to the newest run's gate: the verdict is measured there.
    route: '/release-gate',
    path: `/release-gate/${RUN_ID}`,
    handlers: () => withGateBlockers(releaseGateOn({ clusters: GATE_CLUSTERS })),
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    // 961 / 961.
    budget: 1_000,
    table: false,
  },
  {
    route: '/test-management',
    path: '/test-management',
    handlers: () => TEST_CASES,
    ready: (p) => p.getByRole('heading', { name: 'Test Case Management', level: 1 }),
    // 1,020 / 1,039.
    budget: 1_050,
    target: 1_100,
    table: true,
  },
  {
    route: '/suites',
    path: '/suites',
    handlers: () => SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-primary] table'),
    // 844 / 844: shorter than the viewport.
    budget: 850,
    target: 1_100,
    table: true,
  },
  {
    route: '/suites/:id',
    path: `/suites/${SUITE_ID}`,
    handlers: () => SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-suite-tests]'),
    // 844 / 844: shorter than the viewport.
    budget: 850,
    table: true,
  },
  {
    route: '/releases',
    path: '/releases',
    handlers: () => RELEASES_PAGE,
    ready: (p) => p.getByRole('heading', { name: 'Releases', level: 1 }),
    // 894 / 912; 1,097 before P6, when a verdict band AND a KPI strip pushed the
    // list to 466 px (now 263 / 281: one StatusBanner, the rest in a disclosure).
    budget: 950,
    target: 1_100,
    table: false,
  },
  {
    // A run on screen: the page's content (with no run it is the recent pipelines and an empty report).
    route: '/agents',
    path: `/agents?run=${RUN_ID}`,
    handlers: () => agentsHandlers(),
    ready: (p) => p.getByRole('region', { name: 'AI report' }).getByText(AI_REPORT_SUMMARY),
    // 1,288 / 1,288.
    budget: 1_300,
    table: false,
  },
  {
    route: '/settings',
    path: '/settings',
    handlers: () => LAYOUT,
    ready: (p) => p.locator('[data-settings-index]'),
    // 1,687 / 1,687. OVER the list-page target: the index is seven groups of
    // one-line links and nothing else, in ONE column since the P5 baselines
    // (two columns beside the sub-nav cut every description to ten characters;
    // they measured 1,288).
    budget: 1_700,
    target: 1_100,
    table: false,
  },
]

interface Fold {
  primaries: number
  primaryTop: number | null
  primaryBottom: number | null
  scrollHeight: number
  clientHeight: number
  /** The scroller's own sideways overflow (content width against its box). */
  mainScrollWidth: number
  mainClientWidth: number
  /** The primary content holds a table (or an ARIA table). */
  primaryTable: boolean
}

/** The scroller's height and the primary content's box, px below the scroller's top (scrolled to 0). */
async function measure(page: Page): Promise<Fold> {
  await page.evaluate(() => document.fonts.ready)
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const box = primaries[0]?.getBoundingClientRect()
    return {
      primaries: primaries.length,
      primaryTop: box ? Math.round(box.top - top) : null,
      primaryBottom: box ? Math.round(box.bottom - top) : null,
      scrollHeight: main.scrollHeight,
      clientHeight: main.clientHeight,
      mainScrollWidth: main.scrollWidth,
      mainClientWidth: main.clientWidth,
      // A `<table>`, or an ARIA one (Coverage's suite rows are a grid with table roles).
      primaryTable: Boolean(primaries[0]?.querySelector('table, [role="table"]')),
    }
  }, MAIN)
}

/**
 * Where the height goes (FOLD_BLOCKS=1): each block of the page column that
 * holds the header, as [top, bottom] px below the scroller's top, named by
 * its landmark label or data attributes.
 */
async function blocks(page: Page): Promise<[string, number, number][]> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    const top = main.getBoundingClientRect().top
    const header = main.querySelector('[data-page-header]')
    // The page column: the header's nearest ancestor with more than one block in it.
    let column = header?.parentElement ?? null
    while (column && column !== main && column.children.length < 2) column = column.parentElement
    if (!column) return []
    return Array.from(column.children, (el) => {
      const box = el.getBoundingClientRect()
      const data = Array.from(el.attributes).filter((a) => a.name.startsWith('data-')).map((a) => a.name.slice(5))
      const name = el.getAttribute('aria-label') ?? (data.join(',') || `${el.tagName.toLowerCase()}.${(el.getAttribute('class') ?? '').split(' ')[0]}`)
      return [name, Math.round(box.top - top), Math.round(box.bottom - top)] as [string, number, number]
    })
  }, MAIN)
}

async function open(page: Page, r: FoldRoute) {
  const opened = await openRollout(page, r.path, { handlers: r.handlers(), ready: r.ready })
  if (r.settle) await r.settle(page)
  await networkQuiet(page, opened.api)
  return opened
}

/**
 * Each table in the primary content against the box that bounds it: its
 * nearest ancestor that clips or scrolls sideways (else the primary itself).
 * `primaryOverflow` reads a scroller's `scrollWidth`, which a wrapper with
 * `overflow: hidden` (a rounded card) keeps equal to its width while the
 * table's right-hand columns are cut off: this measures the table itself.
 */
async function clippedTables(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const primary = document.querySelector('[data-primary]') as HTMLElement
    const out: string[] = []
    for (const table of Array.from(primary.querySelectorAll<HTMLElement>('table, [role="table"]'))) {
      let box: HTMLElement = primary
      for (let el = table.parentElement; el && primary.contains(el); el = el.parentElement) {
        if (getComputedStyle(el).overflowX !== 'visible') {
          box = el
          break
        }
      }
      // A screen-reader-only table (a chart's data, `sr-only`: a 1 px clip) is not drawn.
      if (box.clientWidth <= 1) continue
      const width = Math.round(table.getBoundingClientRect().width)
      if (width > box.clientWidth + 1) out.push(`${table.getAttribute('aria-label') ?? table.tagName.toLowerCase()}: ${width} px in a ${box.clientWidth} px box`)
    }
    return out
  })
}

/** The width checks: the scroller does not scroll sideways; a table primary fits its box. */
async function expectFits(page: Page, r: FoldRoute, fold: Fold, width: number) {
  expect(fold.mainScrollWidth, `${r.route} @${width}: ${MAIN} scrolls sideways`).toBeLessThanOrEqual(fold.mainClientWidth)
  expect(fold.primaryTable, `${r.route}: the primary content holds a table`).toBe(r.table)
  if (!r.table) return
  const overflow = await primaryOverflow(page)
  const clipped = await clippedTables(page)
  console.log(`OVERFLOW ${r.route} @${width} ${JSON.stringify({ ...overflow, clipped })}`)
  expect(overflow.scrollWidth, `${r.route} @${width}: the primary table is wider than its box`).toBeLessThanOrEqual(
    overflow.clientWidth,
  )
  expect(clipped, `${r.route} @${width}: a table wider than the box that clips it`).toEqual([])
}

test.describe('1440 x 900', () => {
  test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  for (const r of ROUTES) {
    test(`${r.route}: the primary content within ${PRIMARY_TOP_PX} px, the page within ${r.budget} px, nothing wider than its box`, async ({ page }, testInfo) => {
      const { api, errors } = await open(page, r)
      const fold = await measure(page)
      // For reading the layout by eye (never compared): FOLD_SHOT=1; where the height goes: FOLD_BLOCKS=1.
      if (process.env.FOLD_SHOT) await page.screenshot({ path: testInfo.outputPath('fold-1440.png') })
      if (process.env.FOLD_BLOCKS) console.log(`BLOCKS ${r.route} ${JSON.stringify(await blocks(page))}`)
      const line = { ...fold, budget: r.budget, target: r.target ?? null }
      test.info().annotations.push({ type: 'fold', description: `${r.route} ${JSON.stringify(line)}` })
      console.log(`FOLD ${r.route} ${JSON.stringify(line)}`)

      expect(fold.primaries, `${r.route}: exactly one [data-primary]`).toBe(1)
      expect(fold.primaryTop as number, `${r.route}: the primary content top, px below ${MAIN}`).toBeLessThanOrEqual(PRIMARY_TOP_PX)
      expect(fold.scrollHeight, `${r.route}: ${MAIN} scrollHeight against its budget`).toBeLessThanOrEqual(r.budget)
      await expectFits(page, r, fold, 1440)
      assertHermetic(api, errors)
    })
  }
})

test.describe('1280 x 900', () => {
  test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  for (const r of ROUTES) {
    test(`${r.route}: nothing wider than its box at 1280 px`, async ({ page }) => {
      const { api, errors } = await open(page, r)
      const fold = await measure(page)
      console.log(`FOLD@1280 ${r.route} ${JSON.stringify(fold)}`)
      expect(fold.primaries, `${r.route}: exactly one [data-primary]`).toBe(1)
      await expectFits(page, r, fold, 1280)
      assertHermetic(api, errors)
    })
  }
})
