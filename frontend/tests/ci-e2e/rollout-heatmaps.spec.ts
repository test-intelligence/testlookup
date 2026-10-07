/**
 * The generic matrix heatmap (Wave 3, VIZ-205 / VIZ-501, FK1) on its two
 * multi-kind hosts: Trends (`suite_day`, section `trends-heatmap`) and
 * Coverage (`suite_environment` with a kind selector for `suite_release`,
 * section `heatmap-suite_environment`). Suite detail's `test_run` is in
 * `rollout-suite-detail.spec.ts`. No flag is asked for any of them since
 * Phase D, S4 (migration 0195 turned the chart flags on everywhere), so no
 * flag is set here.
 *
 * Each section asks `GET /analytics/heatmap` itself, once near; a cell is a
 * mark whose one action is its rows (`/analytics/chart-data/rows`, metric
 * `executions`, the cell's two KEYS as selectors), in the page's one rows
 * panel. Fixtures: the server's wire shape (`tests/visual/production/
 * fixtures.ts`, validated by `rollout-fixtures.spec.ts`).
 *
 * Not here: All Projects (the one-project kinds ask nothing there). The
 * harness cannot reach it on Coverage: a seeded `activeProjectId: 'all'` is
 * replaced by the project on load, even with two projects listed (B0.md);
 * the rule is unit-tested in `HeatmapSection.test.tsx` (mutation M10).
 *
 * UX redesign P3: each host's heatmap is in its own page tab (`?tab=heatmap`
 * on both), so every test opens the page on that tab; the lazy proof holds
 * inside it.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, respond, TALL_VIEWPORT, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectHostileAsText,
  expectNoPrototypePollution,
  expectOneRowsPanel,
  networkQuiet,
  openRollout,
  proveLazyMount,
  queryOf,
  requestsTo,
  rowsPanels,
  section,
  sectionFrame,
  SHORT_VIEWPORT,
  watchConsoleErrors,
} from '../lib/rollout'
import { expectNoBlockingViolations } from '../lib/axe-gate'
import {
  CHART_ROWS_PATH,
  COVERAGE_ON,
  HEATMAP_PATH,
  HOSTILE_NAME,
  PROJECT_ID,
  rowsTotal,
  TRENDS_ON,
} from '../visual/production/fixtures'

const P = PROJECT_ID
const TRENDS = { id: 'trends-heatmap', title: 'Suite pass rate by day' } as const
const ENV = { id: 'heatmap-suite_environment', title: 'Suite pass rate by environment' } as const
const RELEASE_TITLE = 'Suite pass rate by release'


const openTrends = (page: Page, handlers = TRENDS_ON) =>
  openRollout(page, '/trends?tab=heatmap', { handlers, ready: (p) => landmark(p, 'Trend metrics') })
const openCoverage = (page: Page, handlers = COVERAGE_ON, days?: number) =>
  openRollout(page, '/coverage?tab=heatmap', { handlers, ready: (p) => landmark(p, 'Run cadence'), days })
/** A page tab (UX redesign P3). */
const coverageTab = (page: Page, name: string) =>
  page.getByRole('tablist', { name: 'Coverage views' }).getByRole('tab', { name, exact: true })

/** The heatmap's keyboard surface in a section. */
const keyboard = (page: Page, id: string) => section(page, id).locator('[data-chart-keyboard="heatmap"]')

test.describe('heatmaps, everything on screen (1280 x 4000)', () => {
  test.use({ viewport: { ...TALL_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('Trends: drawn from ONE /analytics/heatmap?kind=suite_day read; the cut is stated; no Other row', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await openTrends(page)
    const frame = sectionFrame(page, TRENDS.id, TRENDS.title)
    await expectDrawn(frame, TRENDS.id)
    await expect(frame.locator('canvas').first()).toBeVisible()
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH)).toEqual([`GET ${HEATMAP_PATH}?kind=suite_day&project_id=${P}&days=14`])
    await expect(frame.locator('[data-heatmap-rows]')).toContainText('Top 7 of 11 suites by failures.')
    // One kind on Trends: no kind selector; the rows order and the fit toggle are offered.
    await expect(frame.locator('[data-heatmap-kinds]')).toHaveCount(0)
    await expect(frame.locator('[data-heatmap-sort]')).toHaveValue('worst')
    await expect(frame.locator('[data-heatmap-fit]')).toHaveAttribute('aria-pressed', 'false')
    await expectHostileAsText(page, section(page, TRENDS.id), 'Trends heatmap')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test('keyboard: focus the grid, arrow to a cell, Enter opens ITS rows (keys, executions); Escape returns focus', async ({ page }) => {
    const { api, errors } = await openTrends(page)
    await expectDrawn(sectionFrame(page, TRENDS.id, TRENDS.title), TRENDS.id)
    const grid = keyboard(page, TRENDS.id)
    await grid.focus()
    await expect(grid).toBeFocused()
    await page.keyboard.press('ArrowRight')
    await expect(grid).toHaveAttribute('data-active-index', /^\d+$/)
    // The highlighted cell offers its one action as a real button (the same action Enter takes).
    await expect(section(page, TRENDS.id).locator('[data-mark-intent="rows"]')).toBeVisible()
    await page.keyboard.press('Enter')
    const panel = await expectOneRowsPanel(page, /^Executions in /)
    await expect(page).toHaveURL(/[?&]rows=/)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric'), 'every execution in the cell (reconciles with its n)').toBe('executions')
    // The time dimension first: /chart-data/rows answers 422
    // time_dimension_position for suite,day (found on the homelab, PR #177).
    expect(q.getAll('group_by')).toEqual(['day', 'suite'])
    // Selectors are KEYS (lower-cased suite, UTC day), never labels.
    expect(q.get('bucket_suite')).toMatch(/^[^A-Z]+$/)
    expect(q.get('bucket_day')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(q.get('project_id')).toBe(P)
    expect(Number(q.get('days'))).toBeLessThanOrEqual(90)
    // The panel's total is the cell's n: no reconciliation notice.
    const total = rowsTotal('executions', { suite: q.get('bucket_suite') ?? '', day: q.get('bucket_day') ?? '' }, 14)
    expect(total, 'the first cell is a measured one').toBeGreaterThan(0)
    await expect(panel.locator('[data-rows-count]')).toContainText(`${total} execution`)
    await expect(panel.locator('[data-rows-notice]')).toHaveCount(0)
    await expectHostileAsText(page, panel, 'rows panel')
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(page).not.toHaveURL(/[?&]rows=/)
    await expect(grid, 'focus back on the chart that opened the panel').toBeFocused()
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('Back closes the rows panel a cell opened (the selection is a history entry)', async ({ page }) => {
    await openTrends(page)
    await expectDrawn(sectionFrame(page, TRENDS.id, TRENDS.title), TRENDS.id)
    await keyboard(page, TRENDS.id).focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expectOneRowsPanel(page, /^Executions in /)
    await page.goBack()
    await expect(rowsPanels(page)).toHaveCount(0)
  })

  test('Coverage: the environment kind first; the kind selector switches to releases with ONE new read', async ({ page }) => {
    const { api, errors } = await openCoverage(page)
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH)).toEqual([`GET ${HEATMAP_PATH}?kind=suite_environment&project_id=${P}&days=30`])
    const kinds = section(page, ENV.id).locator('[data-heatmap-kinds]')
    await expect(kinds.getByRole('radio')).toHaveCount(2)
    await kinds.getByRole('radio', { name: 'Release', exact: true }).check()
    await expect(section(page, ENV.id)).toHaveAttribute('data-heatmap-kind', 'suite_release')
    await expectDrawn(sectionFrame(page, ENV.id, RELEASE_TITLE), 'release kind')
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH)).toEqual([
      `GET ${HEATMAP_PATH}?kind=suite_environment&project_id=${P}&days=30`,
      `GET ${HEATMAP_PATH}?kind=suite_release&project_id=${P}&days=30`,
    ])
    // A release cell's rows: suite + release KEYS (a release id or `unattributed`).
    const grid = keyboard(page, ENV.id)
    await grid.focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.getAll('group_by')).toEqual(['suite', 'release'])
    expect(q.get('bucket_release')).toMatch(/^([0-9a-f-]{36}|unattributed)$/)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('hostile names (markup, constructor, __proto__): in the row labels and the table view, as text; no pollution', async ({
    page,
  }) => {
    await openCoverage(page)
    const frame = sectionFrame(page, ENV.id, ENV.title)
    await expectDrawn(frame, ENV.id)
    await frame.getByRole('button', { name: 'View as table' }).click()
    await expect(frame.getByRole('table')).toContainText(HOSTILE_NAME)
    await expectHostileAsText(page, section(page, ENV.id), 'heatmap table')
    await expectNoPrototypePollution(page, 'heatmap rows named constructor and __proto__')
  })

  test('a 500 is the heatmap\'s own error frame (no toast): the coverage map still draws, Retry recovers', async ({ page }) => {
    let fail = true
    const handlers: ApiHandlers = [
      [HEATMAP_PATH, (request) => (fail ? respond(500, { detail: 'planted heatmap failure' }) : heatmapFixture(request))],
      ...COVERAGE_ON,
    ]
    const { api, errors } = await openCoverage(page, handlers)
    const frame = sectionFrame(page, ENV.id, ENV.title)
    await section(page, ENV.id).scrollIntoViewIfNeeded()
    await expect(frame).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    await expect(page.getByText('planted heatmap failure')).toHaveCount(0)
    // The map (its own tab since P3) still draws: the heatmap's error is the heatmap's alone.
    await coverageTab(page, 'Coverage map').click()
    await expectDrawn(sectionFrame(page, 'coverage-map', 'Test coverage map'), 'coverage map')
    await coverageTab(page, 'Env × release heatmap').click()
    await section(page, ENV.id).scrollIntoViewIfNeeded()
    await expect(frame).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    fail = false
    await frame.getByRole('button', { name: /retry/i }).click()
    await expectDrawn(frame, 'after Retry')
    await networkQuiet(page, api)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a 429 is a wait state that retries itself after Retry-After, then draws', async ({ page }) => {
    let limited = 1
    const handlers: ApiHandlers = [
      [
        HEATMAP_PATH,
        (request) =>
          limited-- > 0
            ? respond(429, { code: 'rate_limited', message: 'slow down', detail: 'slow down' }, { 'Retry-After': '1' })
            : heatmapFixture(request),
      ],
      ...COVERAGE_ON,
    ]
    const { api, errors } = await openCoverage(page, handlers)
    const frame = sectionFrame(page, ENV.id, ENV.title)
    await section(page, ENV.id).scrollIntoViewIfNeeded()
    await expect(frame.locator('[data-chart-error-kind="rate-limited"]')).toBeVisible({ timeout: 20_000 })
    await expectDrawn(frame, 'after the wait')
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH)).toHaveLength(2)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a stored 365-day window: every heatmap request asks for at most 90 days', async ({ page }) => {
    const { api } = await openCoverage(page, COVERAGE_ON, 365)
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await networkQuiet(page, api)
    expect(requestsTo(api, HEATMAP_PATH).length).toBeGreaterThan(0)
    expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the heatmap sections, every impact (${theme}): idle, a cell focused, the rows panel open`, async ({ page }) => {
      await openRollout(page, '/trends?tab=heatmap', { handlers: TRENDS_ON, ready: (p) => landmark(p, 'Trend metrics'), theme })
      await expectDrawn(sectionFrame(page, TRENDS.id, TRENDS.title), TRENDS.id)
      await expectNoBlockingViolations(page, theme, [], [`[data-catalogue-section="${TRENDS.id}"]`])
      await keyboard(page, TRENDS.id).focus()
      await page.keyboard.press('ArrowRight')
      await expect(section(page, TRENDS.id).locator('[data-mark-intent="rows"]')).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], [`[data-catalogue-section="${TRENDS.id}"]`])
      await page.keyboard.press('Enter')
      await expectOneRowsPanel(page, /^Executions in /)
      await expectNoBlockingViolations(page, theme, [], ['[data-side-panel]'])
    })
  }
})

test.describe('heatmaps, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('Trends: in its tab, the heatmap read waits until the section is near, and goes out before it is visible', async ({ page }) => {
    const { api, errors } = await openTrends(page)
    await proveLazyMount(page, api, {
      label: TRENDS.id,
      section: TRENDS.id,
      asked: () => requestsTo(api, HEATMAP_PATH).length,
      before: 0,
    })
    await expectDrawn(sectionFrame(page, TRENDS.id, TRENDS.title), TRENDS.id)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})

/** The fixture's own heatmap answer (the last handler for the path in `COVERAGE_ON`). */
function heatmapFixture(request: ApiRequest) {
  const own = COVERAGE_ON.find(([matcher]) => matcher === HEATMAP_PATH)
  if (!own) throw new Error('COVERAGE_ON answers no heatmap')
  return own[1](request)
}
