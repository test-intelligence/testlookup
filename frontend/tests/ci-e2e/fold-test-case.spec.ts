/**
 * UX redesign P4 (`02-design-spec.md` §2 + §5 "Test case"), on
 * /runs/:runId/tests/:testId, hermetic, at 1440 x 900:
 *
 *  - the answer is in the first viewport: the error, the stack trace and the
 *    AI root cause are one block (`[data-primary]`) whose top is at most
 *    300 px below the top of `#main-content`, and the stack trace and the
 *    suggested root cause are on screen without scrolling;
 *  - above it only the header and the chips (suite, class, status, duration,
 *    tags), never the old metadata grid or the hard-coded `<Class>.java`;
 *  - below it the tabs History · Steps · Details (`?tab=`): History is the
 *    default and asks for the cross-run history at load, Steps asks for the
 *    step tree only when opened, Details shows the fingerprint, the ids and
 *    the parser.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after can be compared (`docs/viz-work/p4-agent-E.md`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). The fixtures
 * are this page's own (`TEST_CASE` in `fixtures-pages.ts`, shared with the
 * fold budget, P6): one failed test of the visual specs' run `RUN_ID`.
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import {
  TEST_CASE,
  TEST_CASE_API as BASE,
  TEST_CASE_ERROR as ERROR,
  TEST_CASE_FINGERPRINT as FINGERPRINT,
  TEST_CASE_ID as TEST_ID,
  TEST_CASE_NAME as TEST_NAME,
  TEST_CASE_PATH as PATH,
  TEST_CASE_ROOT_CAUSE as ROOT_CAUSE,
} from '../visual/production/fixtures-pages'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: TEST_NAME })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The scroller's height and the primary block's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<{ scrollHeight: number; primaryTop: number | null; primaries: number }> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
    }
  }, MAIN)
}

test('the error, the stack trace and the AI root cause are in the first viewport at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: TEST_CASE, ready })
  const primary = page.locator('[data-primary]')
  await expect(primary.getByText(ROOT_CAUSE)).toBeVisible()
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD ${PATH.replace(RUN_ID, ':runId').replace(TEST_ID, ':testId')} ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary block').toBe(1)
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // The answer, without scrolling: the error, its stack trace, the AI's root cause.
  await expect(primary.getByText(ERROR).first()).toBeInViewport()
  await expect(primary.getByText('TotalSpec.java:42', { exact: false }).first()).toBeInViewport()
  await expect(primary.getByText(ROOT_CAUSE)).toBeInViewport()
  // The chips above it, and none of the deleted duplicates (§5).
  const chips = page.locator('[data-test-case-chips]')
  for (const text of ['Checkout', 'TotalSpec', 'FAILED', '4.2s', 'smoke', 'checkout']) {
    await expect(chips.getByText(text, { exact: true }).first()).toBeVisible()
  }
  await expect(page.getByText('TotalSpec.java', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Run Date', { exact: true })).toHaveCount(0)
  // The tabs, below the block; History by default.
  const tabs = page.getByRole('tablist', { name: 'Test case sections' })
  await expect(tabs.getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
  expect(requestsTo(api, `${BASE}/history`)).toHaveLength(1)
  // Steps are asked for only when their tab opens.
  expect(requestsTo(api, `${BASE}/steps`)).toHaveLength(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the tabs: ?tab= opens one, and each shows its section', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${PATH}?tab=steps`, { handlers: TEST_CASE, ready })
  const tabs = page.getByRole('tablist', { name: 'Test case sections' })
  await expect(tabs.getByRole('tab', { name: /Steps/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('read the total')).toBeVisible()
  await expect.poll(() => requestsTo(api, `${BASE}/steps`).length).toBe(1)

  await tabs.getByRole('tab', { name: /Details/ }).click()
  await expect(page).toHaveURL(/[?&]tab=details/)
  await expect(page.getByText(FINGERPRINT)).toBeVisible()
  await expect(page.getByText('2.4.1')).toBeVisible()

  await tabs.getByRole('tab', { name: /History/ }).click()
  await expect(page).not.toHaveURL(/[?&]tab=/)
  await expect(page.getByText('Recent runs (oldest → newest)')).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
