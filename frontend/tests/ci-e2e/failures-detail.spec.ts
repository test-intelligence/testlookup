/**
 * W07 FAIL-01 + FAIL-02 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.6, §2.7), hermetic, on
 * `/failures`:
 *
 *  FAIL-01  each Top failing row expands (a toggle before the test name) to its latest failure: when and in which
 *           build, the error's first line, the stack head, the owner chip and "Open in run" (the test's page in
 *           that run: the spec asserts the `href`, it does not navigate). A hostile error stays text, a row with no
 *           message reads "No message recorded" and "Unassigned"; collapsed rows render no detail. In All Projects
 *           the expanded row reads "Select a project to see failure detail."
 *  FAIL-02  the KPI strip's fifth tile, "Median time to fix", reads the server's median (43.2 h → `1.8 d`) and
 *           `23 fixed`, asked once for the page's window; "How this score is computed" → Time to fix scores it (74);
 *           below the floor the tile says "Not measured" (never 0) and the dimension reads "—".
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `FAILURES_ON` (`fixtures.ts`: the §1.5
 * keys on the Top failing rows, `/analytics/time-to-fix`), `FAILURES_TTF_NOT_MEASURED` (`fixtures-runs-failures.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { landmark, openProductionPage } from '../lib/production-pages'
import { assertHermetic, expectHostileAsText, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  FAILURES_ON,
  HOSTILE_NAME,
  NOW,
  PROJECT_ID,
  RUN_ID,
  TIME_TO_FIX_PATH,
  TOP_FAILING_CASE_IDS,
  TOP_FAILING_DETAIL,
} from '../visual/production/fixtures'
import { ADMIN } from '../visual/production/fixtures-releases'
import { FAILURES_TTF_NOT_MEASURED } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Failure verdict')
const toggle = (page: Page, test: string): Locator =>
  page.getByRole('button', { name: `Show failure detail for ${test}`, exact: true })
const detailRows = (page: Page) => page.locator('[data-failure-detail]')
/** The detail row right under a test's row. */
const detailOf = (page: Page, test: string): Locator =>
  page.locator('tr', { has: toggle(page, test) }).locator('xpath=following-sibling::tr[1][@data-failure-detail]')

test('a Top failing row expands to its latest error, stack head, owner and "Open in run"', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  await expect(toggle(page, 'card declined shows reason')).toBeVisible()
  await expect(detailRows(page), 'rows start collapsed').toHaveCount(0)
  await expect(toggle(page, 'card declined shows reason')).toHaveAttribute('aria-expanded', 'false')

  await toggle(page, 'card declined shows reason').click()
  await expect(toggle(page, 'card declined shows reason')).toHaveAttribute('aria-expanded', 'true')
  const first = detailOf(page, 'card declined shows reason')
  await expect(first).toBeVisible()
  await expect(first.locator('pre[data-failure-error]')).toHaveText(TOP_FAILING_DETAIL.error)
  await expect(first.locator('pre[data-failure-stack]')).toContainText(TOP_FAILING_DETAIL.stackFirstLine)
  await expect(first).toContainText('first 20 lines')
  await expect(first).toContainText('build 240')
  await expect(first.locator('[data-failure-owner]')).toHaveText(`${TOP_FAILING_DETAIL.owner} · assignee · ${TOP_FAILING_DETAIL.ownerTeam}`)
  await expect(first.locator('[data-failure-open-run]')).toHaveText('Open in run')
  await expect(first.locator('[data-failure-open-run]')).toHaveAttribute('href', `/runs/${RUN_ID}/tests/${TOP_FAILING_CASE_IDS[0]}`)
  await expect(detailRows(page), 'only the opened row').toHaveCount(1)

  // A hostile error is text; an ownership-rule owner says so.
  await toggle(page, 'checkout total includes tax').click()
  const second = detailOf(page, 'checkout total includes tax')
  await expect(second.locator('pre[data-failure-error]')).toHaveText(`TypeError: ${HOSTILE_NAME} is not a function`)
  await expectHostileAsText(page, second, 'the latest error')
  await expect(second.locator('[data-failure-owner]')).toHaveText('Checkout · ownership rule')

  // No message, no trace, nobody: said so, nothing invented.
  await toggle(page, 'refund webhook retried').click()
  const third = detailOf(page, 'refund webhook retried')
  await expect(third.locator('pre[data-failure-error]')).toHaveCount(0)
  await expect(third).toContainText('No message recorded')
  await expect(third.locator('pre[data-failure-stack]')).toHaveCount(0)
  await expect(third.locator('[data-failure-owner]')).toHaveText('Unassigned')

  // Collapsing closes it again.
  await toggle(page, 'card declined shows reason').click()
  await expect(first).toHaveCount(0)
  await expect(detailRows(page)).toHaveCount(2)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the fifth KPI tile is the measured median time to fix, and the score reads it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  const tile = page.locator('[data-time-to-fix]')
  await expect(tile).toHaveAttribute('data-time-to-fix', 'measured')
  await expect(tile).toContainText('Median time to fix')
  await expect(tile).toContainText('1.8 d')
  await expect(tile.locator('[data-time-to-fix-fixed]')).toHaveText('23 fixed')
  await networkQuiet(page, api)
  expect(requestsTo(api, TIME_TO_FIX_PATH)).toEqual([`GET ${TIME_TO_FIX_PATH}?project_id=${PROJECT_ID}&days=30`])

  const disclosure = page.locator('[data-disclosure]').filter({ hasText: 'How this score is computed' })
  await disclosure.getByRole('button', { name: /How this score is computed/ }).click()
  const dimension = page.locator('[data-dimension="time_to_fix"]')
  await expect(dimension).toHaveAttribute('data-measured', 'true')
  // 43.2 h of a 168 h scale: 100 − 25.7 = 74.
  await expect(dimension.locator('[data-dimension-value]')).toHaveText('74')
  assertHermetic(api, errors)
})

test('below the floor the tile says "Not measured" (never 0) and the dimension reads "—"', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_TTF_NOT_MEASURED, ready })
  const tile = page.locator('[data-time-to-fix]')
  await expect(tile).toHaveAttribute('data-time-to-fix', 'not-measured')
  await expect(tile).toContainText('Not measured')
  await expect(tile).not.toContainText(/\d\s*(h|d)\b/)
  await expect(tile.locator('[data-time-to-fix-fixed]')).toHaveCount(0)
  const disclosure = page.locator('[data-disclosure]').filter({ hasText: 'How this score is computed' })
  await disclosure.getByRole('button', { name: /How this score is computed/ }).click()
  const dimension = page.locator('[data-dimension="time_to_fix"]')
  await expect(dimension).toHaveAttribute('data-measured', 'false')
  await expect(dimension.locator('[data-dimension-value]')).toHaveText('—')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('in All Projects an expanded row asks for one project', async ({ page }) => {
  const { api, errors } = await openProductionPage(page, '/failures', {
    theme: 'signal',
    now: NOW,
    // All Projects is an ADMIN's view (every other role is moved onto its first project).
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: FAILURES_ON,
    ready,
    mock: { flags: {} },
  })
  await toggle(page, 'card declined shows reason').click()
  const row = detailOf(page, 'card declined shows reason')
  await expect(row).toHaveText('Select a project to see failure detail.')
  await expect(row.locator('[data-failure-open-run]')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/analytics/top-failing').every((line) => !line.includes('project_id=')), 'All Projects').toBe(true)
  assertHermetic(api, errors)
})
