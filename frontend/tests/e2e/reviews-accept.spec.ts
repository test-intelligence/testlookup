import { test, expect, type Route } from '@playwright/test'
import { seedActiveProject } from './apiMock'

/**
 * E8.5 (architecture section 8): accepting a pending AI report moves its
 * pipeline's chip from COMPLETED (awaiting review) to PASSED.
 *
 * The pipelines list, the review queue and the accept call are mocked so the
 * flow is deterministic and nothing is written to the live backend. The mocks
 * share one `accepted` flag, standing in for the server-side transition that
 * `POST /reviews/{id}/accept` performs.
 */

const PIPELINE_ID = '00000000-0000-4000-8000-00000000e851'
const RUN_ID = '00000000-0000-4000-9000-00000000e851'
const REVIEW_ID = '00000000-0000-4000-a000-00000000e851'

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

test.describe('/reviews accept flow (E8.5)', () => {
  test('accepting a pending report moves its pipeline chip to PASSED', async ({ page }) => {
    const project = await seedActiveProject(page)
    let accepted = false

    const pipeline = () => ({
      id: PIPELINE_ID,
      test_run_id: RUN_ID,
      workflow_type: 'deep',
      status: accepted ? 'passed' : 'completed',
      public_status: accepted ? 'passed' : 'completed',
      attempt: 1,
      max_attempts: 5,
      started_at: '2026-09-12T10:00:00Z',
      completed_at: '2026-09-12T10:05:00Z',
      error: null,
      created_at: '2026-09-12T10:00:00Z',
      execution_metadata: {},
      provenance_metadata: null,
      build_number: 'e85-1',
      run_seq: 851,
      suite_name: null,
    })

    const review = () => ({
      id: REVIEW_ID,
      project_id: project.id,
      kind: 'report',
      subject_type: 'pipeline_run',
      subject_id: PIPELINE_ID,
      pipeline_run_id: PIPELINE_ID,
      test_run_id: RUN_ID,
      workflow_type: 'deep',
      state: accepted ? 'accepted' : 'pending_review',
      reviewed: accepted,
      reviewed_at: accepted ? '2026-09-12T11:00:00Z' : null,
      reason_code: null,
      notes: null,
      evidence_bundle_sha256: null,
      superseded_by: null,
      created_at: '2026-09-12T10:05:00Z',
      requires_human_review: true,
      ai_disclaimer: 'AI-generated content. Verify before acting.',
      ai_disclaimer_version: '2026-09-12.v1',
    })

    // The list call only, not ".../pipelines/{id}/..." or ".../pipelines/trigger".
    await page.route(/\/api\/v1\/agents\/pipelines(\?[^/]*)?$/, async route => {
      if (route.request().method() !== 'GET') return route.continue()
      await json(route, [pipeline()])
    })
    await page.route(/\/api\/v1\/projects\/[^/?]+\/reviews(\?.*)?$/, async route => {
      const state = new URL(route.request().url()).searchParams.get('state')
      await json(route, [review()].filter(r => !state || r.state === state))
    })
    await page.route(`**/api/v1/reviews/${REVIEW_ID}/accept`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      accepted = true
      await json(route, review())
    })

    await page.goto('/agents')
    await expect(page.getByTestId('pipeline-status-chip').first()).toHaveText('COMPLETED', { timeout: 15_000 })
    await expect(page.getByTestId('pipeline-awaiting-review').first()).toBeVisible()

    await page.goto('/reviews')
    await expect(page.getByTestId('review-row')).toHaveCount(1, { timeout: 15_000 })
    await page.getByTestId('review-accept').click()
    await page.getByTestId('review-confirm').click()
    await expect(page.getByTestId('review-row')).toHaveCount(0, { timeout: 15_000 })
    expect(accepted).toBe(true)

    await page.goto('/agents')
    await expect(page.getByTestId('pipeline-status-chip').first()).toHaveText('PASSED', { timeout: 15_000 })
    await expect(page.getByTestId('pipeline-awaiting-review')).toHaveCount(0)
  })
})
