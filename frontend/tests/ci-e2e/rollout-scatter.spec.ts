/**
 * The test scatter (Wave 3, VIZ-506, FK4): p95 duration (log x) x failure
 * rate x executions, one point per test, from `/analytics/test-scatter`. On
 * Suite detail for the page's suite (`scatter-suite`; its inventory, keyboard
 * and one-panel tests are in `rollout-suite-detail.spec.ts`) and project-wide
 * on Failures (`scatter-project`, here).
 *
 * The selection is OUR filter of a data rectangle (spike S2: no ECharts
 * `large` mode, `brushSelected` never read), so a mouse drag and "Select slow
 * and flaky" list the same kind of thing in the selection list (the brush's
 * accessible twin); a list row's "View rows" opens the test's executions in
 * the page's ONE rows panel. Drag mode is a toggle, off by default, so a finger
 * scrolling over the plot never brushes.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, respond, type ApiHandlers } from '../lib/production-pages'
import {
  bringNear,
  expectDrawn,
  expectHostileAsText,
  expectOneRowsPanel,
  networkQuiet,
  openRollout,
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
  FAILURES_ON,
  HOSTILE_NAME,
  PROJECT_ID,
  SCATTER_PROJECT_TESTS,

  TEST_SCATTER_PATH,
  testScatterBody,
} from '../visual/production/fixtures'

const P = PROJECT_ID
const SCATTER = { id: 'scatter-project', title: 'Test duration vs failure rate' } as const

/** UX redesign P3: the project scatter is the Failures page's Scatter tab (`?tab=scatter`). */
const openFailures = (page: Page, handlers: ApiHandlers = FAILURES_ON, path = '/failures?tab=scatter') =>
  openRollout(page, path, { handlers, ready: (p) => landmark(p, 'Failure verdict') })

/** The page's section tabs (Groups · By suite · Scatter · Categories). */
const sectionTab = (page: Page, name: string) =>
  page.getByRole('tablist', { name: 'Failure analysis sections' }).getByRole('tab', { name, exact: true })

const scatter = (page: Page) => section(page, SCATTER.id)
const frame = (page: Page) => sectionFrame(page, SCATTER.id, SCATTER.title)

async function drawn(page: Page) {
  await bringNear(page, SCATTER.id)
  await expectDrawn(frame(page), SCATTER.id)
  await expect(scatter(page).locator('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready')
}

test.describe('Project scatter on Failures (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('project-wide: one read with no suite; every test placed; the exclusions stated', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await openFailures(page)
    await drawn(page)
    await networkQuiet(page, api)
    const reads = queryOf(api, TEST_SCATTER_PATH)
    expect(reads).toHaveLength(1)
    expect(reads[0].get('project_id')).toBe(P)
    expect(reads[0].getAll('suite_name')).toEqual([])
    expect(reads[0].get('min_executions')).toBe('5')
    await expect(scatter(page).locator('[data-scatter-excluded]')).toContainText('3 tests with fewer than 5 executions')
    // The quadrant key names every quadrant with its shape and count (colour is never alone).
    await expect(scatter(page).locator('[data-scatter-key] [data-quadrant]')).toHaveCount(4)
    await expectHostileAsText(page, scatter(page), 'project scatter')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test('drag mode is off by default; on, a mouse drag over the plot lists the tests in the rectangle', async ({ page }) => {
    await openFailures(page)
    await drawn(page)
    const drag = scatter(page).getByRole('button', { name: 'Drag to select' })
    await expect(drag).toHaveAttribute('aria-pressed', 'false')
    await drag.click()
    await expect(drag).toHaveAttribute('aria-pressed', 'true')
    const canvas = scatter(page).locator('[data-chart-type="scatter"] canvas').first()
    const box = await canvas.boundingBox()
    expect(box).not.toBeNull()
    if (!box) return
    await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.15)
    await page.mouse.down()
    for (let i = 1; i <= 12; i++) {
      await page.mouse.move(box.x + box.width * (0.35 + 0.5 * (i / 12)), box.y + box.height * (0.15 + 0.6 * (i / 12)))
    }
    await page.mouse.up()
    const count = scatter(page).locator('[data-scatter-selection-count]')
    await expect(count).toHaveText(/^\d+ tests? selected$/)
    const listed = Number(((await count.textContent()) ?? '0').split(' ')[0])
    expect(listed).toBeGreaterThan(0)
    expect(listed).toBeLessThanOrEqual(SCATTER_PROJECT_TESTS.length)
    await scatter(page).getByRole('button', { name: /Clear selection/ }).click()
    await expect(scatter(page).locator('[data-scatter-selection-count]')).toHaveCount(0)
  })

  test('the selection list: names sorted by code unit; a row\'s "View rows" opens ONE rows panel', async ({ page }) => {
    const { api, errors } = await openFailures(page)
    await drawn(page)
    await scatter(page).getByRole('button', { name: 'Select slow and flaky' }).click()
    const list = scatter(page).locator('[data-scatter-selection]')
    await expect(list.locator('[data-point-id]').first()).toBeVisible()
    await list.locator('[data-sort-key="label"]').click()
    const names = await list.locator('[data-point-id] th, [data-point-id] td:first-child').allTextContents()
    const sorted = [...names].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    expect(names.length).toBeGreaterThan(0)
    expect([names, [...names].reverse()]).toContainEqual(sorted)
    const first = list.locator('[data-view-rows]').first()
    const id = await first.getAttribute('data-view-rows')
    await first.click()
    await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('bucket_test')).toBe(id)
    expect(q.get('metric')).toBe('failure_rate')
    expect(q.get('top_n'), 'project-wide group_by=test needs a bound; rows ignore it').toBe('1')
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('every test excluded is said in the body, not drawn as an empty plot', async ({ page }) => {
    const handlers: ApiHandlers = [[TEST_SCATTER_PATH, () => testScatterBody(30, SCATTER_PROJECT_TESTS, { allExcluded: true })], ...FAILURES_ON]
    await openFailures(page, handlers)
    await bringNear(page, SCATTER.id)
    await expect(frame(page)).toContainText('No test can be placed on this chart', { timeout: 20_000 })
    await expect(frame(page)).toContainText('7 tests with fewer than 5 executions')
  })

  test('a 500 on the scatter is its own error (no toast); the groups still draw', async ({ page }) => {
    const handlers: ApiHandlers = [[TEST_SCATTER_PATH, () => respond(500, { detail: 'planted scatter failure' })], ...FAILURES_ON]
    await openFailures(page, handlers)
    await bringNear(page, SCATTER.id)
    await expect(frame(page)).toHaveAttribute('data-chart-state', 'error', { timeout: 20_000 })
    await expect(page.getByText('planted scatter failure')).toHaveCount(0)
    // P3: the groups are another tab; the scatter's error does not take them down.
    await sectionTab(page, 'Groups').click()
    await bringNear(page, 'failures-groups')
    await expectDrawn(sectionFrame(page, 'failures-groups', 'Failures grouped by error message'), 'groups')
  })

  test('hostile names in the table view and the selection list, as text', async ({ page }) => {
    await openFailures(page)
    await drawn(page)
    await frame(page).getByRole('button', { name: 'View as table' }).click()
    await expect(frame(page).getByRole('table')).toContainText(HOSTILE_NAME)
    await expectHostileAsText(page, scatter(page), 'scatter table')
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the project scatter, every impact (${theme}): idle, a point focused, the selection listed`, async ({ page }) => {
      await openRollout(page, '/failures?tab=scatter', { handlers: FAILURES_ON, ready: (p) => landmark(p, 'Failure verdict'), theme })
      await drawn(page)
      const only = [`[data-catalogue-section="${SCATTER.id}"]`]
      await expectNoBlockingViolations(page, theme, [], only)
      await scatter(page).locator('[data-chart-keyboard="scatter"]').focus()
      await page.keyboard.press('ArrowRight')
      await expect(scatter(page).locator('[data-mark-intent="rows"]')).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], only)
      await scatter(page).getByRole('button', { name: 'Select slow and flaky' }).click()
      await expectNoBlockingViolations(page, theme, [], only)
    })
  }
})

test.describe('Scatter, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  // UX redesign P3: the scatter is a tab, not the last of three stacked sections. Its intent is kept:
  // nothing of the scatter (placeholder, chunk, read) at load on the default tab, and ONE read once the
  // reader opens the Scatter tab and the scatter's own placeholder comes near.
  test('Failures: the project scatter is not asked at load (its tab is closed); it is once its tab opens', async ({
    page,
  }) => {
    const scatterChunk: string[] = []
    page.on('request', (request) => {
      if (/\/catalogue\/ScatterSection\.tsx|\/assets\/ScatterSection-/.test(new URL(request.url()).pathname)) scatterChunk.push(request.url())
    })
    const { api, errors } = await openFailures(page, FAILURES_ON, '/failures')
    await networkQuiet(page, api)
    await expect(page.locator('[data-lazy-section="scatter-project"]')).toHaveCount(0)
    await expect(section(page, SCATTER.id)).toHaveCount(0)
    expect(requestsTo(api, TEST_SCATTER_PATH)).toEqual([])
    expect(scatterChunk, 'the scatter chunk before its tab opened').toEqual([])
    await sectionTab(page, 'Scatter').click()
    await expect(page).toHaveURL(/[?&]tab=scatter(&|$)/)
    await bringNear(page, SCATTER.id)
    await expect.poll(() => requestsTo(api, TEST_SCATTER_PATH).length).toBe(1)
    expect(scatterChunk.length, 'the scatter chunk once its tab opened').toBeGreaterThan(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})
