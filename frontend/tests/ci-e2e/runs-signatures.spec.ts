/**
 * W07 FAIL-03 (`docs/deferred-scope-2026-10-13/work/W07-contract.md` §2.3), hermetic, on `/runs`: "Failure
 * signatures" groups the window's failed builds by the error MOST of their failing tests share, not by their
 * `(passed, failed, total)` tuple.
 *
 * The fixture (`RUNS_EQUAL_COUNTS`, `fixtures-runs-failures.ts`): builds 240 and 239 have the SAME pass / fail /
 * skip / total, and different dominant errors (an assertion, a NullPointerException); four builds share the
 * assertion (one test fails with it in all four), one build's failures carry no message. So:
 *
 *  - ONE `GET /runs/failure-signatures` carries every failed build of the window (six ids), and the page reads
 *    no per-run signature;
 *  - the panel has three groups titled by their error's first line (`No message recorded` for the blank one);
 *    240 and 239 sit in different groups (their chips differ), whatever their counts say;
 *  - the shared-test sentence names the test failing in all four builds;
 *  - "How this verdict is computed" shows Signature diversity = 3 groups / 6 grouped builds = 50;
 *  - the old tuple copy is gone.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_SIGNATURES, SIGNATURE } from '../visual/production/fixtures'
import { equalCountRuns, RUNS_EQUAL_COUNTS } from '../visual/production/fixtures-runs-failures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-status-banner]')
const panel = (page: Page) => page.getByRole('complementary', { name: 'Failure signatures' })
const FAILED = equalCountRuns().filter((run) => run.status === 'FAILED')

test('equal-count builds with different errors are different groups, titled by their errors', async ({ page }) => {
  const runs = equalCountRuns()
  const [b240, b239] = runs
  // The premise: the old tuple could not tell these two apart.
  expect([b239.passed_tests, b239.failed_tests, b239.total_tests]).toEqual([b240.passed_tests, b240.failed_tests, b240.total_tests])

  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_EQUAL_COUNTS, ready })
  await expect(page.locator('[data-signature-chip]').first()).toBeVisible()
  await networkQuiet(page, api)

  // ONE batch with every failed build of the window, and nothing per run.
  const asked = requestsTo(api, '/api/v1/runs/failure-signatures')
  expect(asked, 'one signatures request').toHaveLength(1)
  const ids = new URLSearchParams(asked[0].split('?')[1] ?? '').getAll('run_id')
  expect([...ids].sort()).toEqual(FAILED.map((run) => run.id).sort())
  expect(requestsTo(api, /^\/api\/v1\/runs\/[^/]+\/(intelligence|failure-signatures)$/)).toEqual([])

  // The two equal-count builds carry different chips: different groups.
  const chip = (id: string) => page.locator(`#run-row-${id} [data-signature-chip]`)
  const [chip240, chip239] = [await chip(b240.id).textContent(), await chip(b239.id).textContent()]
  expect(chip240?.trim()).toBeTruthy()
  expect(chip239?.trim(), 'build 239 is not in build 240\'s group').not.toBe(chip240?.trim())
  await expect(chip(b240.id)).toHaveAttribute('title', SIGNATURE.assertion.label)
  await expect(chip(b239.id)).toHaveAttribute('title', SIGNATURE.npe.label)
  // A passed build has no chip.
  await expect(page.locator(`#run-row-${runs[2].id} [data-signature-chip]`)).toHaveCount(0)

  await page.getByRole('button', { name: /^Failure signatures/ }).click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('[data-signature-lead]')).toHaveText('3 signatures across 6 failed builds')
  const titles = panel(page).locator('[data-signature-group] code')
  await expect(titles).toHaveText([SIGNATURE.assertion.label, 'No message recorded', SIGNATURE.npe.label])
  await expect(panel(page).locator('[data-signature-group="primary"] code')).toHaveText(SIGNATURE.assertion.label)
  await expect(panel(page).locator('[data-signature-group="primary"] code')).toHaveAttribute('title', SIGNATURE.assertion.signature)
  // The shared test, named only because every member's dominant error lists it.
  expect(RUN_SIGNATURES[0][0].tests).toContain('login keeps session')
  await expect(panel(page).locator('[data-signature-shared]')).toHaveText('login keeps session fails with this error in all 4 builds.')
  await expect(panel(page)).toContainText('2 failed builds carry a different error.')
  // The old tuple copy is gone.
  await expect(panel(page)).not.toContainText('same test failing across all of them')
  await expect(page.getByText('outlier · old config')).toHaveCount(0)

  // Signature diversity: 3 groups over 6 grouped builds.
  await panel(page).getByRole('button', { name: 'Close panel' }).click()
  const disclosure = page.locator('[data-disclosure]').filter({ hasText: 'How this verdict is computed' })
  await disclosure.getByRole('button', { name: /How this verdict is computed/ }).click()
  const diversity = page.locator('[data-dimension="signature_diversity"]')
  await expect(diversity).toHaveAttribute('data-dimension-measured', 'true')
  await expect(diversity.locator('[data-dimension-value]')).toHaveText('50')
  assertHermetic(api, errors)
})

test('the default fixture: the four-build assertion group is primary and every failed build is grouped', async ({ page }) => {
  const { RUNS_PAGE, runs } = await import('../visual/production/fixtures')
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  await page.getByRole('button', { name: /^Failure signatures 3$/ }).click()
  await expect(panel(page).locator('[data-signature-lead]')).toHaveText('3 signatures across 6 failed builds')
  await expect(panel(page).locator('[data-signature-group="primary"]')).toContainText('4 builds')
  await networkQuiet(page, api)
  const ids = new URLSearchParams(requestsTo(api, '/api/v1/runs/failure-signatures')[0].split('?')[1] ?? '').getAll('run_id')
  expect(ids).toHaveLength(runs().filter((run) => run.status === 'FAILED').length)
  assertHermetic(api, errors)
})
