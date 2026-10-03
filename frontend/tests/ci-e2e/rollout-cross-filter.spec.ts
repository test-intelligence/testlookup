/**
 * Cross-filtering (Wave 3, VIZ-603, FK5): a suite mark on the Failures drill
 * ladder can filter the PAGE ("Filter page by this", Shift+Enter, Shift-click)
 * — only with `viz_multi_filters` on, only for suite (and release) marks, by
 * APPENDING to the page's suite selection (the existing stores: the chip,
 * `suites=` in the address bar, every catalogue read's `suite_name`).
 *
 * The flag matrix of the action itself: with `viz_multi_filters` off the
 * action is ABSENT (no button, Shift+Enter is a plain drill); a status mark
 * never offers it. Fixtures: `FAILURES_ON` plus the multi-filter runtime's
 * own reads (answered here).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type ApiHandlers } from '../lib/production-pages'
import { bringNear, expectDrawn, networkQuiet, openRollout, queryOf, section, watchConsoleErrors } from '../lib/rollout'
import { expectNoBlockingViolations } from '../lib/axe-gate'
import { ADVANCED_ON, CHART_DATA_PATH, FAILURES_ON, LADDER_SUITES, PROJECT_ID, suiteKey } from '../visual/production/fixtures'

const ready = (p: Page) => landmark(p, 'Failure verdict')
const DRILL = 'failures-drill'
const MULTI_ON = { ...ADVANCED_ON, viz_multi_filters: true }

/** The suite picker's options (the multi-filter runtime asks the project's suites). */
const SUITES: ApiHandlers = [
  [
    '/api/v1/suites',
    () => ({
      items: LADDER_SUITES.map((s, i) => ({
        id: `66666666-6666-4666-8666-${String(i).padStart(12, '0')}`,
        project_id: PROJECT_ID,
        name: s.label,
        description: null,
        created_at: '2026-02-01T00:00:00Z',
      })),
      total: LADDER_SUITES.length,
      page: 1,
      size: 100,
      pages: 1,
    }),
  ],
]

const open = (page: Page, flags: Record<string, boolean>) =>
  openRollout(page, '/failures', { handlers: [...SUITES, ...FAILURES_ON], flags, ready })

const ladder = (page: Page) => section(page, DRILL)

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

test.describe('Cross-filter, both flags (1280 x 4000)', () => {
  test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('viz_multi_filters on: Shift+Enter on a suite APPENDS it to the page filter (URL, reads), it does not drill', async ({
    page,
  }) => {
    const console = watchConsoleErrors(page)
    const { api, errors } = await open(page, MULTI_ON)
    await levelZero(page)
    const label = await focusFirstSuite(page)
    await expect(ladder(page).locator('[data-mark-intent="filter"]')).toHaveText('Filter page by this')
    const before = queryOf(api, CHART_DATA_PATH).length
    await page.keyboard.press('Shift+Enter')
    await expect(page, 'the page filter is in the address bar').toHaveURL(new RegExp(`[?&]suites=${encodeURIComponent(label)}(&|$)`))
    await expect(page, 'a filter is not a drill').not.toHaveURL(/[?&]drill=/)
    await expect(ladder(page)).toHaveAttribute('data-drill-level', 'suites')
    await networkQuiet(page, api)
    // The ladder re-reads level 0 under the new page scope: its suite as the filter.
    const after = queryOf(api, CHART_DATA_PATH).slice(before)
    expect(after.length, 'the catalogue re-read under the filter').toBeGreaterThan(0)
    expect(after.every((q) => q.getAll('suite_name').map(suiteKey).includes(suiteKey(label)))).toBe(true)
    expect(api.unhandled).toEqual([])
    expect(errors).toEqual([])
    expect(console).toEqual([])
  })

  test("the readout's button does the same; a suite already in the filter is not written twice", async ({ page }) => {
    const { api } = await open(page, MULTI_ON)
    await levelZero(page)
    const first = await focusFirstSuite(page)
    await ladder(page).locator('[data-mark-intent="filter"]').click()
    await expect(page).toHaveURL(new RegExp(`suites=${encodeURIComponent(first)}`))
    // Under the filter the ladder re-reads level 0 for that one suite (wait for it: the previous bars stay
    // on screen while it loads); filtering by it again changes nothing.
    await expect
      .poll(() => queryOf(api, CHART_DATA_PATH, (q) => q.getAll('suite_name').map(suiteKey).includes(suiteKey(first))).length)
      .toBeGreaterThan(0)
    await networkQuiet(page, api)
    await levelZero(page)
    await focusFirstSuite(page)
    await page.keyboard.press('Shift+Enter')
    await page.waitForTimeout(300)
    expect(new URL(page.url()).searchParams.getAll('suites')).toEqual([first])
  })

  test('viz_multi_filters off: the action is absent; Shift+Enter drills like Enter', async ({ page }) => {
    await open(page, ADVANCED_ON)
    await levelZero(page)
    await focusFirstSuite(page)
    await expect(ladder(page).locator('[data-mark-intent="filter"]')).toHaveCount(0)
    await page.keyboard.press('Shift+Enter')
    await expect(page).not.toHaveURL(/[?&]suites=/)
  })

  test('a status mark never offers the page filter', async ({ page }) => {
    await open(page, MULTI_ON)
    await page.goto('/failures?drill=suite~payments')
    await bringNear(page, DRILL)
    await expect(ladder(page)).toHaveAttribute('data-drill-level', 'statuses', { timeout: 20_000 })
    await ladder(page).locator('[data-chart-cursor]').focus()
    await page.keyboard.press('ArrowRight')
    await expect(ladder(page).locator('[data-mark-intent="drill"]')).toBeVisible()
    await expect(ladder(page).locator('[data-mark-intent="filter"]')).toHaveCount(0)
  })

  for (const theme of ['signal', 'lab'] as const) {
    test(`axe on the ladder with the filter action offered, every impact (${theme})`, async ({ page }) => {
      await openRollout(page, '/failures', { handlers: [...SUITES, ...FAILURES_ON], flags: MULTI_ON, ready, theme })
      await levelZero(page)
      await focusFirstSuite(page)
      await expectNoBlockingViolations(page, theme, [], [`[data-catalogue-section="${DRILL}"]`])
    })
  }
})
