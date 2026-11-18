/**
 * D-0004 (GPT-6 exploratory, E2E-0004, mocked form), hermetic: on `/runs/<run>?tab=tests`, a failing test's
 * attribution evidence names what it did in the project's previous run. A test failing in the project's FIRST run
 * reads "unknown — first run in this project", one that did not run there "unknown — not in the previous run", and
 * only a test that failed there "no — already failing". GPT-6 read "already failing" for a first observed failure.
 *
 * What a browser cannot prove: which history the server found. The authoritative checks are the backend
 * regressions in `backend/tests/regression/test_phase4_attribution_verdict.py` (the classifier, the rationale per
 * outcome, `last_green_run_id` only after a pass). This spec guards the reader's view of `inputs.previous_outcome`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `RUN_PAGE`, `runTests()` (`fixtures-run.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { RUN_ID } from '../visual/production/fixtures'
import { RUN_PAGE, runTests } from '../visual/production/fixtures-run'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Outcome = 'no_previous_run' | 'not_in_previous_run' | 'failed'

const failing = runTests().filter((t) => t.status === 'FAILED' || t.status === 'BROKEN')
const CASES: { test: (typeof failing)[number]; outcome: Outcome; reads: string }[] = [
  { test: failing[0], outcome: 'no_previous_run', reads: 'unknown — first run in this project' },
  { test: failing[1], outcome: 'not_in_previous_run', reads: 'unknown — not in the previous run' },
  { test: failing[2], outcome: 'failed', reads: 'no — already failing' },
]

const RATIONALE: Record<Outcome, string> = {
  no_previous_run: 'No transition to attribute — this is the first recorded run in this project, so there is no earlier result to compare against.',
  not_in_previous_run: 'No transition to attribute — this test did not run in the previous run of this project, so there is no earlier result to compare against.',
  failed: 'Not a new failure — this test was already failing before this run, so there is no transition to attribute.',
}

function attributionItem({ test, outcome }: (typeof CASES)[number]) {
  return {
    test_case_id: test.id, test_name: test.test_name, suite_name: test.suite_name,
    verdict: 'UNCERTAIN', verdict_label: 'uncertain', verdict_description: 'Not enough evidence for a verdict.',
    confidence: 0, rationale: RATIONALE[outcome],
    inputs: {
      is_new_failure: false, last_green_run_id: null, previous_outcome: outcome,
      flaky_score: null, flaky_confidence: 'none', cluster_key: null, cluster_cause_family: null, cluster_size: 0,
      change_overlap: null, changed_files: [], calibration_mode: 'hint', calibration_specificity: null,
    },
    votes: {}, policy: 'Advisory only. No verdict suppresses, hides or auto-closes a failure.',
  }
}

const world: ApiHandlers = [
  [`/api/v1/runs/${RUN_ID}/attribution`, () => ({ items: CASES.map(attributionItem), total: CASES.length })],
  ...RUN_PAGE,
]

const ready = (p: Page) => p.locator('[data-primary] table')

test('a failing test\'s evidence names its previous outcome; only a prior failure reads "already failing"', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=tests`, { handlers: world, ready })
  for (const testCase of CASES) {
    const row = page.locator('[data-primary] tbody tr').filter({ hasText: String(testCase.test.test_name) })
    await row.getByRole('button', { name: 'Why this verdict? Show the signals it was composed from' }).click()
    const dialog = page.getByRole('dialog', { name: 'Attribution verdict evidence' })
    const newFailure = dialog.getByText('New failure').locator('xpath=following-sibling::dd[1]')
    await expect(newFailure).toHaveText(testCase.reads)
    if (testCase.outcome !== 'failed') await expect(dialog).not.toContainText('already failing')
    // The badge toggles its popover: close it before the next row.
    await row.getByRole('button', { name: 'Why this verdict? Show the signals it was composed from' }).click()
    await expect(page.getByRole('dialog', { name: 'Attribution verdict evidence' })).toHaveCount(0)
  }
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
