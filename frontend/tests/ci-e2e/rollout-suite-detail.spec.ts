/**
 * /coverage/suite and its catalogue (Wave 2.6, VIZ-408; plan 2.2 "Suite
 * detail", 5.3). The pass-rate frame has trend analysis and zoom, drawn from
 * the page's own points, so they add NO request. Both frames sit at the 240 px
 * floor (VIZ-106). Since Phase D (S3) the page asks no `viz_chart_data_api`
 * of its own: there is no flag-off page.
 *
 * Wave 3 (plan 2.5, FK4 + FK1): with `viz_advanced_charts` (and, until S4,
 * `viz_chart_data_api`) on, the page's composite (`SuiteDetailAdvanced`, now
 * always mounted) adds two lazy sections for its one suite: the test x run
 * status heatmap (`heatmap-test_run`, `/analytics/heatmap?kind=test_run`) and
 * the test scatter (`scatter-suite`, `/analytics/test-scatter`), plus the
 * sections' unfiltered run probe. The composite asks both lookups whatever
 * their answers. Both sections share the page's one `rows` URL key: only the
 * section that opened a selection may answer it (REQUESTS FK4-1), so every
 * rows test asserts ONE panel.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { frameByHeading } from '../lib/production-pages'
import {
  daysOnTheWire,
  expectDrawn,
  expectHostileAsText,
  expectInventory,
  expectNoErrorFrame,
  expectNoPrototypePollution,
  expectNoTextEscapes,
  expectOneRowsPanel,
  mountEverySection,
  networkQuiet,
  openRollout,
  proveLazyMount,
  queryOf,
  requestsTo,
  rowsPanels,
  RUN_PROBE,
  section,
  sectionFrame,
  SHELL_BASE,
  SHORT_VIEWPORT,
  watchConsoleErrors,
} from '../lib/rollout'
import {
  ADVANCED_ON,
  CHART_ROWS_PATH,
  HEATMAP_PATH,
  HOSTILE_NAME,
  PROJECT_ID,
  SCATTER_SUITE_TESTS,
  SUITE,
  SUITE_DETAIL_ON,
  TEST_SCATTER_PATH,
  withoutObjectMemberLabels,
} from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const ready = (p: Page) => p.getByRole('heading', { name: /^Run history/ })
const PATH = `/coverage/suite?name=${SUITE}&days=30`

/** The page's own reads (without the shell). */
const PAGE_READS = [
  `GET /api/v1/analytics/suite-detail?project_id=${P}&suite_name=${SUITE}&days=30`,
  `GET /api/v1/test-management/suites/${SUITE}/trend?project_id=${P}&days=30`,
  `GET /api/v1/suites?project_id=${P}`,
]

/**
 * One cold load: the shell (`SHELL_BASE`: the page itself asks no flag since
 * S3), the page's reads, and the composite's two lookups (`useAdvancedRollout`,
 * asked whatever the answers; S4 removes them), and nothing else.
 */
const INVENTORY = [
  ...SHELL_BASE,
  `GET /api/v1/feature-flags/viz_chart_data_api/status?project_id=${P}`,
  `GET /api/v1/feature-flags/viz_advanced_charts/status?project_id=${P}`,
  ...PAGE_READS,
]

const HEATMAP_LINE = `GET ${HEATMAP_PATH}?kind=test_run&project_id=${P}&days=30&suite_name=${SUITE}`
const SCATTER_LINE = `GET ${TEST_SCATTER_PATH}?min_executions=5&order=failures&project_id=${P}&days=30&suite_name=${SUITE}`

/** The drawn scatter asks whether it may offer its 3D view (VIZ-508; answered off by the harness). */
const THREE_D_LOOKUP = `GET /api/v1/feature-flags/viz_three_d/status?project_id=${P}`

/** Both flags, every section near: + the test x run heatmap, the scatter (and its 3D lookup) and the run probe. */
const INVENTORY_BOTH = [...INVENTORY, HEATMAP_LINE, SCATTER_LINE, THREE_D_LOOKUP, RUN_PROBE]

const HEATMAP = { id: 'heatmap-test_run', title: 'Test results by run' } as const
const SCATTER = { id: 'scatter-suite', title: 'Test duration vs failure rate' } as const

async function bodyHeight(frame: Locator): Promise<number> {
  return (await frame.locator('[data-chart-body]').boundingBox())?.height ?? 0
}

test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('every viz flag off: the pass-rate frame has trend analysis and zoom, and nothing is asked for it', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, ready })
  const passRate = sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/)
  await expectDrawn(passRate, 'suite-pass-rate')
  await expect(passRate.locator('[data-trend-controls]')).toBeVisible()
  await expect(passRate.locator('[data-chart-brush]')).toBeVisible()
  // Only that frame: Run history is untouched and outside any section.
  const history = frameByHeading(page, /^Run history/)
  await expectDrawn(history, 'run history')
  await expect(history.locator('[data-trend-controls], [data-chart-brush]')).toHaveCount(0)
  await expect(page.locator('[data-catalogue-section]')).toHaveCount(1)
  // The VIZ-106 floor.
  expect(await bodyHeight(history), 'run history body').toBeGreaterThanOrEqual(240)
  expect(await bodyHeight(passRate), 'pass-rate body').toBeGreaterThanOrEqual(240)
  await expectNoErrorFrame(page)
  await expectNoTextEscapes(page, 'Suite detail at 1280')
  await networkQuiet(page, api)
  expectInventory(api, errors, INVENTORY, 'Suite detail, every flag off')
})

// Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
// catalogue section (the pass-rate frame with its overlays and brush), in
// both themes the harness renders. No allowlist: R2 measured 0 violations.
for (const theme of ['signal', 'lab'] as const) {
  test(`axe: the catalogue section, every impact, no violation (${theme})`, async ({ page }) => {
    const { api } = await openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, ready, theme })
    await expectDrawn(sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/), 'suite-pass-rate')
    await networkQuiet(page, api)
    await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
  })
}

test('?days=365 in the URL: the page falls back to its own window, and nothing on the wire asks for more than 90', async ({
  page,
}) => {
  const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=365`, {
    handlers: SUITE_DETAIL_ON,
    ready,
  })
  await expectDrawn(sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/), 'suite-pass-rate')
  await networkQuiet(page, api)
  expect(daysOnTheWire(api).filter(({ days }) => !(days >= 1 && days <= 90)), 'requests over 90 days').toEqual([])
  expectInventory(api, errors, INVENTORY, 'Suite detail at ?days=365')
})

// ── Wave 3: the test x run heatmap and the test scatter (both flags) ──────

test.describe('Suite detail, both flags (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  const open = (page: Page, flags: Record<string, boolean> = ADVANCED_ON) =>
    openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, flags, ready })

  test("both sections drawn for the page's suite; exactly their reads, once each", async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page)
    await mountEverySection(page, api)
    const heatmap = sectionFrame(page, HEATMAP.id, HEATMAP.title)
    const scatter = sectionFrame(page, SCATTER.id, SCATTER.title)
    await expectDrawn(heatmap, HEATMAP.id)
    await expectDrawn(scatter, SCATTER.id)
    // A status matrix: no colour-scale fit.
    await expect(section(page, HEATMAP.id)).toHaveAttribute('data-heatmap-kind', 'test_run')
    await expect(heatmap.locator('[data-heatmap-fit]')).toHaveCount(0)
    await expect(heatmap.locator('canvas').first()).toBeVisible()
    await expect(scatter.locator('canvas').first()).toBeVisible()
    // The footer states every exclusion (fixtures: 3 below 5 executions, 1 with no duration, 2 with no rate).
    await expect(scatter.locator('[data-scatter-excluded]')).toContainText('3 tests with fewer than 5 executions')
    await expectHostileAsText(page, page.locator('[data-suite-advanced]'), 'Suite detail sections')
    await expectNoErrorFrame(page)
    await networkQuiet(page, api)
    expectInventory(api, errors, INVENTORY_BOTH, 'Suite detail, both flags')
    expect(console).toEqual([])
  })

  // The advanced gate stays until S4: with its flags off, the composite draws
  // nothing and asks no Wave 3 read.
  test('viz_advanced_charts off: no Wave 3 section, no Wave 3 read', async ({ page }) => {
    const { api } = await open(page, {})
    await expectDrawn(sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/), 'suite-pass-rate')
    await networkQuiet(page, api)
    await expect(page.locator('[data-suite-advanced], [data-lazy-section]')).toHaveCount(0)
    expect([...requestsTo(api, HEATMAP_PATH), ...requestsTo(api, TEST_SCATTER_PATH)]).toEqual([])
  })

  test("test x run: Enter on a cell opens ONE rows panel, the test's executions (its KEY, top_n bound)", async ({ page }) => {
    const { api, errors } = await open(page)
    await expectDrawn(sectionFrame(page, HEATMAP.id, HEATMAP.title), HEATMAP.id)
    const grid = section(page, HEATMAP.id).locator('[data-chart-keyboard="heatmap"]')
    await grid.focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric')).toBe('executions')
    expect(q.getAll('group_by')).toEqual(['test'])
    expect(q.get('bucket_test')).toMatch(/^fp-auth-/)
    expect(q.get('top_n')).toBe('60')
    expect(q.getAll('suite_name')).toEqual([SUITE])
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(grid).toBeFocused()
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('scatter: "Select slow and flaky" lists exactly the quadrant; Enter on a point opens ONE rows panel', async ({ page }) => {
    const { api, errors } = await open(page)
    const frame = sectionFrame(page, SCATTER.id, SCATTER.title)
    await expectDrawn(frame, SCATTER.id)
    const medians = medianOf(SCATTER_SUITE_TESTS)
    const salient = SCATTER_SUITE_TESTS.filter((t) => t.x > medians.x && t.y > medians.y)
    expect(salient.length, 'the fixture has a slow-and-flaky quadrant').toBeGreaterThan(0)
    await section(page, SCATTER.id).getByRole('button', { name: 'Select slow and flaky' }).click()
    await expect(section(page, SCATTER.id).locator('[data-scatter-selection-count]')).toHaveText(`${salient.length} tests selected`)
    // The keyboard path: focus the plot, walk to a point, Enter opens its executions.
    const plot = section(page, SCATTER.id).locator('[data-chart-keyboard="scatter"]')
    await plot.focus()
    await page.keyboard.press('ArrowRight')
    await expect(section(page, SCATTER.id).locator('[data-mark-intent="rows"]')).toBeVisible()
    await page.keyboard.press('Enter')
    const panel = await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric'), "the rate's evaluated executions: they reconcile with the point's n").toBe('failure_rate')
    expect(q.getAll('group_by')).toEqual(['test'])
    const point = SCATTER_SUITE_TESTS.find((t) => t.id === q.get('bucket_test'))
    expect(point, 'the selector is a point id (fingerprint)').toBeDefined()
    await expect(panel.locator('[data-rows-count]')).toContainText(`${point?.n} execution`)
    await expect(panel.locator('[data-rows-notice]')).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    await expect(plot).toBeFocused()
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test("a shared link to a scatter point's rows opens ONE panel (the scatter's), never the heatmap's too", async ({ page }) => {
    const { api } = await open(page)
    await expectDrawn(sectionFrame(page, SCATTER.id, SCATTER.title), SCATTER.id)
    await section(page, SCATTER.id).locator('[data-chart-keyboard="scatter"]').focus()
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expectOneRowsPanel(page, /^Executions in /)
    await page.goto(page.url())
    await mountEverySection(page, api)
    await expectOneRowsPanel(page, /^Executions in /)
  })

  test('hostile test names: in the heatmap table and the scatter table, as text', async ({ page }) => {
    await open(page)
    const heatmap = sectionFrame(page, HEATMAP.id, HEATMAP.title)
    await expectDrawn(heatmap, HEATMAP.id)
    await heatmap.getByRole('button', { name: 'View as table' }).click()
    await expect(heatmap.getByRole('table')).toContainText(HOSTILE_NAME)
    const scatter = sectionFrame(page, SCATTER.id, SCATTER.title)
    await expectDrawn(scatter, SCATTER.id)
    await scatter.getByRole('button', { name: 'View as table' }).click()
    await expect(scatter.getByRole('table')).toContainText(HOSTILE_NAME)
    await expectHostileAsText(page, page.locator('[data-suite-advanced]'), 'tables')
    await expectNoPrototypePollution(page, 'Suite detail with tests named constructor and __proto__')
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the two sections, every impact (${theme})`, async ({ page }) => {
      // Without the Object-member names: a polluted page breaks axe itself (the hostile test owns that defect).
      const handlers = withoutObjectMemberLabels(SUITE_DETAIL_ON)
      const { api } = await openRollout(page, PATH, { handlers, flags: ADVANCED_ON, ready, theme })
      await mountEverySection(page, api)
      await expectDrawn(sectionFrame(page, HEATMAP.id, HEATMAP.title), HEATMAP.id)
      await expectDrawn(sectionFrame(page, SCATTER.id, SCATTER.title), SCATTER.id)
      const both = [`[data-catalogue-section="${HEATMAP.id}"]`, `[data-catalogue-section="${SCATTER.id}"]`]
      await expectNoBlockingViolations(page, theme, [], both)
      await section(page, SCATTER.id).getByRole('button', { name: 'Select slow and flaky' }).click()
      await expectNoBlockingViolations(page, theme, [], [`[data-catalogue-section="${SCATTER.id}"]`])
    })
  }
})

test.describe('Suite detail, both flags, a short screen (1280 x 600)', () => {
  test.use({ viewport: { ...SHORT_VIEWPORT }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: the heatmap read waits until it is near, and goes out before it is visible', async ({ page }) => {
    const { api, errors } = await openRollout(page, PATH, { handlers: SUITE_DETAIL_ON, flags: ADVANCED_ON, ready })
    await proveLazyMount(page, api, {
      label: HEATMAP.id,
      section: HEATMAP.id,
      asked: () => requestsTo(api, HEATMAP_PATH).length,
      before: 0,
    })
    // The scatter is further down: still unasked.
    await networkQuiet(page, api)
    expect(requestsTo(api, TEST_SCATTER_PATH), 'the scatter asked while far below the fold').toEqual([])
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })
})

/** Python's `statistics.median` over each axis, two decimals (the server's medians). */
function medianOf(points: readonly { x: number; y: number }[]) {
  const median = (values: number[]) => {
    const sorted = [...values].sort((a, b) => a - b)
    const mid = Math.floor(sorted.length / 2)
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
  }
  const two = (value: number) => Math.round(value * 100) / 100
  return { x: two(median(points.map((p) => p.x))), y: two(median(points.map((p) => p.y))) }
}
