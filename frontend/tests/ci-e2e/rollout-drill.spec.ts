/**
 * The Failures drill ladder (Wave 3, VIZ-602, FK5), no flag asked (Phase D,
 * S5): level 0
 * "Results by suite" (chart-data executions by suite x status, stacked), a
 * suite's statuses, a status's suites, and the leaf (a suite's tests with one
 * status, `top_n=20`), each level a history entry in the page URL
 * (`drill=suite~<key>&drill=status~<status>`), each level one chart-data read.
 *
 * Keyboard (the EPIC's path): Tab to the bars, arrows walk them, Enter drills
 * (focus follows to the new level), Back returns, the breadcrumb's root link
 * clears the path; the leaf's only action is "View rows" (ONE rows panel,
 * reconciling with the bar). A pointer click on a stacked SEGMENT drills
 * suite AND status in one push. A hand-edited link naming a level the page
 * cannot open is cut and SAID (`data-drill-notice`). The page filter
 * (Shift+Enter, "Filter page by this") is `rollout-cross-filter.spec.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import {
  bringNear,
  expectDrawn,
  expectHostileAsText,
  expectOneRowsPanel,
  networkQuiet,
  openRollout,
  queryOf,
  rowsPanels,
  section,
  sectionFrame,
  watchConsoleErrors,
} from '../lib/rollout'
import { expectNoBlockingViolations } from '../lib/axe-gate'
import {
  CHART_DATA_PATH,
  CHART_ROWS_PATH,
  FAILURES_ON,
  HOSTILE_NAME,
  LADDER_SUITES,
  ladderTests,
  PROJECT_ID,
  suiteKey,
} from '../visual/production/fixtures'

const P = PROJECT_ID
const ready = (p: Page) => landmark(p, 'Failure verdict')
const DRILL = 'failures-drill'

const open = (page: Page, path = '/failures') => openRollout(page, path, { handlers: FAILURES_ON, ready })

const ladder = (page: Page) => section(page, DRILL)
const frame = (page: Page) => ladder(page).locator('[data-chart-frame]')
const cursor = (page: Page) => ladder(page).locator('[data-chart-cursor]')

/** Bring the ladder near and wait for its level to be drawn. */
async function drawn(page: Page, level: string) {
  await bringNear(page, DRILL)
  await expect(ladder(page)).toHaveAttribute('data-drill-level', level, { timeout: 20_000 })
  await expectDrawn(frame(page), `ladder at ${level}`)
}

/** The chart-data reads of the ladder, parsed (the page's other reads are not chart-data). */
const ladderReads = (api: Parameters<typeof queryOf>[0]) => queryOf(api, CHART_DATA_PATH)

test.describe('Drill ladder (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('level 0: results by suite, one read; the breadcrumb is the root alone', async ({ page }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page)
    await drawn(page, 'suites')
    await expect(sectionFrame(page, DRILL, 'Results by suite')).toHaveCount(1)
    await expect(ladder(page).locator('[data-drill-breadcrumb]')).toContainText('All suites')
    await networkQuiet(page, api)
    const reads = ladderReads(api)
    expect(reads.map((q) => [q.get('metric'), q.getAll('group_by').join(',')])).toEqual([['executions', 'suite,status']])
    expect(reads[0].get('project_id')).toBe(P)
    await expectHostileAsText(page, ladder(page), 'ladder level 0')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test('keyboard: Enter on a suite drills (URL push, one read for that suite, focus follows), Enter again reaches the leaf, Back returns', async ({
    page,
  }) => {
    const { api, errors } = await open(page)
    await drawn(page, 'suites')
    await cursor(page).focus()
    await page.keyboard.press('ArrowRight')
    const drillButton = ladder(page).locator('[data-mark-intent="drill"]')
    await expect(drillButton).toBeVisible()
    const name = ((await drillButton.textContent()) ?? '').replace(/^Drill into\s+/, '').trim()
    const suite = LADDER_SUITES.find((s) => s.label === name)
    expect(suite, `the focused bar "${name}" is a fixture suite`).toBeDefined()
    const key = suiteKey(suite?.label ?? '')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/[?&]drill=suite(%7E|~)/)
    await drawn(page, 'statuses')
    await expect(cursor(page), 'focus follows the drill').toBeFocused()
    const l1 = ladderReads(api).filter((q) => q.getAll('group_by').join(',') === 'status')
    expect(l1).toHaveLength(1)
    expect(l1[0].get('metric')).toBe('executions')
    expect(l1[0].getAll('suite_name').map(suiteKey), 'the level\'s suite, as the page filter').toEqual([key])
    await expect(ladder(page).locator('[data-drill-breadcrumb]')).toContainText(name)
    // The suite's statuses: walk to one and Enter -> the leaf (its tests with that status).
    await page.keyboard.press('ArrowRight')
    await expect(drillButton).toBeVisible()
    await page.keyboard.press('Enter')
    await drawn(page, 'tests')
    const leaf = ladderReads(api).filter((q) => q.getAll('group_by').join(',') === 'test')
    expect(leaf).toHaveLength(1)
    expect(leaf[0].get('top_n')).toBe('20')
    expect(leaf[0].getAll('suite_name').map(suiteKey)).toEqual([key])
    // Back: the suite level again, from the cache (no new read for it).
    const before = ladderReads(api).length
    await page.goBack()
    await drawn(page, 'statuses')
    await page.goBack()
    await drawn(page, 'suites')
    await networkQuiet(page, api)
    expect(ladderReads(api).length, 'Back reads nothing it already has').toBe(before)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('the leaf: "View rows" is its only action; ONE rows panel, the test with that status, reconciling', async ({ page }) => {
    const leafSuite = 'payments'
    const { api, errors } = await open(page, `/failures?drill=suite~${leafSuite}&drill=status~failed`)
    await drawn(page, 'tests')
    await cursor(page).focus()
    await page.keyboard.press('ArrowRight')
    const actions = ladder(page).locator('[data-mark-actions] [data-mark-intent]')
    await expect(actions).toHaveCount(1)
    await expect(actions).toHaveAttribute('data-mark-intent', 'rows')
    await page.keyboard.press('Enter')
    const panel = await expectOneRowsPanel(page, /^Executions in /)
    const [q] = queryOf(api, CHART_ROWS_PATH)
    expect(q.get('metric')).toBe('failed')
    expect(q.getAll('group_by')).toEqual(['test', 'status'])
    expect(q.get('bucket_status')).toBe('failed')
    expect(q.getAll('suite_name').map(suiteKey)).toEqual([leafSuite])
    const test = ladderTests(leafSuite, 'failed').find((t) => t.fingerprint === q.get('bucket_test'))
    expect(test, 'the selector is the bar\'s test KEY').toBeDefined()
    await expect(panel.locator('[data-rows-count]')).toContainText(`${test?.count} execution`)
    await expect(panel.locator('[data-rows-notice]')).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(rowsPanels(page)).toHaveCount(0)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('a pointer click on a stacked segment drills suite AND status in one push', async ({ page }) => {
    await open(page)
    await drawn(page, 'suites')
    const segment = frame(page).locator('.recharts-bar-rectangle path, .recharts-rectangle').first()
    await segment.click()
    await expect(page).toHaveURL(/drill=suite(%7E|~)[^&]+&drill=status(%7E|~)/)
    await drawn(page, 'tests')
    await page.goBack()
    await drawn(page, 'suites')
  })

  test('the breadcrumb\'s root link clears the path and puts focus on the chart', async ({ page }) => {
    await open(page, '/failures?drill=suite~payments')
    await drawn(page, 'statuses')
    await ladder(page).locator('[data-drill-breadcrumb]').getByRole('link', { name: 'All suites' }).click()
    await expect(page).not.toHaveURL(/[?&]drill=/)
    await drawn(page, 'suites')
    await expect(cursor(page)).toBeFocused()
  })

  test('a hand-edited link naming a status that does not exist is cut and said, never a crash', async ({ page }) => {
    const { api, errors } = await open(page, '/failures?drill=status~not-a-status')
    await drawn(page, 'suites')
    await expect(ladder(page).locator('[data-drill-notice]')).not.toHaveText('')
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
  })

  test('the hostile suite: drilled by its KEY, labelled as text', async ({ page }) => {
    const { api } = await open(page, `/failures?drill=${encodeURIComponent(`suite~${suiteKey(HOSTILE_NAME)}`)}`)
    await drawn(page, 'statuses')
    const l1 = ladderReads(api).filter((q) => q.getAll('group_by').join(',') === 'status')
    expect(l1.map((q) => q.getAll('suite_name').map(suiteKey))).toEqual([[suiteKey(HOSTILE_NAME)]])
    await expectHostileAsText(page, ladder(page), 'hostile suite level')
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the ladder, every impact (${theme}): level 0, a bar focused, the leaf`, async ({ page }) => {
      await openRollout(page, '/failures', { handlers: FAILURES_ON, ready, theme })
      await drawn(page, 'suites')
      const only = [`[data-catalogue-section="${DRILL}"]`]
      await expectNoBlockingViolations(page, theme, [], only)
      await cursor(page).focus()
      await page.keyboard.press('ArrowRight')
      await expect(ladder(page).locator('[data-mark-actions]')).toBeVisible()
      await expectNoBlockingViolations(page, theme, [], only)
      await page.goto('/failures?drill=suite~payments&drill=status~failed')
      await drawn(page, 'tests')
      await expectNoBlockingViolations(page, theme, [], only)
    })
  }
})
