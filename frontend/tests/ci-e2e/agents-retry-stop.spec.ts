/**
 * W04 AGT-04, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.8):
 *
 *  - `/agents`: a failed and a degraded row offer Retry, a running row offers Stop, a passed row offers
 *    nothing; the actions sit BESIDE the row's button (no button inside a button, no nesting warning);
 *  - Retry -> one POST, the server's decision in words (resume "attempt 2/5" / rerun "configuration
 *    changed"), and the list read again (the resumed row goes in progress);
 *  - a review-rejected deep run answers 409: "Rejected in review — start a new run." and the button queues a
 *    DEEP run (`{test_run_id, workflow_id: "deep"}`);
 *  - Stop asks first, then the run is asked to stop and the row shows "stopping";
 *  - a VIEWER sees no action and makes no request a viewer is refused;
 *  - `/deep-investigate` on a run whose deep pipeline failed: "Retry from last successful stage" posts the
 *    pipeline's retry, never a second `deep-investigate`, and the page follows the resumed run.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agents-billing-flags.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond, type ApiHandlers } from '../lib/production-pages'
import {
  agentsActionsHandlers,
  DEEP_PIPELINE_ID,
  deepHandlers,
  PIPELINE,
  RUN_ID,
  VIEWER,
} from '../visual/production/fixtures-agents-billing-flags'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const row = (page: Page, id: string) => page.locator(`[data-testid="pipeline-row"][data-pipeline-id="${id}"]`)
const ready = (p: Page) => p.getByTestId('pipeline-list')

/** React's nesting warnings, as the console reports them. */
function watchNesting(page: Page): string[] {
  const warnings: string[] = []
  page.on('console', (message) => {
    const text = message.text()
    if (message.type() === 'error' && /validateDOMNesting|cannot be a descendant of|cannot contain a nested/.test(text)) warnings.push(text)
  })
  return warnings
}

test('each row offers exactly its actions, beside the row button and never inside it', async ({ page }) => {
  const nesting = watchNesting(page)
  const { handlers } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers, ready })
  await expect(row(page, PIPELINE.failed).getByTestId('pipeline-retry')).toBeVisible()
  await expect(row(page, PIPELINE.degraded).getByTestId('pipeline-retry')).toBeVisible()
  await expect(row(page, PIPELINE.rejected).getByTestId('pipeline-retry')).toBeVisible()
  await expect(row(page, PIPELINE.running).getByTestId('pipeline-stop')).toBeVisible()
  await expect(row(page, PIPELINE.running).getByTestId('pipeline-retry')).toHaveCount(0)
  await expect(row(page, PIPELINE.passed).getByTestId('pipeline-row-actions')).toHaveCount(0)
  for (const id of [PIPELINE.failed, PIPELINE.degraded, PIPELINE.rejected, PIPELINE.passed]) {
    await expect(row(page, id).getByTestId('pipeline-stop')).toHaveCount(0)
  }
  await expect(page.locator('button button')).toHaveCount(0)
  // The row's own control is still the one button that selects it (`fold-agents.spec.ts` clicks it).
  await expect(row(page, PIPELINE.passed).getByRole('button', { name: /offline pipeline/i })).toHaveAttribute('aria-pressed', 'false')
  await networkQuiet(page, api)
  expect(nesting).toEqual([])
  assertHermetic(api, errors)
})

test('Retry resumes a failed run: one POST, "Resumed (attempt 2/5)", and the list read again shows it in progress', async ({ page }) => {
  const { handlers, state } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers, ready })
  const failed = row(page, PIPELINE.failed)
  const readsBefore = state.listReads
  await failed.getByTestId('pipeline-retry').click()
  await expect(failed.getByTestId('pipeline-action-note')).toHaveText('Resumed (attempt 2/5) — keeps the completed stages.')
  // The list is revalidated: a second read, and the resumed run is in progress (Stop, not Retry).
  await expect.poll(() => state.listReads).toBeGreaterThan(readsBefore)
  await expect(failed.getByTestId('pipeline-status-chip')).toHaveText('IN PROGRESS')
  await expect(failed.getByTestId('pipeline-stop')).toBeVisible()
  await expect(failed.getByTestId('pipeline-retry')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes.map((w) => w.line)).toEqual([`POST /api/v1/agents/pipelines/${PIPELINE.failed}/retry`])
  assertHermetic(api, errors)
})

test('Retry of a degraded run whose configuration changed: the server started a new run, the page triggers nothing', async ({ page }) => {
  const { handlers, state } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers, ready })
  const degraded = row(page, PIPELINE.degraded)
  await degraded.getByTestId('pipeline-retry').click()
  await expect(degraded.getByTestId('pipeline-action-note')).toHaveText(
    'Started a new run — the analysis configuration changed since this run started.',
  )
  await networkQuiet(page, api)
  expect(state.writes.map((w) => w.line)).toEqual([`POST /api/v1/agents/pipelines/${PIPELINE.degraded}/retry`])
  assertHermetic(api, errors)
})

test('a review-rejected deep run: "Rejected in review — start a new run." and the button queues a DEEP run', async ({ page }) => {
  const { handlers, state } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers, ready })
  const rejected = row(page, PIPELINE.rejected)
  await rejected.getByTestId('pipeline-retry').click()
  await expect(rejected.getByTestId('pipeline-action-note')).toHaveText('Rejected in review — start a new run.')
  // The page renders the 409 itself: no global toast repeats it.
  await expect(page.getByText(/^message: /)).toHaveCount(0)
  await rejected.getByTestId('pipeline-start-new-run').click()
  await expect(page.getByText('Pipeline queued — it will appear in the list shortly.')).toBeVisible()
  await expect(rejected.getByTestId('pipeline-start-new-run')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([
    { line: `POST /api/v1/agents/pipelines/${PIPELINE.rejected}/retry`, body: null },
    { line: 'POST /api/v1/agents/pipelines/trigger', body: { test_run_id: RUN_ID, workflow_id: 'deep' } },
  ])
  assertHermetic(api, errors)
})

test('Stop asks first; then the run is asked to stop at its next stage boundary and shows "stopping"', async ({ page }) => {
  const { handlers, state } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers, ready })
  const running = row(page, PIPELINE.running)
  await running.getByTestId('pipeline-stop').click()
  await expect(running.getByTestId('pipeline-stop-confirm')).toContainText('Stop this run?')
  expect(state.writes).toEqual([])
  await running.getByTestId('pipeline-stop-confirm-yes').click()
  await expect(running.getByTestId('pipeline-action-note')).toHaveText('Stop requested — the run ends at its next stage boundary.')
  await expect(running.getByTestId('pipeline-stopping')).toHaveText('stopping')
  await expect(running.getByTestId('pipeline-stop')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(state.writes.map((w) => w.line)).toEqual([`POST /api/v1/agents/pipelines/${PIPELINE.running}/cancel`])
  assertHermetic(api, errors)
})

test('a VIEWER sees no Retry or Stop and makes no request a viewer is refused', async ({ page }) => {
  const refused: string[] = []
  const forbidden: ApiHandlers = [
    /^\/api\/v1\/agents\/pipelines\/[^/]+\/(retry|cancel)$/,
    /^\/api\/v1\/agents\/pipelines\/trigger$/,
  ].map((pattern) => [pattern, ({ path }) => {
    refused.push(path)
    return respond(403, { detail: 'Requires at least QA_ENGINEER role' })
  }, 'POST'] as const)
  const { handlers } = agentsActionsHandlers()
  const { api, errors } = await openRollout(page, '/agents', { handlers: [...forbidden, ...handlers], user: VIEWER, ready })
  await expect(row(page, PIPELINE.failed)).toBeVisible()
  await expect(page.getByTestId('pipeline-row-actions')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Trigger pipeline' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(refused, 'requests a viewer is refused').toEqual([])
  await expect(page.getByText(/Requires at least/)).toHaveCount(0)
  assertHermetic(api, errors)
})

test('/deep-investigate: a failed deep run is retried from its last successful stage — never a second deep-investigate', async ({ page }) => {
  const { handlers, state } = deepHandlers({ failed: true })
  const verdict = (p: Page) => p.getByRole('region', { name: 'Investigation verdict' })
  const { api, errors } = await openRollout(page, `/deep-investigate?run=${RUN_ID}`, { handlers, ready: verdict })
  await expect(verdict(page)).toContainText('Last run failed')
  const retry = verdict(page).getByTestId('deep-retry')
  await expect(retry).toHaveText('Retry from last successful stage')
  await expect(verdict(page).getByRole('button', { name: /Run on all/ })).toHaveCount(0)
  await retry.click()
  await expect(verdict(page).getByTestId('deep-retry-note')).toHaveText('Resuming from the last successful stage (attempt 2/5).')
  // The status is read again and the page follows the resumed run.
  await expect(verdict(page)).toContainText('Investigation running')
  await networkQuiet(page, api)
  expect(state.writes.map((w) => w.line)).toEqual([`POST /api/v1/agents/pipelines/${DEEP_PIPELINE_ID}/retry`])
  expect(requestsTo(api, `/api/v1/deep-investigate/${RUN_ID}`).filter((line) => line.startsWith('POST'))).toEqual([])
  assertHermetic(api, errors)
})

test('/deep-investigate as a VIEWER: the retry is shown disabled, and nothing a viewer is refused is asked for', async ({ page }) => {
  const refused: string[] = []
  const forbidden: ApiHandlers = [
    [/^\/api\/v1\/settings\/ai$/, 'GET'],
    [/^\/api\/v1\/integration-health\/status$/, 'GET'],
    [/^\/api\/v1\/agents\/pipelines\/[^/]+\/retry$/, 'POST'],
    [/^\/api\/v1\/deep-investigate\/[^/]+$/, 'POST'],
  ].map(([pattern, method]) => [pattern as RegExp, ({ path }) => {
    refused.push(`${method} ${path}`)
    return respond(403, { detail: 'Requires at least QA_LEAD role' })
  }, method as string] as const)
  const { handlers } = deepHandlers({ failed: true })
  const verdict = (p: Page) => p.getByRole('region', { name: 'Investigation verdict' })
  const { api, errors } = await openRollout(page, `/deep-investigate?run=${RUN_ID}`, {
    handlers: [...forbidden, ...handlers],
    user: VIEWER,
    ready: verdict,
  })
  await expect(verdict(page).getByTestId('deep-retry')).toBeDisabled()
  await expect(verdict(page).getByTestId('deep-retry')).toHaveAttribute('title', 'QA Engineer role required')
  await networkQuiet(page, api)
  expect(refused, 'requests a viewer is refused').toEqual([])
  await expect(page.getByText(/Requires at least/)).toHaveCount(0)
  assertHermetic(api, errors)
})
