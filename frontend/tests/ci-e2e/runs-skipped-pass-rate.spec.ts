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
import { PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { agentsHandlers, RELEASE_ACTIVE_ID, RELEASES_PAGE } from '../visual/production/fixtures-pages'
import { RUN_COMPARE, RUN_COMPARE_PAGE } from '../visual/production/fixtures-ai-history'

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

// ── FIXREVIEW P-4 (test-coverage round 2026-11-18, card TEST-D): the other D-0005 surfaces (plan P8) ──────────
// A release's linked runs, a Run Compare side card and an Agents live card read the same rule
// (`utils/runPassRate.ts` `measuredRunPassRate`); only vitest guarded them. Each shows "—" for an all-skipped run.

const NO_RATE_REASON = 'No pass rate: no test was executed (every result was skipped)'

test('D-0005 P8: a release\'s linked runs: the all-skipped one is "—" with the reason, a failure-only one 0.0%', async ({ page }) => {
  const detailOf = RELEASES_PAGE.find(([matcher]) => String(matcher) === String(/^\/api\/v1\/releases\/[^/]+$/))?.[1]
  if (!detailOf) throw new Error('RELEASES_PAGE answers GET /releases/{id}')
  const linked = (id: string, build: string, counts: Record<string, number | string>) => ({
    id, build_number: build, created_at: '2026-09-17T10:00:00Z', primary_suite_name: 'checkout-e2e', suite_names: ['checkout-e2e'], phase_id: null, ...counts,
  })
  const skippedRun = linked('run-skip', 'build-skip-9', { status: 'stopped', total_tests: 4, passed_tests: 0, failed_tests: 0, broken_tests: 0, skipped_tests: 4, pass_rate: 0 })
  const failedRun = linked('run-fail', 'build-fail-9', { status: 'failed', total_tests: 2, passed_tests: 0, failed_tests: 2, broken_tests: 0, skipped_tests: 0, pass_rate: 0 })
  const handlers: ApiHandlers = [
    [`/api/v1/releases/${RELEASE_ACTIVE_ID}`, (request) => ({ ...(detailOf(request) as Record<string, unknown>), linked_runs: [skippedRun, failedRun] })],
    ...RELEASES_PAGE,
  ]
  const { api, errors } = await openRollout(page, `/releases?open=${RELEASE_ACTIVE_ID}`, { handlers, ready: (p) => p.getByRole('heading', { name: 'Releases', level: 1 }) })
  const pane = page.getByRole('complementary', { name: '2026.11', exact: true })
  const rateOf = (build: string) => pane.getByRole('row').filter({ hasText: build }).getByRole('cell').nth(5)
  await expect(rateOf('build-skip-9')).toHaveText('—')
  await expect(rateOf('build-skip-9').locator('span')).toHaveAttribute('title', NO_RATE_REASON)
  await expect(rateOf('build-fail-9')).toHaveText('0.0%')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('D-0005 P8: a Run Compare side card for an all-skipped baseline is "—" with the reason; the other side keeps its rate', async ({ page }) => {
  const skippedLeft = { ...RUN_COMPARE.left, status: 'STOPPED', total_tests: 5, passed_tests: 0, failed_tests: 0, broken_tests: 0, skipped_tests: 5, pass_rate: 0 }
  const handlers: ApiHandlers = [
    ['/api/v1/runs/compare/latest', () => ({ ...RUN_COMPARE, left: skippedLeft, selection: { ...RUN_COMPARE.selection, mode: 'latest' } })],
    ...RUN_COMPARE_PAGE,
  ]
  const { api, errors } = await openRollout(page, '/runs/compare?mode=latest&suite=Auth', { handlers, ready: (p) => p.locator('[data-delta-table]') })
  const side = (label: string) => page.locator('div.rounded-md').filter({ has: page.getByText(label, { exact: true }) })
  const leftRate = side('Left (baseline)').locator('span.ml-auto')
  await expect(leftRate).toHaveText('—')
  await expect(leftRate.locator('span')).toHaveAttribute('title', NO_RATE_REASON)
  await expect(side('Right (target)').locator('span.ml-auto')).toHaveText(`${RUN_COMPARE.right.pass_rate.toFixed(1)}%`)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('D-0005 P8: an Agents live card for a run with only skips so far reads "—", a failing one 0%', async ({ page }) => {
  const live = (run_id: string, counts: Record<string, number>, run_seq: number) => ({
    run_id, project_id: PROJECT_ID, build_number: run_id, current_test: null, started_at: '2026-09-18T11:58:00Z', suite_name: 'Auth', run_seq, ...counts,
  })
  const handlers: ApiHandlers = [
    ['/api/v1/agents/active-runs', () => ({
      active_runs: [
        live('live-skip', { total: 6, passed: 0, failed: 0, broken: 0, skipped: 3, pass_rate: 0 }, 71),
        live('live-fail', { total: 6, passed: 0, failed: 2, broken: 0, skipped: 0, pass_rate: 0 }, 72),
      ],
    })],
    ...agentsHandlers(),
  ]
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers, ready: (p) => p.getByRole('region', { name: 'AI report' }) })
  await page.getByRole('button', { name: /Live executions/ }).click()
  // The nearest card around the run's label (the section around the cards is a card too).
  const card = (seq: number) =>
    page.getByText(`Run #${seq}`, { exact: true }).locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " card ")][1]')
  await expect(card(71).locator('[data-live-pass-rate]')).toHaveText('—')
  await expect(card(72).locator('[data-live-pass-rate]')).toHaveText('0%')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
