/**
 * GAP-D06 (test-coverage round 2026-11-18, card TEST-D), hermetic: `/getting-started` (the Help menu and every empty
 * chart's call to action, every role) had no browser journey.
 *
 *  - As a VIEWER and as a TESTER (one context each): the checklist renders from `POST
 *    /api/v1/onboarding/{project}/detect`: five steps by label, in order, the done one marked Completed, the bar's
 *    "2 of 5 steps completed"; no Skip and no Restore (S-D-7: those writes need a QA lead), and nothing is written.
 *  - As a QA lead: Skip on a pending step sends exactly one `POST …/skip` with `{"step_key": …}`, the step turns
 *    skipped (Restore replaces Set Up) and the line adds "· 1 skipped", with no reload and no toast; Restore sends
 *    exactly one `POST …/restore` with the same key, and the step is pending again (Set Up is back).
 *
 * The fixture answers as `onboarding_service` does since D-127: a skipped step is not counted done, and the
 * percentage is over the steps not skipped.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import type { ApiHandlers, ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout } from '../lib/rollout'
import { LAYOUT, PROJECT_ID, USER as QA_LEAD } from '../visual/production/fixtures'
import { TESTER, VIEWER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const BASE = `/api/v1/onboarding/${P}`

type StepStatus = 'pending' | 'completed' | 'skipped'
const STEPS: { key: string; label: string; description: string; status: StepStatus; completed_at: string | null }[] = [
  { key: 'create_project', label: 'Create a project', description: 'Name the project your runs report to.', status: 'completed', completed_at: '2026-09-10T09:00:00Z' },
  { key: 'upload_run', label: 'Upload a test run', description: 'Send results from CI or the CLI.', status: 'completed', completed_at: '2026-09-11T09:00:00Z' },
  { key: 'connect_jira', label: 'Connect Jira', description: 'Link defects to issues.', status: 'pending', completed_at: null },
  { key: 'connect_telemetry', label: 'Stream live results', description: 'Watch runs as they happen.', status: 'pending', completed_at: null },
  { key: 'view_intelligence', label: 'Open Run Intelligence', description: 'Read the AI report of a run.', status: 'pending', completed_at: null },
]

/** The server's status for a set of step states (D-127: skipped is not done; the share is over the open steps). */
function statusOf(states: Record<string, StepStatus>) {
  const steps = STEPS.map((s) => ({ ...s, status: states[s.key] }))
  const done = steps.filter((s) => s.status === 'completed').length
  const skipped = steps.filter((s) => s.status === 'skipped').length
  const open = steps.length - skipped
  return {
    project_id: P,
    steps,
    completed_count: done,
    skipped_count: skipped,
    total_count: steps.length,
    progress_pct: open ? Math.round((done / open) * 100) : 100,
    is_complete: done === open,
  }
}

function onboardingWorld() {
  const states: Record<string, StepStatus> = Object.fromEntries(STEPS.map((s) => [s.key, s.status]))
  const writes: { line: string; body: unknown }[] = []
  const write = (to: StepStatus) => (request: ApiRequest) => {
    const body = JSON.parse(request.route.request().postData() ?? '{}') as { step_key?: string }
    writes.push({ line: `${request.method} ${request.path}`, body })
    if (body.step_key) states[body.step_key] = to
    return statusOf(states)
  }
  const handlers: ApiHandlers = [
    [`${BASE}/detect`, () => statusOf(states), 'POST'],
    [`${BASE}/skip`, write('skipped'), 'POST'],
    [`${BASE}/restore`, write('pending'), 'POST'],
    ...LAYOUT,
  ]
  return { handlers, writes }
}

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Getting Started', exact: true })
const toasts = (page: Page) => page.locator('div[style*="z-index: 9999"] [role="status"]')
/** A step's card, by its label. */
const card = (page: Page, label: string): Locator =>
  page.locator('#main-content .card').filter({ has: page.getByText(label, { exact: true }) })
const labels = (page: Page) => page.locator('#main-content .card p.font-medium')

for (const user of [VIEWER, TESTER]) {
  test(`/getting-started as ${user.role}: the five steps render, with no Skip or Restore and no write`, async ({ page }) => {
    const world = onboardingWorld()
    const { api, errors } = await openRollout(page, '/getting-started', { handlers: world.handlers, ready, user })
    await expect(labels(page)).toHaveText(STEPS.map((s) => s.label))
    await expect(card(page, 'Create a project')).toContainText('Completed')
    await expect(card(page, 'Create a project').getByRole('button', { name: 'View →' })).toBeVisible()
    await expect(page.getByRole('progressbar', { name: 'Setup progress' })).toHaveAttribute('aria-valuenow', '40')
    await expect(page.getByText(/^2 of 5\s+steps completed$/)).toBeVisible()
    await expect(card(page, 'Connect Jira').getByRole('button', { name: 'Set Up' })).toBeVisible()
    // S-D-7: skip and restore change the whole team's checklist, so they are a QA lead's.
    await expect(page.getByTitle('Skip this step')).toHaveCount(0)
    await expect(page.getByTitle('Restore this skipped step')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(world.writes).toEqual([])
    await expect(toasts(page), `/getting-started as ${user.role}: a toast`).toHaveCount(0)
    assertHermetic(api, errors)
  })
}

test('/getting-started as a QA lead: Skip and Restore each send one write and redraw in place', async ({ page }) => {
  const world = onboardingWorld()
  const { api, errors } = await openRollout(page, '/getting-started', { handlers: world.handlers, ready, user: QA_LEAD })
  await expect(labels(page)).toHaveText(STEPS.map((s) => s.label))
  await expect(page.getByText(/^2 of 5\s+steps completed$/)).toBeVisible()

  const jira = card(page, 'Connect Jira')
  await expect(jira.getByRole('button', { name: 'Set Up' })).toBeVisible()
  await jira.getByTitle('Skip this step').click()
  await expect(jira.getByRole('button', { name: /Restore/ })).toBeVisible()
  await expect(jira.getByRole('button', { name: 'Set Up' })).toHaveCount(0)
  await expect(page.getByText(/^2 of 5\s+steps completed\s*·\s*1 skipped$/)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `POST ${BASE}/skip`, body: { step_key: 'connect_jira' } }])

  await jira.getByRole('button', { name: /Restore/ }).click()
  await expect(jira.getByRole('button', { name: 'Set Up' })).toBeVisible()
  await expect(page.getByText(/^2 of 5\s+steps completed$/)).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${BASE}/skip`, body: { step_key: 'connect_jira' } },
    { line: `POST ${BASE}/restore`, body: { step_key: 'connect_jira' } },
  ])
  // The checklist was read once; the writes answered with the new status, so nothing was re-read.
  expect(observed(api).filter((line) => line === `POST ${BASE}/detect`)).toHaveLength(1)
  await expect(toasts(page), '/getting-started as QA_LEAD: a toast').toHaveCount(0)
  assertHermetic(api, errors)
})
