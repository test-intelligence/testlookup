/**
 * W08 TRND-12 (contract §2.10), hermetic, on `/coverage` › Coverage map:
 *
 *   - a level of 503 suites folds into 498 tiles and `Other (5)`; opening
 *     Other (keyboard: End, then Enter) asks the SAME level with
 *     `other_page=2` and draws the next five, the breadcrumb reading
 *     `All suites › Other · page 2`; the level's own crumb returns to the
 *     first page (no `other_page`);
 *   - a suite whose tests the last suite sync marked for review: its action
 *     row offers `Open the tests that need review`, the table view lists
 *     `3 need review`, and the action lands on
 *     `/test-management?tab=suites&suite=Payments`.
 *
 * `rollout-coverage-map.spec.ts` stays green UNEDITED: its default fixture
 * has no Other and no review counts, so its inventory and regions do not
 * move. Harness: `tests/lib/rollout.ts` (fail closed). Fixtures: `WIDE_MAP`,
 * `NEEDS_REVIEW_MAP` (`fixtures-trends-coverage.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, expectDrawn, expectNoErrorFrame, networkQuiet, openRollout, queryOf, section, sectionFrame } from '../lib/rollout'
import { COVERAGE_MAP_PATH, COVERAGE_ON } from '../visual/production/fixtures'
import { NEEDS_REVIEW_MAP, WIDE_MAP, WIDE_SUITES } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const MAP = { id: 'coverage-map', title: 'Test coverage map' } as const
const ready = (p: Page) => landmark(p, 'Run cadence')
const map = (page: Page) => sectionFrame(page, MAP.id, MAP.title)
const treemap = (page: Page) => section(page, MAP.id).locator('[data-chart-keyboard="treemap"]')
const focused = (page: Page) => section(page, MAP.id).locator('[data-coverage-focused]')
const crumbs = (page: Page) => section(page, MAP.id).locator('[data-coverage-breadcrumb] li')

async function openMap(page: Page, variant: ApiHandlers) {
  const opened = await openRollout(page, '/coverage', { handlers: [...variant, ...COVERAGE_ON], ready })
  await expectDrawn(map(page), MAP.id)
  await expect(section(page, MAP.id).locator('[data-chart-type="treemap"]')).toHaveAttribute('data-chart-status', 'ready')
  return opened
}

test('Other (5) opens the next page of the same level; the level\'s crumb returns to the first', async ({ page }) => {
  const { api, errors } = await openMap(page, WIDE_MAP)
  await expect(crumbs(page)).toHaveText(['All suites'])
  await treemap(page).focus()
  await page.keyboard.press('End')
  await expect(focused(page)).toHaveText('Other (5)')
  await expect(section(page, MAP.id).locator('[data-coverage-open-other]')).toHaveText('Show the next 5')
  await page.keyboard.press('Enter')

  await expect(crumbs(page)).toHaveText(['All suites', 'Other · page 2'])
  await expectDrawn(map(page), 'page 2')
  await expect
    .poll(() => queryOf(api, COVERAGE_MAP_PATH, (q) => q.get('other_page') === '2').length, { message: 'the level asked again, page 2' })
    .toBe(1)
  const page2 = queryOf(api, COVERAGE_MAP_PATH, (q) => q.get('other_page') === '2')[0]
  expect(page2.get('depth')).toBe('1')
  expect(page2.has('suite'), 'the same level: no drill').toBe(false)
  // The URL keeps its level: an Other page is the section's state, not a drill.
  await expect(page).not.toHaveURL(/[?&]drill=/)
  // Page 2: the five smallest suites, and no Other.
  await map(page).getByRole('button', { name: 'View as table' }).click()
  const table = map(page).getByRole('table').first()
  for (const suite of WIDE_SUITES.slice(498)) await expect(table).toContainText(suite.label)
  await expect(table).not.toContainText('Other (')
  await map(page).getByRole('button', { name: 'Hide table' }).click()

  // The level's own crumb is a link back to its first page.
  await crumbs(page).first().getByRole('link').click()
  await expect(crumbs(page)).toHaveText(['All suites'])
  await expectDrawn(map(page), 'page 1 again')
  await expectNoErrorFrame(page)
  await networkQuiet(page, api)
  expect(queryOf(api, COVERAGE_MAP_PATH).every((q) => q.get('other_page') === null || q.get('other_page') === '2')).toBe(true)
  assertHermetic(api, errors)
})

test('a suite with tests that need review: the action row, the table view, and the link to Test Management', async ({ page }) => {
  const { api, errors } = await openMap(page, NEEDS_REVIEW_MAP)
  await treemap(page).focus()
  // Walk the drawn order to Payments.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('ArrowRight')
    if (((await focused(page).textContent()) ?? '').trim() === 'Payments') break
  }
  await expect(focused(page)).toHaveText('Payments')
  const review = section(page, MAP.id).locator('[data-coverage-needs-review]')
  await expect(review).toHaveText('Open the tests that need review')
  // A suite with none offers no such action.
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('ArrowRight')
    if (((await focused(page).textContent()) ?? '').trim() === 'Search') break
  }
  await expect(focused(page)).toHaveText('Search')
  await expect(review).toHaveCount(0)

  await map(page).getByRole('button', { name: 'View as table' }).click()
  const counts = map(page).locator('[data-coverage-needs-review-table]')
  await expect(counts).toContainText('Payments')
  await expect(counts.locator('tr').filter({ hasText: 'Payments' })).toContainText('3 need review')
  await expect(counts.locator('tr').filter({ hasText: 'Auth' })).toContainText('1 need review')
  await expect(counts.locator('tr')).toHaveCount(2)
  await networkQuiet(page, api)
  assertHermetic(api, errors)

  // Back to the chart, to Payments, and its action lands on Test Management's Suites tab.
  await page.goto('/coverage')
  await expectDrawn(map(page), MAP.id)
  await treemap(page).focus()
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('ArrowRight')
    if (((await focused(page).textContent()) ?? '').trim() === 'Payments') break
  }
  await section(page, MAP.id).locator('[data-coverage-needs-review]').click()
  await expect(page).toHaveURL(/\/test-management\?tab=suites&suite=Payments$/)
})
