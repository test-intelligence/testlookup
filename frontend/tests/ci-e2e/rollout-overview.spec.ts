/**
 * /overview with the catalogue ON (Wave 2.6, VIZ-408; plan 2.2 "Overview",
 * 5.3). With `viz_chart_data_api` on, the pass-rate trend REPLACES the
 * Execution-trend card (OD-4), a status donut sits beside it, and a lazy row
 * adds Top failing tests and Failure categories. The trend and the donut are
 * drawn from the page's own day series (no request); the lazy row asks
 * `/analytics/top-failing`, and Failure categories draws the page's own
 * `/analytics/failure-categories` read (no second request).
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; shared rollout
 * helpers: `tests/lib/rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { frameByHeading, respond } from '../lib/production-pages'
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
import { CATALOGUE_ON, OVERVIEW_ON, PROJECT_ID, TOP_FAILING_PATH } from '../visual/production/fixtures'
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
 * One cold load with the flag on. The flag-off list of
 * `rollout-flag-off.spec.ts`, plus the seam's lookup (`SHELL_ON`) and top
 * failing. Failure categories is the page's own read and the trend's markers
 * read the top bar's cached release list, so neither is asked twice.
 */
const INVENTORY_ON = [
  ...SHELL_ON,
  `GET /api/v1/saved-views?project_id=${P}&page=dashboard`,
  `GET /api/v1/metrics/summary?project_id=${P}&days=30`,
  `GET /api/v1/metrics/trends?project_id=${P}&days=30`,
  `GET /api/v1/analytics/failure-categories?project_id=${P}&days=30`,
  `GET /api/v1/analytics/top-failing?project_id=${P}&days=30`,
  `GET /api/v1/value-metrics?project_id=${P}&days=30&months=6`,
  `GET /api/v1/runs?project_id=${P}&page=1&size=100&days=30`,
  `GET /api/v1/projects/${P}/activity?limit=8&since=2026-08-19T12:00:00.000Z`,
]

test.describe('Overview, catalogue on, everything on screen (1280 x 2400)', () => {
  test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the four sections render with their headings and draw; Execution trend is replaced', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, flags: CATALOGUE_ON, ready })
    for (const [id, title] of SECTIONS) {
      await expect(section(page, id), id).toHaveCount(1)
      await expect(section(page, id).getByRole('heading', { level: 3, name: title, exact: true })).toBeVisible()
      await expectDrawn(sectionFrame(page, id, title), id)
    }
    // OD-4: the pass-rate trend takes the Execution-trend card's place.
    await expect(frameByHeading(page, 'Execution trend')).toHaveCount(0)
    await expect(page.locator('[data-chart-frame]')).toHaveCount(SECTIONS.length)
    await expect(page.locator('[data-lazy-section]'), 'every lazy section mounted at this height').toHaveCount(0)
    // The rest of the page is still there, one row lower.
    await expect(page.getByRole('heading', { name: "What's blocking release" })).toBeVisible()
    // Hostile test names (top failing) reach the screen as text only.
    await expect(page.locator('[data-catalogue-section] img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Overview at 1280')
    await networkQuiet(page, api)
    expectInventory(api, errors, INVENTORY_ON, 'Overview, catalogue on')
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // new sections' subtree only (the page's own pre-existing findings are not
  // this wave's; see plan 5.5 on the lab muted token), in both themes the
  // harness renders. No allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the catalogue sections, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, flags: CATALOGUE_ON, ready, theme })
      for (const [id, title] of SECTIONS) await expectDrawn(sectionFrame(page, id, title), id)
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('the stored window is 365 days: every request on the wire asks for at most 90', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/overview', {
      handlers: OVERVIEW_ON,
      flags: CATALOGUE_ON,
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
    // The page snaps 365 to its longest option, 90 (value metrics keep their own
    // 30 days, the activity feed starts 90 days back).
    const at90 = INVENTORY_ON.map((line) =>
      line.startsWith('GET /api/v1/value-metrics')
        ? line
        : line.replace('days=30', 'days=90').replace('since=2026-08-19T', 'since=2026-06-20T'),
    )
    expectInventory(api, errors, at90, 'Overview at 365 days')
  })

  test('a failing section is its own error frame: the other three draw, Retry recovers it', async ({ page }) => {
    let fail = true
    const handlers = [
      [TOP_FAILING_PATH, () => (fail ? respond(500, { detail: 'planted failure' }) : { items: [] })] as const,
      ...OVERVIEW_ON,
    ]
    const { api, errors } = await openRollout(page, '/overview', { handlers, flags: CATALOGUE_ON, ready })
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

test.describe('Overview, catalogue on, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: no top-failing request until the row is near, and it is asked before it is visible', async ({
    page,
  }) => {
    const { api, errors } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, flags: CATALOGUE_ON, ready })
    // The headline row is drawn from page data: it is not lazy.
    await expectDrawn(sectionFrame(page, 'overview-trend', 'Pass rate trend'), 'trend')
    const categories = () => requestsTo(api, '/api/v1/analytics/failure-categories').length
    await networkQuiet(page, api)
    expect(categories(), "only the page's own categories read before the row is near").toBe(1)
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
