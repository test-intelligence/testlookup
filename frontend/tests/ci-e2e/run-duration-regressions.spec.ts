/**
 * EXEC-05 on `/runs/:runId?tab=changes`, hermetic: "Did anything get slower?"
 * under the regression diff.
 *
 *  - a run with findings lists the test slower than its baseline ("+300ms
 *    (4.1σ)") and the suite slower than its history ("1.8× slower than its
 *    previous 20 runs"), both marked New;
 *  - a run without enough history says so ("Not enough history yet (4 of 10
 *    runs)") and never "No test or suite is slower";
 *  - the read is made once, when the tab opens.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock); the run page's
 * fixtures are `tests/visual/production/fixtures-run.ts` (`RUN_PAGE`,
 * `DURATION_REGRESSIONS`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { type ApiHandlers } from '../lib/production-pages'
import { RUN_ID } from '../visual/production/fixtures'
import { DURATION_REGRESSIONS, RUN_PAGE } from '../visual/production/fixtures-run'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const READ = new RegExp(`^/api/v1/runs/${RUN_ID}/duration-regressions$`)
const card = (page: Page) => page.getByRole('region', { name: 'Duration regressions' })

test('the Changes tab lists the test slower than baseline and the suite slower than its history', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
    handlers: RUN_PAGE,
    ready: (p) => card(p),
  })
  const region = card(page)
  await expect(region.getByRole('heading', { name: 'Did anything get slower?' })).toBeVisible()
  const spike = region.locator('[data-duration-spike="fp-auth-token-refresh"]')
  await expect(spike).toContainText('auth::token::test_refresh_rotates')
  await expect(spike).toContainText('+300ms (4.1σ)')
  await expect(spike).toContainText('400ms vs a mean of 100ms over 34 runs')
  await expect(spike.getByText('New')).toBeVisible()
  const suite = region.locator('[data-duration-suite="auth"]')
  await expect(suite).toContainText('Suite Auth: 1.8× slower than its previous 20 runs')
  await expect(region.getByText(/No test or suite is slower/)).toHaveCount(0)
  // Under the regression diff, above the way into Compare.
  const order = await page.evaluate(() => {
    const names = ['What changed since the last good run', 'Duration regressions', 'Compare']
    const nodes = names.map((n) => document.querySelector(`section[aria-label="${n}"]`))
    return nodes.every(Boolean) && nodes.every((n, i) => i === 0 || (nodes[i - 1] as Node).compareDocumentPosition(n as Node) === Node.DOCUMENT_POSITION_FOLLOWING)
  })
  expect(order).toBe(true)
  await networkQuiet(page, api)
  expect(requestsTo(api, READ)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('a run without enough history says how much there is, not "nothing got slower"', async ({ page }) => {
  const thin = {
    ...DURATION_REGRESSIONS,
    measured: false,
    reason: 'not enough history (4 of 10)',
    spikes: [],
    spike_count: 0,
    suites: [],
    suite_regression_count: 0,
    test_check: { measured: false, reason: 'not enough history (4 of 10)' },
    suite_check: { measured: false, reason: 'not enough history (4 of 15)' },
  }
  const handlers: ApiHandlers = [[`/api/v1/runs/${RUN_ID}/duration-regressions`, () => thin], ...RUN_PAGE]
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, {
    handlers,
    ready: (p) => card(p),
  })
  await expect(card(page)).toContainText('Not enough history yet (4 of 10 runs)')
  await expect(card(page).getByText(/No test or suite is slower/)).toHaveCount(0)
  await expect(card(page).locator('[data-duration-spike]')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('the Tests tab does not ask for duration regressions', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, {
    handlers: RUN_PAGE,
    ready: (p) => p.locator('[data-primary] table'),
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, READ)).toEqual([])
  await page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { name: 'Changes' }).click()
  await expect(card(page).locator('[data-duration-suite="auth"]')).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, READ)).toHaveLength(1)
  assertHermetic(api, errors)
})
