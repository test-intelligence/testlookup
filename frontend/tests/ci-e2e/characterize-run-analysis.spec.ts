/**
 * W27a PAN-03 CHARACTERIZATION, `/runs/:runId` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.3, rows
 * T1, A1-A3, E1, E2), hermetic. Written against the MODAL tree (the test in an edge side panel, the promotion,
 * the classification correction, the claim evidence and the claim correction in modal dialogs, the decision
 * trail in a fixed drawer) and kept green, UNEDITED, after the in-page conversion: every container is found by
 * the name it keeps (`opened`).
 *
 *  - T1 a failing Tests row → the test opens beside the list (named by the test), its reads exactly (rich detail,
 *    history, step flips once each; the stored AI analysis twice, `paneReads`), `1 of N failures on this page`, Previous disabled
 *    at the first; Next failure → the next failing row, its own reads; `Open full page` links
 *    `/runs/{run}/tests/{test}`; `Close panel` closes it;
 *  - A3 in that test's AI panel, Correct → a category (+ a comment) → Record correction:
 *    `POST /api/v1/feedback/{analysis_id}` `{ rating: 'incorrect', corrected_category, comment? }` and the toast;
 *    Cancel sends nothing; a viewer gets no stored analysis, no Correct and no `/analyze/` read;
 *  - A1 Analysis → File defect (QA engineer+) → the candidate read only once the form opens, the prefill, then
 *    `POST …/clusters/{cluster}/promote` with the whole body (labels split on commas, trimmed, empty dropped;
 *    the key upper-cased; `project_key` absent when blank); `pending_review` keeps the form with its result,
 *    the toast and the Inbox › Approvals link; any other answer toasts `Defect promoted` and closes it; Cancel
 *    sends nothing; a viewer has no File defect;
 *  - A2 Decision trail from the verdict banner, the failure block and the provenance footer: the trail is read
 *    only once it opens; each opener shows it (its search field), `Close decision trail` closes it;
 *  - E1 Evidence → Inspect evidence for a claim → the claim's evidence (named by the claim), `Close evidence` and
 *    Escape close it, nothing is read;
 *  - E2 Correct a claim → Record correction: `POST …/decision-reports/{report}/feedback` with the whole body
 *    (`corrected_value` a boolean for the flaky assessment); Cancel sends nothing.
 *
 * T2 (a Timing bar → its test) is `run-timing-tab.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `runAnalysisWorld()`
 * (`fixtures-in-page.ts`, over `RUN_PAGE`, `TEST_CASE`, `DEEP_INVESTIGATION`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import {
  CLUSTER_ID,
  DECISION_REPORT,
  DEFECT_CANDIDATE,
  PROMOTED_DEFECT_ID,
  runAnalysisWorld,
} from '../visual/production/fixtures-in-page'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

type Api = Parameters<typeof observed>[0]
const testsReady = (p: Page) => p.locator('[data-primary] table')
const analysisReady = (p: Page) => p.locator('section[aria-label="What failed"]')
const RUN_TEST = `/api/v1/runs/${RUN_ID}/tests`
const CANDIDATE = `/api/v1/deep-investigate/${RUN_ID}/clusters/${CLUSTER_ID}/defect-candidate`
const PROMOTE = `POST /api/v1/deep-investigate/${RUN_ID}/clusters/${CLUSTER_ID}/promote`
const TRAIL = `/api/v1/runs/${RUN_ID}/decision-trail`

/** Every request about one test of the run, sorted. */
const readsOf = (api: Api, id: string) => observed(api).filter((line) => line.includes(id)).sort()
/**
 * What opening a test pane reads for it today: the SWR reads once each; the stored AI analysis twice, because
 * `AIAnalysisPanel` reads it in a plain effect and the dev build (the one CI's e2e server runs) mounts under
 * `React.StrictMode`, which runs every effect twice. A conversion that mounts the panel once more reads it more.
 */
const paneReads = (id: string) =>
  [
    `GET ${RUN_TEST}/${id}/rich-detail`,
    `GET ${RUN_TEST}/${id}/history`,
    `GET ${RUN_TEST}/${id}/step-flips`,
    `GET /api/v1/analyze/${id}`,
    `GET /api/v1/analyze/${id}`,
  ].sort()
/** The kinds of request made so far (a poll repeating an existing line is not a new kind). */
const kinds = (api: Api) => [...new Set(observed(api))].sort()

/** The failing rows of the Tests table, in the order the table shows them. */
async function failingRows(page: Page, world: ReturnType<typeof runAnalysisWorld>) {
  const ids = await page.locator('[data-test-row]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-test-row') ?? ''))
  return ids
    .map((id) => world.tests.find((t) => t.id === id))
    .filter((t): t is (typeof world.tests)[number] => !!t && (t.status === 'FAILED' || t.status === 'BROKEN'))
    .map((t) => ({ id: String(t.id), name: String(t.test_name) }))
}

test('T1 a failing row opens the test beside the list with exactly its reads; Next failure; Open full page; Close panel', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: world.handlers, ready: testsReady })
  const failing = await failingRows(page, world)
  expect(failing.length).toBeGreaterThan(2)
  const [first, second] = failing
  await networkQuiet(page, api)
  expect(readsOf(api, first.id), 'nothing is read for a test until it is opened').toEqual([])

  await page.locator(`[data-test-row="${first.id}"]`).click()
  let pane = opened(page, first.name)
  await expect(pane).toBeVisible()
  await expect(pane.locator('[data-test-case-body]')).toBeVisible()
  await expect(pane.getByText(`1 of ${failing.length} failures on this page`)).toBeVisible()
  await expect(pane.getByRole('button', { name: 'Previous failure' })).toBeDisabled()
  await expect(pane.getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', `/runs/${RUN_ID}/tests/${first.id}`)
  await networkQuiet(page, api)
  expect(readsOf(api, first.id)).toEqual(paneReads(first.id))

  await pane.getByRole('button', { name: 'Next failure' }).click()
  pane = opened(page, second.name)
  await expect(pane).toBeVisible()
  await expect(opened(page, first.name)).toHaveCount(0)
  await expect(pane.getByText(`2 of ${failing.length} failures on this page`)).toBeVisible()
  await expect(pane.getByRole('button', { name: 'Previous failure' })).toBeEnabled()
  await expect(pane.getByRole('link', { name: /Open full page/ })).toHaveAttribute('href', `/runs/${RUN_ID}/tests/${second.id}`)
  await networkQuiet(page, api)
  expect(readsOf(api, second.id)).toEqual(paneReads(second.id))
  expect(readsOf(api, first.id), 'the first test is not read again').toEqual(paneReads(first.id))

  await pane.getByRole('button', { name: 'Close panel' }).click()
  await expect(pane).toHaveCount(0)
  await expect(page.locator('[data-test-row]').first()).toBeVisible()
  assertHermetic(api, errors)
})

test('A3 Correct classification in the test pane: the feedback POST with and without a comment; Cancel sends nothing', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: world.handlers, ready: testsReady })
  const [first] = await failingRows(page, world)
  await page.locator(`[data-test-row="${first.id}"]`).click()
  const pane = opened(page, first.name)
  const correct = pane.getByRole('button', { name: 'Correct', exact: true })
  await expect(correct).toBeVisible()

  // Cancel: nothing.
  await correct.click()
  let form = opened(page, 'Correct classification')
  await expect(form).toBeVisible()
  await form.getByRole('radio', { name: /^Infrastructure/ }).click()
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await expect(pane, 'the test stays open').toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // A category and a comment.
  await correct.click()
  form = opened(page, 'Correct classification')
  const record = form.getByRole('button', { name: 'Record correction' })
  await expect(record, 'nothing to record until a category is picked').toBeDisabled()
  await form.getByRole('radio', { name: /^Infrastructure/ }).click()
  await expect(form.getByRole('radio', { name: /^Infrastructure/ })).toHaveAttribute('aria-checked', 'true')
  await form.getByRole('textbox').fill('  A DNS blip in the CI network  ')
  await record.click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Correction recorded — feeds the next training export.')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST /api/v1/feedback/analysis-${first.id}`, body: { rating: 'incorrect', corrected_category: 'INFRASTRUCTURE', comment: 'A DNS blip in the CI network' } },
  ])

  // A category only: no `comment` key at all.
  await correct.click()
  form = opened(page, 'Correct classification')
  await form.getByRole('radio', { name: /^Test Data/ }).click()
  await form.getByRole('button', { name: 'Record correction' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    { line: `POST /api/v1/feedback/analysis-${first.id}`, body: { rating: 'incorrect', corrected_category: 'TEST_DATA' } },
  ])
  assertHermetic(api, errors)
})

test('A3 role gating: a viewer\'s test pane reads no stored analysis and offers no Correct', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: world.handlers, ready: testsReady, user: VIEWER })
  const [first] = await failingRows(page, world)
  await page.locator(`[data-test-row="${first.id}"]`).click()
  const pane = opened(page, first.name)
  await expect(pane.locator('[data-test-case-body]')).toBeVisible()
  await expect(pane.getByText('AI root cause analysis is available to QA engineers and above.')).toBeVisible()
  await expect(pane.getByRole('button', { name: 'Correct', exact: true })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, /^\/api\/v1\/analyze\//)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

/** Open File defect on the one failure block; the candidate has prefilled the form. */
async function openPromotion(page: Page) {
  await analysisReady(page).getByRole('button', { name: 'File defect' }).click()
  const form = opened(page, 'Promote to Defect')
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Title *')).toHaveValue(DEFECT_CANDIDATE.title)
  return form
}

test('A1 File defect → pending_review: the candidate read on open, the whole POST body, the form stays with its result and the Approvals link', async ({ page }) => {
  const world = runAnalysisWorld({ approval: 'pending_review' })
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: world.handlers, ready: analysisReady })
  await networkQuiet(page, api)
  expect(requestsTo(api, CANDIDATE), 'no candidate read until the form opens').toEqual([])

  const form = await openPromotion(page)
  await expect(form.getByLabel('Component')).toHaveValue(DEFECT_CANDIDATE.component)
  await expect(form.getByLabel('Owner Team')).toHaveValue(DEFECT_CANDIDATE.owner_team)
  await expect(form.getByLabel(/^Labels/)).toHaveValue('regression, cluster-promoted')
  await expect(form.getByLabel('Description')).toHaveValue(DEFECT_CANDIDATE.description)
  await form.getByRole('group', { name: 'Severity' }).getByRole('button', { name: 'CRITICAL' }).click()
  await form.getByLabel(/^Labels/).fill(' regression , , flaky-infra ,')
  await form.getByLabel('Title *').fill('  Gateway timeouts block checkout  ')
  await form.getByLabel(/^Jira Project Key/).fill('qa')
  await expect(form.getByLabel(/^Jira Project Key/), 'upper-cased as typed').toHaveValue('QA')
  await form.getByRole('button', { name: 'Promote to Defect' }).click()

  await expect(form.getByText('Defect created — awaiting approval', { exact: true }).first()).toBeVisible()
  await expect(form, 'a promotion awaiting approval stays on screen').toBeVisible()
  await expect(form.getByText(PROMOTED_DEFECT_ID)).toBeVisible()
  await expect(form.getByRole('link', { name: 'Review it in Inbox › Approvals' })).toHaveAttribute('href', '/my-failures?tab=approvals&source=defects')
  await expect(page.getByText('Defect created — awaiting approval', { exact: true })).toHaveCount(2)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: PROMOTE,
      body: {
        title: 'Gateway timeouts block checkout',
        severity: 'CRITICAL',
        component: DEFECT_CANDIDATE.component,
        owner_team: DEFECT_CANDIDATE.owner_team,
        labels: ['regression', 'flaky-infra'],
        description: DEFECT_CANDIDATE.description,
        project_key: 'QA',
      },
    },
  ])
  expect(requestsTo(api, CANDIDATE), 'the candidate is read once').toHaveLength(1)
  await form.getByRole('button', { name: 'Close', exact: true }).last().click()
  await expect(form).toHaveCount(0)
  assertHermetic(api, errors)
})

test('A1 File defect → Cancel sends nothing (the candidate was read)', async ({ page }) => {
  const world = runAnalysisWorld({ approval: null })
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: world.handlers, ready: analysisReady })
  const form = await openPromotion(page)
  await form.getByLabel('Title *').fill('never sent')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, CANDIDATE)).toHaveLength(1)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('A1 File defect (QA engineer) → promoted: no project_key when blank, the toast, and the form closes', async ({ page }) => {
  const world = runAnalysisWorld({ approval: null })
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: world.handlers, ready: analysisReady, user: QA_ENGINEER })
  const form = await openPromotion(page)
  await form.getByRole('button', { name: 'Promote to Defect' }).click()
  await expect(page.getByText('Defect promoted', { exact: true })).toBeVisible()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: PROMOTE,
      body: {
        title: DEFECT_CANDIDATE.title,
        severity: 'HIGH',
        component: DEFECT_CANDIDATE.component,
        owner_team: DEFECT_CANDIDATE.owner_team,
        labels: DEFECT_CANDIDATE.labels,
        description: DEFECT_CANDIDATE.description,
      },
    },
  ])
  assertHermetic(api, errors)
})

test('A1 role gating: a viewer has no File defect and reads no candidate', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: world.handlers, ready: analysisReady, user: VIEWER })
  await expect(analysisReady(page).getByRole('button', { name: 'Decision trail' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'File defect' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, CANDIDATE)).toEqual([])
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('A2 Decision trail from the banner, the failure block and the provenance footer: read only once it opens', async ({ page }) => {
  const world = runAnalysisWorld()
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=analysis`, { handlers: world.handlers, ready: analysisReady })
  await networkQuiet(page, api)
  expect(requestsTo(api, TRAIL), 'the trail is not read with the tab').toEqual([])
  const trail = opened(page, 'AI decision trail')
  await expect(trail).toHaveCount(0)

  const openers = [
    page.locator('section[aria-label="Release verdict"]').getByRole('button', { name: 'Decision trail' }),
    analysisReady(page).getByRole('button', { name: 'Decision trail' }),
  ]
  for (const opener of openers) {
    await opener.click()
    await expect(trail).toBeVisible()
    await expect(trail.getByRole('searchbox', { name: 'Search decision trail' })).toBeVisible()
    await trail.getByRole('button', { name: 'Close decision trail' }).click()
    await expect(trail).toHaveCount(0)
  }
  expect(requestsTo(api, TRAIL).length, 'read once it opened').toBeGreaterThan(0)

  // The provenance footer sits in the collapsed "Evidence behind the verdict".
  await page.getByRole('button', { name: /Evidence behind the verdict/ }).click()
  await page.getByRole('button', { name: /^Decision trail/ }).last().click()
  await expect(trail).toBeVisible()
  await expect(trail.getByRole('searchbox', { name: 'Search decision trail' })).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('E1 Inspect evidence for a claim: its evidence, closed by "Close evidence" and by Escape, and nothing is read', async ({ page }) => {
  const world = runAnalysisWorld({ decisionReport: true })
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=evidence`, {
    handlers: world.handlers,
    ready: (p) => p.getByRole('heading', { name: 'Decision intelligence' }),
  })
  await networkQuiet(page, api)
  const loaded = kinds(api)

  await page.getByRole('button', { name: 'Inspect evidence for fact-1' }).click()
  let evidence = opened(page, '12 of 197 tests failed.')
  await expect(evidence).toBeVisible()
  await expect(evidence.getByText('metric-failed')).toBeVisible()
  await expect(evidence.getByText('Confidence basis:')).toBeVisible()
  await evidence.getByRole('button', { name: 'Close evidence' }).click()
  await expect(evidence).toHaveCount(0)

  await page.getByRole('button', { name: 'Inspect evidence for inference-1' }).click()
  evidence = opened(page, 'The gateway timeouts cause the Payments failures.')
  await expect(evidence.getByText('cluster-cl-1')).toBeVisible()
  await expect(evidence.getByText('quality-1')).toBeVisible()
  await evidence.getByRole('button', { name: 'Close evidence' }).focus()
  await page.keyboard.press('Escape')
  await expect(evidence).toHaveCount(0)
  await networkQuiet(page, api)
  expect(kinds(api), 'inspecting evidence asks for nothing').toEqual(loaded)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('E2 Correct a claim → Record correction: the whole feedback body (a boolean for the flaky assessment); Cancel sends nothing', async ({ page }) => {
  const world = runAnalysisWorld({ decisionReport: true })
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=evidence`, {
    handlers: world.handlers,
    ready: (p) => p.getByRole('heading', { name: 'Decision intelligence' }),
  })
  const feedback = page.getByTestId('decision-report-feedback')
  await expect(feedback.getByText('Correct a claim:')).toBeVisible()
  const FEEDBACK = `POST /api/v1/runs/${RUN_ID}/decision-reports/${DECISION_REPORT.report_id}/feedback`

  // Cancel: nothing.
  await feedback.getByRole('button', { name: 'inference-1', exact: true }).click()
  let form = opened(page, 'Correct claim')
  await expect(form).toBeVisible()
  await expect(form.getByText('The gateway timeouts cause the Payments failures.')).toBeVisible()
  await form.getByLabel('Correct value').fill('never sent')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])

  // A root-cause correction, with the claim's own evidence preselected.
  await feedback.getByRole('button', { name: 'inference-1', exact: true }).click()
  form = opened(page, 'Correct claim')
  await expect(form.getByLabel('Supporting evidence')).toHaveValue('cluster-cl-1')
  await form.getByLabel('Correction type').selectOption('cause')
  await form.getByLabel('Correct value').fill('  A DNS outage in the CI network  ')
  await form.getByLabel('Reason').fill('  The resolver logs show it  ')
  await form.getByLabel('Supporting evidence').selectOption('quality-1')
  await form.getByRole('button', { name: 'Record correction' }).click()
  await expect(form).toHaveCount(0)
  await expect(feedback.getByRole('status')).toHaveText('Correction recorded for this report version.')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: FEEDBACK,
      body: {
        report_version: DECISION_REPORT.report_version,
        feedback_kind: 'claim_correction',
        claim_id: 'inference-1',
        correction_type: 'cause',
        corrected_value: 'A DNS outage in the CI network',
        reason: 'The resolver logs show it',
        evidence_ids: ['quality-1'],
      },
    },
  ])

  // The flaky assessment: the value travels as a boolean.
  await feedback.getByRole('button', { name: 'fact-1', exact: true }).click()
  form = opened(page, 'Correct claim')
  await form.getByLabel('Correction type').selectOption('flaky')
  await form.getByLabel('Correct value').fill('TRUE')
  await form.getByLabel('Reason').fill('It passes on retry')
  await form.getByRole('button', { name: 'Record correction' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes.slice(1)).toEqual([
    {
      line: FEEDBACK,
      body: {
        report_version: DECISION_REPORT.report_version,
        feedback_kind: 'claim_correction',
        claim_id: 'fact-1',
        correction_type: 'flaky',
        corrected_value: true,
        reason: 'It passes on retry',
        evidence_ids: ['metric-failed'],
      },
    },
  ])
  assertHermetic(api, errors)
})
