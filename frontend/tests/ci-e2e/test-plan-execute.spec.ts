/**
 * HON-01, hermetic: recording a test-plan execution on /test-management ->
 * Plans sends the verb the backend registers (PATCH). The client sent POST,
 * and the server's only route on `.../items/{item_id}/execute` is PATCH, so
 * every "passed / failed / blocked" click answered 405 and toasted "Failed to
 * record execution". The server half is pinned by
 * `backend/tests/regression/test_plan_item_execute_verb.py`.
 *
 * Here a POST is answered 405 exactly as the real router does, so a client
 * that regresses to POST fails on the toast, not only on the request log.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond, type ApiHandlers } from '../lib/production-pages'
import { isoAgo, PROJECT_ID } from '../visual/production/fixtures'
import { TEST_CASES } from '../visual/production/fixtures-pages'

const PLAN_ID = '9a000000-0000-4000-8000-000000000001'
const ITEM_ID = '9b000000-0000-4000-8000-000000000001'
const CASE_ID = 'c0ffee00-0000-4000-8000-000000000001'
const EXECUTE = `/api/v1/test-management/plans/${PLAN_ID}/items/${ITEM_ID}/execute`

const ready = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface PlanState {
  status: string
  bodies: unknown[]
}

function planHandlers(state: PlanState): ApiHandlers {
  const item = () => ({
    id: ITEM_ID,
    plan_id: PLAN_ID,
    test_case_id: CASE_ID,
    order_index: 0,
    priority_override: null,
    execution_status: state.status,
    executed_by_id: null,
    executed_at: state.status === 'not_run' ? null : isoAgo(0),
    execution_notes: null,
    actual_duration_minutes: null,
    created_at: isoAgo(3),
  })
  const executed = () => (state.status === 'not_run' ? 0 : 1)
  const plan = () => ({
    id: PLAN_ID,
    project_id: PROJECT_ID,
    name: 'Release 2026.11 regression',
    description: 'Checkout and auth before the cut',
    status: 'active',
    ai_generated: false,
    total_cases: 1,
    executed_cases: executed(),
    passed_cases: state.status === 'passed' ? 1 : 0,
    failed_cases: state.status === 'failed' ? 1 : 0,
    blocked_cases: state.status === 'blocked' ? 1 : 0,
    created_at: isoAgo(5),
    updated_at: isoAgo(1),
  })
  return [
    ['/api/v1/test-management/plans', () => ({ items: [plan()], total: 1, page: 1, size: 10, pages: 1 })],
    [`/api/v1/test-management/plans/${PLAN_ID}/items`, () => [item()]],
    [
      EXECUTE,
      ({ route }) => {
        const body = route.request().postDataJSON() as { execution_status: string }
        state.bodies.push(body)
        state.status = body.execution_status
        return item()
      },
      'PATCH',
    ],
    // The real router registers no POST on this path.
    [EXECUTE, () => respond(405, { detail: 'Method Not Allowed' }), 'POST'],
  ]
}

test('clicking "passed" on a plan item records the execution with a PATCH', async ({ page }) => {
  const state: PlanState = { status: 'not_run', bodies: [] }
  const { api, errors } = await openRollout(page, '/test-management', {
    handlers: [...planHandlers(state), ...TEST_CASES],
    ready,
  })

  await page.getByRole('tablist', { name: 'Test case sections' }).getByRole('tab', { name: 'Plans' }).click()
  await page.getByRole('heading', { name: 'Release 2026.11 regression' }).click()

  const row = page.getByRole('row').filter({ hasText: CASE_ID.slice(0, 8) })
  await expect(row.getByRole('cell').nth(2)).toHaveText('not run')

  await row.getByRole('button', { name: 'passed', exact: true }).click()

  await expect(page.getByText('Execution recorded')).toBeVisible()
  await expect(page.getByText('Failed to record execution')).toHaveCount(0)
  // The row and the plan's counts change without a reload.
  await expect(row.getByRole('cell').nth(2)).toHaveText('passed')
  await expect(page.getByText('1/1 executed')).toBeVisible()

  await networkQuiet(page, api)
  expect(requestsTo(api, EXECUTE)).toEqual([`PATCH ${EXECUTE}`])
  expect(state.bodies).toEqual([{ execution_status: 'passed' }])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
