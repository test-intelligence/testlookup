/**
 * /overview with the catalogue (Wave 2.6, VIZ-408; plan 2.2 "Overview",
 * 5.3). The pass-rate trend REPLACES the Execution-trend card (OD-4), a status donut sits beside it, and a lazy row
 * adds Top failing tests and Failure categories. The trend and the donut are
 * drawn from the page's own day series (no request); the lazy row asks
 * `/analytics/top-failing`, and Failure categories draws the page's own
 * `/analytics/failure-categories` read (no second request).
 *
 * Phase D S1: the page no longer asks `viz_chart_data_api` (migration 0195
 * turned it on everywhere and the flag-off path is deleted), so every load
 * here runs with every flag OFF in the harness and the inventory has no seam
 * lookup (`SHELL_BASE`).
 *
 * UX redesign P3 (page template): the catalogue's headline row (trend +
 * donut) is the page's primary content under a one-line verdict banner and a
 * strip of five KPI tiles; its lazy row (Top failing tests + Failure
 * categories) is the default "Top failing" tab under it, and the activity feed
 * is the "Activity" tab (`?tab=activity`). Two reads left the load:
 * `/value-metrics` (the Eng-hours KPI moved to Reports › Value) and the
 * activity feed (it asks when its tab is opened). The top-failing request
 * belongs to the Top failing tab: opened on Activity, the page never makes it.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; shared rollout
 * helpers: `tests/lib/rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { frameByHeading, respond } from '../lib/production-pages'
import {
  belowMainFold,
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
import { OVERVIEW_ON, PROJECT_ID, TOP_FAILING_PATH } from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

/** The four sections, by `data-catalogue-section`, with their frame titles. */
const SECTIONS: [string, string][] = [
  ['overview-trend', 'Pass rate trend'],
  ['overview-donut', 'Status breakdown'],
  ['overview-top-failing', 'Top failing tests'],
  ['overview-categories', 'Failure categories'],
]

/**
 * One cold load. The Wave 2.5 list (the shell, `SHELL_BASE`, and the page's
 * own reads) plus top failing; no flag lookup of the page's own. Failure categories is the page's own read and the trend's markers
 * read the top bar's cached release list, so neither is asked twice. Since P3
 * no `/value-metrics` (the Eng-hours KPI is on Reports › Value) and no
 * activity feed until the Activity tab is opened (`ACTIVITY_30`).
 */
const INVENTORY_ON = [
  ...SHELL_BASE,
  `GET /api/v1/saved-views?project_id=${P}&page=dashboard`,
  `GET /api/v1/metrics/summary?project_id=${P}&days=30`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=30`,
  `GET /api/v1/analytics/failure-categories?project_id=${P}&days=30`,
  `GET /api/v1/analytics/top-failing?project_id=${P}&days=30`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
]

/** The activity feed's one read, asked when the Activity tab is opened. */
const ACTIVITY_PATH = `/api/v1/projects/${P}/activity`
const ACTIVITY_30 = `GET ${ACTIVITY_PATH}?limit=8&since=2026-08-19T12:00:00.000Z`

test.describe('Overview, everything on screen (1280 x 2400)', () => {
  test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the four sections render with their headings and draw; Execution trend is replaced', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready })
    for (const [id, title] of SECTIONS) {
      await expect(section(page, id), id).toHaveCount(1)
      await expect(section(page, id).getByRole('heading', { level: 3, name: title, exact: true })).toBeVisible()
      await expectDrawn(sectionFrame(page, id, title), id)
    }
    // OD-4: the pass-rate trend takes the Execution-trend card's place.
    await expect(frameByHeading(page, 'Execution trend')).toHaveCount(0)
    await expect(page.locator('[data-chart-frame]')).toHaveCount(SECTIONS.length)
    await expect(page.locator('[data-lazy-section]'), 'every lazy section mounted at this height').toHaveCount(0)
    // P3: the headline row is the primary content, under the one-line verdict
    // (which absorbed "What's blocking release") and the five KPI tiles; the
    // lazy row is the Top failing tab's (the default), under the tab bar.
    const primary = page.locator('[data-primary]')
    const panel = page.getByRole('tabpanel', { name: 'Top failing' })
    await expect(page.getByRole('tab', { name: 'Top failing' })).toHaveAttribute('aria-selected', 'true')
    for (const [id] of SECTIONS.slice(0, 2)) await expect(primary.locator(`[data-catalogue-section="${id}"]`), id).toHaveCount(1)
    for (const [id] of SECTIONS.slice(2)) await expect(panel.locator(`[data-catalogue-section="${id}"]`), id).toHaveCount(1)
    const banner = page.getByRole('region', { name: 'Release readiness' }).locator('[data-status-banner]')
    await expect(banner).toBeVisible()
    await expect(banner.getByRole('link', { name: /Open failures/ })).toHaveAttribute('href', '/failures')
    await expect(page.locator('[data-kpi-strip] [data-metric-card="compact"]')).toHaveCount(5)
    await expect(page.getByRole('heading', { name: "What's blocking release" })).toHaveCount(0)
    // Hostile test names (top failing) reach the screen as text only.
    await expect(page.locator('[data-catalogue-section] img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Overview at 1280')
    await networkQuiet(page, api)
    expectInventory(api, errors, INVENTORY_ON, 'Overview, every flag off')

    // The Activity tab is closed: opening it asks for the feed, once, and
    // writes ?tab=activity; the lazy row leaves with its tab.
    expect(requestsTo(api, ACTIVITY_PATH), 'no activity read before its tab is opened').toEqual([])
    await page.getByRole('tab', { name: 'Activity' }).click()
    await expect(page).toHaveURL(/[?&]tab=activity(&|$)/)
    await expect(page.getByRole('tabpanel', { name: 'Activity' }).getByRole('heading', { name: 'Recent activity', level: 3 })).toBeVisible()
    await expect(section(page, 'overview-top-failing')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, ACTIVITY_PATH)).toEqual([ACTIVITY_30])
    expectInventory(api, errors, [...INVENTORY_ON, ACTIVITY_30], 'Overview, Activity tab opened')
    // Back on Top failing: the row mounts again and draws (and the clean URL comes back).
    await page.getByRole('tab', { name: 'Top failing' }).click()
    await expect(page).not.toHaveURL(/[?&]tab=/)
    await expectDrawn(sectionFrame(page, 'overview-top-failing', 'Top failing tests'), 'top failing, again')
  })

  test('opened on ?tab=activity: the feed is asked at load, and top failing never is', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview?tab=activity', { handlers: OVERVIEW_ON, ready })
    await expect(page.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('heading', { name: 'Recent activity', level: 3 })).toBeVisible()
    // The headline row (page data) still draws; the lazy row is not rendered.
    await expectDrawn(sectionFrame(page, 'overview-trend', 'Pass rate trend'), 'trend')
    await expect(section(page, 'overview-top-failing')).toHaveCount(0)
    await expect(section(page, 'overview-categories')).toHaveCount(0)
    await expect(page.locator('[data-lazy-section]')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, TOP_FAILING_PATH), 'no top-failing read outside its tab').toEqual([])
    const withoutTopFailing = INVENTORY_ON.filter((line) => !line.includes('/analytics/top-failing'))
    expectInventory(api, errors, [...withoutTopFailing, ACTIVITY_30], 'Overview opened on Activity')
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // new sections' subtree only (the page's own pre-existing findings are not
  // this wave's; see plan 5.5 on the lab muted token), in both themes the
  // harness renders. No allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready, theme })
      for (const [id, title] of SECTIONS) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', {
      handlers: OVERVIEW_ON,
      ready,
      days: 365,
    })
    await expectDrawn(sectionFrame(page, 'overview-top-failing', 'Top failing tests'), 'top failing')
    await expectDrawn(sectionFrame(page, 'overview-categories', 'Failure categories'), 'categories')
    await networkQuiet(page, api)
    const days = daysOnTheWire(api)
    expect(days.filter(({ days: d }) => !(d >= 1 && d <= 90)), 'requests over 90 days').toEqual([])
    // The two catalogue requests carry the page's (snapped) window.
    expect(requestsTo(api, TOP_FAILING_PATH)).toEqual([`GET ${TOP_FAILING_PATH}?project_id=${P}&days=90`])
    // The page snaps 365 to its longest option, 90.
    const at90 = INVENTORY_ON.map((line) => line.replace('days=30', 'days=90'))
    expectInventory(api, errors, at90, 'Overview at 365 days')
  })

  test('a failing section is its own error frame: the other three draw, Retry recovers it', async ({ page }) => {
    let fail = true
    const handlers = [
      [TOP_FAILING_PATH, () => (fail ? respond(500, { detail: 'planted failure' }) : { items: [] })] as const,
      ...OVERVIEW_ON,
    ]
    const { api, errors } = await openRollout(page, '/overview', { handlers, ready })
    const failing = sectionFrame(page, 'overview-top-failing', 'Top failing tests')
    await expect(failing).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    for (const [id, title] of SECTIONS.filter(([id]) => id !== 'overview-top-failing')) {
      await expectDrawn(sectionFrame(page, id, title), id)
    }
    // VIZ-107: a chart's failure is the chart's, never a global toast.
    await expect(page.getByText('planted failure')).toHaveCount(0)
    fail = false
    await failing.getByRole('button', { name: /retry/i }).click()
    await expect(failing).not.toHaveAttribute('data-chart-state', 'error')
    await expect(failing).toHaveAttribute('data-chart-state', /^(ready|truncated|not-measured|filtered-empty)$/)
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})

/**
 * The lazy proof needs the lazy row to start more than the 200 px margin (plus
 * 50) below the scroller's fold at load. Before P3 the row sat under the
 * verdict card, the KPI grid and the headline row, far below a 600 px screen.
 * P3 put the headline row ≤ 300 px from the top (the page template), and the
 * lazy row then starts about 130 px below a 600 px screen's fold: inside the
 * margin, so it mounts at load and the proof would mean nothing. Measured at
 * 1280 wide: the row's top was ~680 px into the page, so a 420 px screen
 * (364 px of scroller under the 56 px top bar) put it ~315 px below the fold.
 * P3 cleanup moved the row into the default "Top failing" tab, under the tab
 * bar: ~65 px lower, 381 px below the fold at 420 (measured, the `lazy`
 * annotation below), so the proof still holds with room to spare.
 */
const LAZY_PROOF_VIEWPORT = { width: SHORT_VIEWPORT.width, height: 420 } as const

test.describe('Overview, a short screen (1280 x 420)', () => {
  test.use({ viewport: { ...LAZY_PROOF_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no top-failing request until the row is near, and it is asked before it is visible', async ({
    page,
  }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready })
    // The headline row is drawn from page data: it is not lazy.
    await expectDrawn(sectionFrame(page, 'overview-trend', 'Pass rate trend'), 'trend')
    const categories = () => requestsTo(api, '/api/v1/analytics/failure-categories').length
    await networkQuiet(page, api)
    expect(categories(), "only the page's own categories read before the row is near").toBe(1)
    // The lazy row is the Top failing tab's (the default), under the tab bar.
    await expect(page.getByRole('tabpanel', { name: 'Top failing' }).locator('[data-lazy-section="overview-top-failing"]')).toHaveCount(1)
    const below = await belowMainFold(page, page.locator('[data-lazy-section="overview-top-failing"]'))
    test.info().annotations.push({ type: 'lazy', description: `overview-top-failing starts ${below} px below the fold` })
    await proveLazyMount(page, api, {
      label: 'overview-top-failing',
      section: 'overview-top-failing',
      asked: () => requestsTo(api, TOP_FAILING_PATH).length,
      before: 0,
    })
    // Its neighbour in the same row mounts with it and draws the page's read: still one request.
    await expectDrawn(sectionFrame(page, 'overview-categories', 'Failure categories'), 'categories')
    expect(categories(), 'Failure categories adds no request of its own').toBe(1)
    await expectDrawn(sectionFrame(page, 'overview-top-failing', 'Top failing tests'), 'top failing')
    await networkQuiet(page, api)
    expectInventory(api, errors, INVENTORY_ON, 'Overview, scrolled to the lazy row')
  })
})
