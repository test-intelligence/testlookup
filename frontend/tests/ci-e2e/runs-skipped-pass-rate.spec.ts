/**
 * D-0005 (GPT-6 exploratory, E2E-0005), hermetic: on `/runs`, a finished run whose only test was skipped has no
 * pass rate: its cell reads "—" with the reason, never a red "0.0%", and the window's "Avg pass rate" leaves it
 * out. The API stores `pass_rate: 0` for "nothing executed" (`core/pass_rate.py`); the page used to read it as a
 * measured 0 %. A failure-only run keeps its real 0.0 %.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `RUNS_PAGE`, a spec-local `/runs` list of
 * four finished runs inside the window.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { RUNS_PAGE, runListOf, runs } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-status-banner]')
const rate = (page: Page, id: string) => page.locator(`#run-row-${id} [data-run-pass-rate]`)

/** Four finished runs, newest first: skipped-only (STOPPED), failure-only, one pass one fail, one pass one skip. */
function fourRuns() {
  const base = runs().slice(0, 4)
  const counts = [
    { status: 'STOPPED', passed_tests: 0, failed_tests: 0, broken_tests: 0, skipped_tests: 1, total_tests: 1, pass_rate: 0 },
    { status: 'FAILED', passed_tests: 0, failed_tests: 1, broken_tests: 0, skipped_tests: 0, total_tests: 1, pass_rate: 0 },
    { status: 'FAILED', passed_tests: 1, failed_tests: 1, broken_tests: 0, skipped_tests: 0, total_tests: 2, pass_rate: 50 },
    { status: 'PASSED', passed_tests: 1, failed_tests: 0, broken_tests: 0, skipped_tests: 1, total_tests: 2, pass_rate: 100 },
  ]
  return base.map((run, i) => ({ ...run, ...counts[i], build_number: 900 + i }))
}

const world = (): ApiHandlers => [['/api/v1/runs', runListOf(fourRuns, () => null)], ...RUNS_PAGE]

test('a skipped-only run shows no pass rate and stays out of the average; a failure-only run is 0.0%', async ({ page }) => {
  const [skipped, failed, mixed, passed] = fourRuns()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world(), ready })
  await expect(rate(page, skipped.id)).toHaveText('—')
  await expect(rate(page, skipped.id).locator('span')).toHaveAttribute(
    'title', 'No pass rate: no test was executed (every result was skipped)',
  )
  await expect(rate(page, failed.id)).toHaveText('0.0%')
  await expect(rate(page, mixed.id)).toHaveText('50.0%')
  await expect(rate(page, passed.id)).toHaveText('100.0%')
  // (0 + 50 + 100) / 3, never (0 + 0 + 50 + 100) / 4 = 37.5.
  await expect(page.locator('[data-banner-fact]').filter({ hasText: 'Avg pass rate' })).toHaveText('Avg pass rate 50.0%')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
