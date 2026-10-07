/**
 * VIZ-106 on the real routes (Wave 2.6, plan 3.3, 3.4, 5.3): the five report
 * pages at 375, 640 (= 1280 at 200% zoom) and 768 px with their charts, and
 * the narrow shell's navigation drawer. Since Phase D (S1-S5) no page asks a
 * chart flag, so there is one load per page and width (S6 removed the
 * flag-off loop, which no page had a variant for any more).
 *
 * WHY THE SCROLLER AND NOT THE DOCUMENT. The shell is `h-screen
 * overflow-hidden` and the page scrolls inside `<main id="main-content"
 * class="overflow-auto">`. A page wider than the screen widens main's scroll
 * box; the document never scrolls sideways, so a check on
 * `document.documentElement.scrollWidth` passes a broken page (the only
 * 375 px test before this wave, on a dev page, measured the document). Both
 * are measured here, and every chart frame's box against main's.
 *
 * Every lazy section is scrolled into reach first, so the catalogue's frames
 * are measured drawn, not as placeholders.
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type ApiHandlers } from '../lib/production-pages'
import {
  expectNoErrorFrame,
  expectNoHorizontalOverflow,
  expectNoTextEscapes,
  frameStates,
  MAIN,
  networkQuiet,
  openRollout,
} from '../lib/rollout'
import {
  GATE_CLUSTERS,
  OVERVIEW_ON,
  releaseGateOn,
  RUN_ID,
  SUITE,
  SUITE_DETAIL_ON,
  SUMMARY_REPORT_ON,
  TRENDS_ON,
} from '../visual/production/fixtures'

interface RoutePage {
  name: string
  path: string
  ready: (page: Page) => ReturnType<Page['locator']>
  handlers: ApiHandlers
  /** Chart frames drawn once every section is mounted. */
  frames: number
  /** A step after the load, before anything is measured (e.g. opening a disclosure). */
  open?: (page: Page) => Promise<void>
}

/** Summary's trend is in a collapsed disclosure (UX redesign P3): open it. */
async function openSummaryTrend(page: Page) {
  const toggle = page.getByRole('button', { name: /^Trend/ })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
}

const PAGES: RoutePage[] = [
  {
    name: 'Overview',
    path: '/overview',
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    // Phase D S1: the catalogue mounts unconditionally; the page asks no flag.
    handlers: OVERVIEW_ON,
    frames: 4,
  },
  // UX redesign P3: the catalogue's sections are in the page's tabs, so each
  // tab is a load: Volume (the default) draws the hero and the daily
  // breakdown; By suite the hero, the suite series and Compare (VIZ-605: with
  // nothing chosen it is a frame that says so); Durations and Heatmap the
  // hero and their one section. Phase D S3/S4: the page asks no flag.
  {
    name: 'Trends',
    path: '/trends',
    ready: (p) => landmark(p, 'Trend metrics'),
    handlers: TRENDS_ON,
    frames: 2,
  },
  { name: 'Trends, By suite', path: '/trends?tab=by-suite', ready: (p) => landmark(p, 'Trend metrics'), handlers: TRENDS_ON, frames: 3 },
  { name: 'Trends, Durations', path: '/trends?tab=durations', ready: (p) => landmark(p, 'Trend metrics'), handlers: TRENDS_ON, frames: 2 },
  { name: 'Trends, Heatmap', path: '/trends?tab=heatmap', ready: (p) => landmark(p, 'Trend metrics'), handlers: TRENDS_ON, frames: 2 },
  // UX redesign P3: Summary draws the suite bars (its primary); the trend is
  // in the collapsed "Trend" disclosure, so it is a second load with it open.
  // No donut and no "Failures by test" (deleted). Phase D S2: no flag.
  {
    name: 'Summary',
    path: '/reports/summary',
    ready: (p) => p.getByText('Total tests', { exact: true }),
    handlers: SUMMARY_REPORT_ON,
    frames: 1,
  },
  {
    name: 'Summary, Trend opened',
    path: '/reports/summary',
    ready: (p) => p.getByText('Total tests', { exact: true }),
    handlers: SUMMARY_REPORT_ON,
    open: openSummaryTrend,
    frames: 2,
  },
  {
    name: 'Suite detail',
    path: `/coverage/suite?name=${SUITE}&days=30`,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    // Phase D S3: the pass-rate frame always has its overlays; the page asks no flag.
    // S4: + the test x run heatmap (no flag); S5: + the suite scatter (no flag).
    handlers: SUITE_DETAIL_ON,
    frames: 4,
  },
  {
    name: 'Release gate',
    path: `/release-gate/${RUN_ID}`,
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
    // Phase D S2: the Context group mounts unconditionally; the page asks no flag.
    handlers: releaseGateOn({ clusters: GATE_CLUSTERS }),
    frames: 2,
  },
]

const WIDTHS = [375, 640, 768] as const

/**
 * Scroll `#main-content` to the end a screen at a time, so every lazy section
 * mounts; then back to the top. A HIDDEN placeholder is another tab's section
 * (UX redesign P3: a Trends tab hides the catalogue sections it does not
 * show): it never mounts, by design, so it is not waited for.
 */
async function mountEverySection(page: Page, api: Parameters<typeof networkQuiet>[1]) {
  for (let i = 0; i < 40 && (await page.locator('[data-lazy-section]:visible').count()) > 0; i++) {
    await page.locator(MAIN).evaluate((main) => {
      main.scrollTop += main.clientHeight
    })
    await networkQuiet(page, api)
  }
  await expect(page.locator('[data-lazy-section]:visible'), 'a lazy section never mounted').toHaveCount(0)
  await page.locator(MAIN).evaluate((main) => {
    main.scrollTop = 0
  })
}

for (const report of PAGES) {
  for (const width of WIDTHS) {
    test(`${report.name} at ${width} px: nothing wider than the scroller, frames drawn inside it`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const { api, errors } = await openRollout(page, report.path, { handlers: report.handlers, ready: report.ready })
      await networkQuiet(page, api)
      if (report.open) await report.open(page)
      await mountEverySection(page, api)
      await expect(page.locator('[data-chart-frame]'), `${report.name}: frames`).toHaveCount(report.frames)
      // Every frame reaches a terminal state before it is measured.
      await expect
        .poll(async () => (await frameStates(page)).filter((s) => /: loading$/.test(s)), { timeout: 20_000 })
        .toEqual([])
      await expectNoErrorFrame(page)
      // The narrow shell: the drawer is closed and the page has the whole width.
      await expect(page.getByRole('button', { name: 'Navigation menu' })).toBeVisible()
      await expect(page.getByRole('dialog', { name: 'Navigation' })).toHaveCount(0)
      const where = `${report.name} at ${width} px`
      await expectNoHorizontalOverflow(page, where)
      // The Trends "wider screen needed" apology is gone: the page is fluid.
      await expect(page.getByText(/wider screen/i)).toHaveCount(0)
      // No chart text drawn outside its frame, cut by its svg, or wider than its box.
      await expectNoTextEscapes(page, where)
      expect(api.unhandled, 'API requests with no fixture (fail closed)').toEqual([])
      expect(api.offHost).toEqual([])
      expect(errors, 'uncaught page errors').toEqual([])
    })
  }
}

// ── The narrow shell's navigation drawer (B5, plan 3.3) ─────────────────────

const overviewReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

/** Whether the focused element is inside the drawer. */
const focusInDrawer = (page: Page) =>
  page.evaluate(() => !!document.activeElement?.closest('[role="dialog"][aria-label="Navigation"]'))

test.describe('the navigation drawer at 375 px', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('opens as a modal dialog, traps focus, closes on Escape and gives focus back to the menu button', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    const menu = page.getByRole('button', { name: 'Navigation menu' })
    const drawer = page.getByRole('dialog', { name: 'Navigation' })
    // Closed: the sidebar is off-canvas, not a column eating the page.
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeHidden()
    await expect(menu).toHaveAttribute('aria-expanded', 'false')

    await menu.click()
    await expect(drawer).toBeVisible()
    await expect(drawer).toHaveAttribute('aria-modal', 'true')
    await expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(await focusInDrawer(page), 'focus moved into the drawer').toBe(true)
    // More Tabs than the drawer has stops, both ways: focus never leaves it.
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press('Tab')
      expect(await focusInDrawer(page), `Tab ${i + 1} left the drawer`).toBe(true)
    }
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press('Shift+Tab')
      expect(await focusInDrawer(page), `Shift+Tab ${i + 1} left the drawer`).toBe(true)
    }
    await page.keyboard.press('Escape')
    await expect(drawer).toHaveCount(0)
    await expect(menu).toBeFocused()
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('closes on navigation from inside it', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', {
      handlers: [...OVERVIEW_ON, ...SUMMARY_REPORT_ON],
      ready: overviewReady,
    })
    await page.getByRole('button', { name: 'Navigation menu' }).click()
    const drawer = page.getByRole('dialog', { name: 'Navigation' })
    await expect(drawer).toBeVisible()
    // UX redesign P1: Reports (Summary · Value), selected by its stable nav id.
    await drawer.locator('[data-nav-id="reports"]').click()
    await expect(page).toHaveURL(/\/reports\/summary$/)
    await expect(page.getByText('Total tests', { exact: true })).toBeVisible()
    await expect(drawer).toHaveCount(0)
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})

// ── At 1024 px and above the desktop shell is untouched (OD-9) ──────────────

for (const width of [1024, 1280]) {
  test(`at ${width} px the desktop shell: sidebar column, one-row top bar in source order, no menu button`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready: overviewReady })
    await expect(page.getByRole('button', { name: 'Navigation menu' })).toHaveCount(0)
    await expect(page.getByRole('dialog', { name: 'Navigation' })).toHaveCount(0)
    const shell = await page.evaluate((selector) => {
      const aside = document.querySelector('aside') as HTMLElement
      const header = document.querySelector('header') as HTMLElement
      const main = document.querySelector(selector) as HTMLElement
      const children = Array.from(header.children)
        .map((child) => child.getBoundingClientRect())
        .filter((box) => box.width > 0)
      return {
        aside: { left: aside.getBoundingClientRect().left, width: aside.getBoundingClientRect().width },
        mainLeft: Math.round(main.getBoundingClientRect().left),
        headerHeight: header.getBoundingClientRect().height,
        // One row, left to right in DOM order (a narrow-only `order-*` or a wrap leaking to desktop breaks this).
        rows: new Set(children.map((box) => Math.round(box.top + box.height / 2))).size,
        inOrder: children.every((box, i) => i === 0 || box.left >= children[i - 1].right - 1),
      }
    }, MAIN)
    expect(shell.aside).toEqual({ left: 0, width: 224 })
    expect(shell.mainLeft).toBe(224)
    expect(shell.headerHeight).toBe(56)
    expect(shell.rows, 'the top bar is one row').toBe(1)
    expect(shell.inOrder, 'the top bar controls are in source order').toBe(true)
    await expectNoHorizontalOverflow(page, `the desktop shell at ${width} px`)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
}
