/**
 * GATE-05, hermetic: /release-gate/<run> -> Why -> Risk Dimension Breakdown
 * -> "Why this score?" explains the weight the verdict applied.
 *
 * The verdict weighs the dimensions with the project's release-gate policy;
 * the stored rows carry the defaults (reproducibility 0.15). The matrix asks
 * `GET /api/v1/scoring-model?run_id=<run>`, which answers with the run's
 * project's policy (here reproducibility 0.40, "Checkout strict" v2), and
 * shows that weight and that policy. The server half (scoping, 403/404,
 * parity with the evaluator) is
 * `backend/tests/regression/test_scoring_model_follows_policy.py`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the `prod-release-gate` visual spec).
 */
import { expect, test, type Page } from '@playwright/test'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import type { ApiHandlers } from '../lib/production-pages'
import { RELEASE_GATE, RUN_ID } from '../visual/production/fixtures'

const PATH = `/release-gate/${RUN_ID}`
const ready = (p: Page) => p.getByRole('meter', { name: 'Risk Score' })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The run's project's policy model; the unscoped default otherwise. */
const SCORING_MODEL: ApiHandlers = [
  [
    '/api/v1/scoring-model',
    ({ url }) => {
      const scoped = url.searchParams.get('run_id') === RUN_ID
      return {
        version: 1,
        go_threshold: scoped ? 15 : 20,
        no_go_threshold: scoped ? 45 : 50,
        dimensions: [
          { name: 'reproducibility', weight: scoped ? 0.4 : 0.15, description: 'How consistently the issue can be reproduced.' },
          { name: 'blast_radius', weight: 0.15, description: 'How much of the product the failures touch.' },
          { name: 'diagnosis_confidence', weight: 0.05, description: 'How sure the analysis is of the cause.' },
        ],
        ...(scoped
          ? {
              pass_rate_minimum: 95,
              pass_rate_hard_floor_factor: 0.7,
              policy: { policy_id: 'p-2', name: 'Checkout strict', version: 2, level: 'project' },
            }
          : {}),
      }
    },
  ],
]

test('"Why this score?" shows the policy weight the verdict used, and names the policy', async ({ page }) => {
  const { api, errors } = await openRollout(page, PATH, { handlers: [...SCORING_MODEL, ...RELEASE_GATE], ready })

  await page.getByRole('button', { name: /Risk Dimension Breakdown/ }).click()
  await expect(page.getByTestId('criticality-weight-source')).toHaveText('Weights from policy Checkout strict v2')
  await expect(page.getByTestId('criticality-weight-reproducibility')).toHaveText('×40%')
  await expect(page.getByTestId('criticality-weight-blast_radius')).toHaveText('×15%')

  // The first row is reproducibility (the decision's order).
  await page.getByTitle('Why this score?').first().click()
  await expect(page.getByText('How consistently the issue can be reproduced.')).toBeVisible()
  await expect(page.getByTestId('criticality-why-weight-reproducibility')).toHaveText(
    'Weight 0.40 · Weights from policy Checkout strict v2',
  )

  await networkQuiet(page, api)
  // Asked for the run's project, once; never the unscoped default.
  expect(requestsTo(api, '/api/v1/scoring-model')).toEqual([`GET /api/v1/scoring-model?run_id=${RUN_ID}`])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
