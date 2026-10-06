/**
 * Cross-filtering (VIZ-603, P2): "Filter page by this" (the readout's button,
 * Shift+Enter, Shift-click) on the LEGACY scope, with no chart flag set (no
 * section asks one: Coverage and Suite detail's heatmap since Phase D S4,
 * Failures since S5). The multi-filter runtime it once also ran against is
 * gone (Phase D, M1-M3).
 *
 *   - a suite mark sets the page's own "Test suite" select, as the select
 *     spells the suite, REPLACING it; every read after it carries
 *     `suite_name`; it does not drill; "All suites" clears it;
 *   - a suite x release heatmap cell (Coverage) also sets the top bar's
 *     release (the select, `?release=`, `release_id` on the reads);
 *   - a status mark never offers it, and neither does Suite detail (the page
 *     has no suite filter: it IS one suite).
 *
 * Fixtures: `FAILURES_ON` / `COVERAGE_ON` / `SUITE_DETAIL_ON` (the runs list
 * gives the suite select its options: Auth, Checkout, Payments).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import {
  bringNear,
  expectDrawn,
  networkQuiet,
  observed,
  openRollout,
  queryOf,
  section,
  sectionFrame,
  walkToMark,
  watchConsoleErrors,
} from '../lib/rollout'
import { expectNoBlockingViolations } from '../lib/axe-gate'
import {
  CHART_DATA_PATH,
  COVERAGE_ON,
  FAILURES_ON,
  HEATMAP_PATH,
  HEATMAP_RELEASES,
  SUITE,
  SUITE_DETAIL_ON,
  suiteKey,
} from '../visual/production/fixtures'

const ready = (p: Page) => landmark(p, 'Failure verdict')
const DRILL = 'failures-drill'
const ENV = { id: 'heatmap-suite_environment', title: 'Suite pass rate by environment' } as const
const RELEASE_TITLE = 'Suite pass rate by release'
const FILTER = '[data-mark-intent="filter"]'

const open = (page: Page) => openRollout(page, '/failures', { handlers: FAILURES_ON, ready })

const ladder = (page: Page) => section(page, DRILL)
/** The page's legacy suite select (`SuiteFilterSelect`) and the top bar's release select (`ReleasePicker`). */
const suiteSelect = (page: Page) => page.getByRole('combobox', { name: 'Test suite' })
const releaseSelect = (page: Page) => page.getByRole('combobox', { name: 'Filter by release' })

async function levelZero(page: Page) {
  await bringNear(page, DRILL)
  await expect(ladder(page)).toHaveAttribute('data-drill-level', 'suites', { timeout: 20_000 })
  await expectDrawn(ladder(page).locator('[data-chart-frame]'), 'ladder')
}

/** Focus the bars and walk to the first suite; returns its label (from the drill button). */
async function focusFirstSuite(page: Page): Promise<string> {
  await ladder(page).locator('[data-chart-cursor]').focus()
  await page.keyboard.press('ArrowRight')
  const drill = ladder(page).locator('[data-mark-intent="drill"]')
  await expect(drill).toBeVisible()
  return ((await drill.textContent()) ?? '').replace(/^Drill into\s+/, '').trim()
}

/** The select's options that are `name` in any case: one, never a lower-cased twin. */
async function optionsNamed(page: Page, name: string): Promise<string[]> {
  const values = await suiteSelect(page).locator('option').evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value))
  return values.filter((value) => suiteKey(value) === suiteKey(name))
}

test.describe('Cross-filter on the legacy scope, multi-filters off (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('Failures ladder: Shift+Enter on a suite sets the Test suite select (as spelled) and the reads; it does not drill', async ({
    page,
  }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page)
    await levelZero(page)
    await expect(suiteSelect(page)).toHaveValue('')
    const label = await focusFirstSuite(page)
    await expect(ladder(page).locator(FILTER)).toHaveText('Filter page by this')
    const before = queryOf(api, CHART_DATA_PATH).length
    await page.keyboard.press('Shift+Enter')
    await expect(suiteSelect(page), 'the page suite is the option, as the select spells it').toHaveValue(label)
    expect(await optionsNamed(page, label), 'no lower-cased twin of the option').toEqual([label])
    await expect(page.getByText(`Page filtered by suite "${label}" (clear: "All suites").`).first()).toBeVisible()
    await expect(page, 'a filter is not a drill').not.toHaveURL(/[?&]drill=/)
    await expect(ladder(page)).toHaveAttribute('data-drill-level', 'suites')
    await networkQuiet(page, api)
    // The ladder re-reads level 0 under the page's new suite.
    const after = queryOf(api, CHART_DATA_PATH).slice(before)
    expect(after.length, 'the catalogue re-read under the filter').toBeGreaterThan(0)
    expect(after.every((q) => q.getAll('suite_name').map(suiteKey).includes(suiteKey(label)))).toBe(true)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test("the readout's button does the same; filtering again is a no-op; \"All suites\" clears it", async ({ page }) => {
    const { api } = await open(page)
    await levelZero(page)
    const first = await focusFirstSuite(page)
    await ladder(page).locator(FILTER).click()
    await expect(suiteSelect(page)).toHaveValue(first)
    // Under the filter the ladder re-reads level 0 for that one suite (wait for it: the previous bars stay
    // on screen while it loads); filtering by it again changes nothing.
    await expect
      .poll(() => queryOf(api, CHART_DATA_PATH, (q) => q.getAll('suite_name').map(suiteKey).includes(suiteKey(first))).length)
      .toBeGreaterThan(0)
    await networkQuiet(page, api)
    await levelZero(page)
    await focusFirstSuite(page)
    const reads = observed(api).length
    await page.keyboard.press('Shift+Enter')
    await expect(page.getByText(`The page is already filtered by suite "${first}".`).first()).toBeVisible()
    await networkQuiet(page, api)
    expect(observed(api).slice(reads), 'nothing re-read').toEqual([])
    await expect(suiteSelect(page)).toHaveValue(first)
    await expect(page).not.toHaveURL(/[?&]drill=/)
    // "All suites" is the undo: the reads lose their suite.
    const cleared = queryOf(api, CHART_DATA_PATH).length
    await suiteSelect(page).selectOption('')
    await expect(suiteSelect(page)).toHaveValue('')
    await expect.poll(() => queryOf(api, CHART_DATA_PATH).length).toBeGreaterThan(cleared)
    await networkQuiet(page, api)
    expect(queryOf(api, CHART_DATA_PATH).slice(cleared).every((q) => q.getAll('suite_name').length === 0)).toBe(true)
  })

  test('a status mark never offers the page filter', async ({ page }) => {
    await open(page)
    await page.goto('/failures?drill=suite~payments')
    await bringNear(page, DRILL)
    await expect(ladder(page)).toHaveAttribute('data-drill-level', 'statuses', { timeout: 20_000 })
    await ladder(page).locator('[data-chart-cursor]').focus()
    await page.keyboard.press('ArrowRight')
    await expect(ladder(page).locator('[data-mark-intent="drill"]')).toBeVisible()
    await expect(ladder(page).locator(FILTER)).toHaveCount(0)
  })

  test('Coverage: a suite x release cell sets the top-bar release (select, ?release=, reads) AND the Test suite select', async ({
    page,
  }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await openRollout(page, '/coverage', { handlers: COVERAGE_ON, ready: (p) => landmark(p, 'Run cadence') })
    await expectDrawn(sectionFrame(page, ENV.id, ENV.title), ENV.id)
    await expect(releaseSelect(page)).toHaveValue('')
    const kinds = section(page, ENV.id).locator('[data-heatmap-kinds]')
    await kinds.getByRole('radio', { name: 'Release', exact: true }).check()
    await expectDrawn(sectionFrame(page, ENV.id, RELEASE_TITLE), 'release kind')
    await networkQuiet(page, api)
    const grid = section(page, ENV.id).locator('[data-chart-keyboard="heatmap"]')
    await grid.focus()
    await walkToMark(page, ENV.id)
    await expect(section(page, ENV.id).locator(FILTER)).toHaveText('Filter page by this')
    const before = queryOf(api, HEATMAP_PATH).length
    await page.keyboard.press('Shift+Enter')
    // The release: one of the matrix's columns, in the picker, the address bar and the next reads.
    const releaseIds: string[] = HEATMAP_RELEASES.map(([id]) => id)
    await expect(releaseSelect(page)).not.toHaveValue('')
    const releaseId = await releaseSelect(page).inputValue()
    expect(releaseIds).toContain(releaseId)
    await expect(page).toHaveURL(new RegExp(`[?&]release=${releaseId}(&|$)`))
    // The suite: the select's own spelling (the cell's key is lower-cased), never a twin option.
    const suite = await suiteSelect(page).inputValue()
    expect(suite).not.toBe('')
    expect(suite, 'the option, not the lower-cased key').not.toBe(suite.toLowerCase())
    expect(await optionsNamed(page, suite)).toEqual([suite])
    await expect(page.getByText(new RegExp(`^Page filtered by suite "${suite}" and release ".+" \\(clear: "All suites", "All releases"\\)\\.$`)).first()).toBeVisible()
    await expect.poll(() => queryOf(api, HEATMAP_PATH).length).toBeGreaterThan(before)
    await networkQuiet(page, api)
    const after = queryOf(api, HEATMAP_PATH).slice(before)
    expect(after.every((q) => q.get('release_id') === releaseId && q.getAll('suite_name').map(suiteKey).includes(suiteKey(suite)))).toBe(true)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test('Suite detail: no page suite filter, so no cell offers one', async ({ page }) => {
    await openRollout(page, `/coverage/suite?name=${SUITE}&days=30`, {
      handlers: SUITE_DETAIL_ON,
      ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    })
    const id = 'heatmap-test_run'
    await expectDrawn(sectionFrame(page, id, 'Test results by run'), id)
    await section(page, id).locator('[data-chart-keyboard="heatmap"]').focus()
    await walkToMark(page, id)
    await expect(section(page, id).locator('[data-mark-intent="rows"]')).toBeVisible()
    await expect(page.locator(FILTER)).toHaveCount(0)
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the ladder with the filter action offered, every impact (${theme})`, async ({ page }) => {
      await openRollout(page, '/failures', { handlers: FAILURES_ON, ready, theme })
      await levelZero(page)
      await focusFirstSuite(page)
      await expect(ladder(page).locator(FILTER)).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], [`[data-catalogue-section="${DRILL}"]`])
    })
  }
})
