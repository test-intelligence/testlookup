/**
 * W36 STRAT-08 on `/runs/:id?tab=changes`, hermetic (`W36-contract.md` §2, §3): the advisory "Likely affected
 * tests" card, right after the commit list's card.
 *
 *  - the ranked list: name, status in this run, the score's number, the matched paths and "failed with these files
 *    in N of M past runs" (a 0 stays 0), a "flaky" tag on the known-flaky row, the caveat as the footer; ONE read
 *    with `limit=20`;
 *  - history not ready: the path-only banner names the readiness reason; each row says "no history yet";
 *  - no commit range / a range without files: the reason in words, no list (never an empty list standing in).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `runChangesWorld('listed')` +
 * `tests/lib/w36-fixtures.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, queryOf } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { runChangesWorld } from '../visual/production/fixtures-integrations-runtime'
import {
  LIKELY_AFFECTED_CAVEAT,
  likelyAffectedPath,
  likelyAffectedPathOnly,
  likelyAffectedTests,
  likelyAffectedUnavailable,
  PATH_ONLY_REASON,
} from '../lib/w36-fixtures'

test.use({ viewport: { width: 1440, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const PATH = likelyAffectedPath(RUN_ID)
const main = (page: Page) => page.getByRole('region', { name: 'What changed since the last good run' })
const card = (page: Page) => page.getByRole('region', { name: 'Likely affected tests' })
const changesReady = (p: Page) => main(p).getByText(/Baseline: Build #238/)
const rows = (page: Page) => card(page).locator('[data-likely-test]')

async function openChanges(page: Page, answer: unknown) {
  const handlers: ApiHandlers = [[PATH, () => answer], ...runChangesWorld('listed').handlers]
  const opened = await openRollout(page, `/runs/${RUN_ID}?tab=changes`, { handlers, ready: changesReady })
  await expect(card(page)).toBeVisible({ timeout: 15_000 })
  return opened
}

test('the ranked list: names, status, score, matched paths and past co-failures; flaky tagged; the caveat; after the commit list', async ({ page }) => {
  const { api, errors } = await openChanges(page, likelyAffectedTests(RUN_ID))
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(0)).toContainText('test_gateway_timeout_retries · tests.payments.test_gateway')
  await expect(rows(page).nth(0).locator('[data-likely-status]')).toHaveText('failed')
  await expect(rows(page).nth(0).locator('[data-likely-score]')).toHaveText('0.92')
  await expect(rows(page).nth(0).locator('[data-likely-reason]')).toHaveText(
    'Matches src/payments/gateway.py · failed with these files in 4 of 5 past runs',
  )
  await expect(rows(page).nth(0).locator('[data-likely-flaky]')).toHaveCount(0)
  await expect(rows(page).nth(1).locator('[data-likely-reason]')).toHaveText(
    'Matches src/payments/refunds/webhook.py, src/payments/refunds/models.py · failed with these files in 1 of 5 past runs',
  )
  await expect(rows(page).nth(1).locator('[data-likely-flaky]')).toHaveText('flaky')
  await expect(rows(page).nth(2).locator('[data-likely-reason]')).toHaveText(
    'Matches src/checkout/cart.py · failed with these files in 0 of 5 past runs',
  )
  await expect(card(page)).toContainText("3 of this run's 212 tests, ranked against 14 changed files.")
  await expect(card(page).locator('[data-likely-path-only]')).toHaveCount(0)
  await expect(card(page).locator('[data-likely-caveat]')).toHaveText(LIKELY_AFFECTED_CAVEAT)

  // Its own card, right after the main one (the commit list), not inside it.
  const placement = await page.evaluate(() => {
    const regions = [...document.querySelectorAll('section[aria-label]')]
    const mainCard = regions.find((el) => el.getAttribute('aria-label') === 'What changed since the last good run')
    return mainCard?.nextElementSibling?.getAttribute('aria-label') ?? null
  })
  expect(placement).toBe('Likely affected tests')
  await expect(main(page).locator('[data-likely-affected]')).toHaveCount(0)

  await networkQuiet(page, api)
  const asked = queryOf(api, PATH)
  expect(asked).toHaveLength(1)
  expect(asked[0].get('limit')).toBe('20')
  assertHermetic(api, errors)
})

test('history not ready: the path-only banner names the reason; each row says "no history yet"', async ({ page }) => {
  const { api, errors } = await openChanges(page, likelyAffectedPathOnly(RUN_ID))
  await expect(card(page).locator('[data-likely-path-only]')).toHaveText(
    `Ranked by path overlap only: past co-failures are not counted yet — ${PATH_ONLY_REASON}.`,
  )
  await expect(rows(page)).toHaveCount(3)
  for (let i = 0; i < 3; i += 1) {
    await expect(rows(page).nth(i).locator('[data-likely-reason]')).toContainText('· no history yet')
    await expect(rows(page).nth(i).locator('[data-likely-reason]')).not.toContainText('past run')
  }
  await expect(rows(page).nth(0).locator('[data-likely-score]')).toHaveText('1.00')
  await expect(card(page).locator('[data-likely-caveat]')).toHaveText(LIKELY_AFFECTED_CAVEAT)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

for (const [reason, words] of [
  ['no_commit_range', 'No commit range is known for this run, so there are no changed files to match its tests against.'],
  ['no_changed_files', "This run's commit range lists no changed files (the source sent none), so there is nothing to match its tests against."],
] as const) {
  test(`unavailable (${reason}): the reason in words, no list, the caveat`, async ({ page }) => {
    const { api, errors } = await openChanges(page, likelyAffectedUnavailable(RUN_ID, reason))
    await expect(card(page).locator('[data-likely-state="unavailable"]')).toHaveText(words)
    await expect(card(page).locator('[data-likely-list]')).toHaveCount(0)
    await expect(card(page).locator('[data-likely-state="none"]')).toHaveCount(0)
    await expect(card(page).locator('[data-likely-caveat]')).toHaveText(LIKELY_AFFECTED_CAVEAT)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}
