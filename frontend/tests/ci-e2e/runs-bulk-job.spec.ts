/**
 * W14 AGT-12 (`docs/deferred-scope-2026-10-13/work/W14-contract.md` §2.4 test 9, §4.2), hermetic, on `/runs`:
 * bulk triggers are tracked jobs.
 *
 *  - Status "Failed" → ⋯ "Re-trigger all matching (6)" (the list's COUNT) → an in-page question naming it →
 *    ONE `POST /agents/pipelines/bulk-trigger` carrying the list's OWN filter and a client token → the panel above
 *    the table: `6 requested · 5 queued · 1 already running`, then (the world drains the job one step per poll,
 *    every 5 s) counting up to Finished, after which nothing is asked again; `?bulk_job=` keeps it;
 *  - a second job (a selection) is cancelled through its own question;
 *  - bulk deep under Rules is ONE refused request: the server's sentence where the panel would be, no job read;
 *  - a reload with `?bulk_job=` reopens the job; Close clears it; `/runs` alone asks no bulk-job read.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Worlds: `fixtures-agent-ops-b.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, runs } from '../visual/production/fixtures'
import { BULK_JOB_IDS, bulkJobWorld, retriggerWorld, RULES_SENTENCE } from '../visual/production/fixtures-agent-ops-b'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-status-banner]')
const panel = (p: Page) => p.getByRole('region', { name: 'Bulk AI analysis' })
const failedRuns = runs().filter((run) => run.status === 'FAILED')
const POLL = { timeout: 20_000 }

async function moreActions(page: Page, item: string | RegExp) {
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: item }).click()
}

test('Re-trigger all matching: the list\'s filter, one request, a panel that counts up and then stops asking', async ({ page }) => {
  const world = bulkJobWorld()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready })
  await networkQuiet(page, api)
  expect(requestsTo(api, /^\/api\/v1\/agents\/pipelines\/bulk-jobs\//), 'no job read on load').toEqual([])

  await page.getByRole('combobox', { name: 'Run status' }).selectOption({ label: 'Failed' })
  await expect(page.locator('[data-primary] tr[id^="run-row-"]')).toHaveCount(failedRuns.length)
  await moreActions(page, `Re-trigger all matching (${failedRuns.length})`)
  const confirm = page.getByTestId('runs-retrigger-confirm')
  await expect(confirm).toContainText(`Re-trigger the AI pipeline for ${failedRuns.length} runs matching this filter?`)
  expect(world.state.writes, 'the question sends nothing').toEqual([])
  await confirm.getByRole('button', { name: 'Re-trigger' }).click()

  await expect(panel(page)).toBeVisible()
  expect(world.state.writes).toHaveLength(1)
  const [write] = world.state.writes
  expect(write.line).toBe('POST /api/v1/agents/pipelines/bulk-trigger')
  const body = write.body as { run_filter: unknown; workflow_type: string; client_token: string }
  expect(body.run_filter).toEqual({ project_id: PROJECT_ID, days: 30, status: 'FAILED' })
  expect(body.workflow_type).toBe('offline')
  expect(body.client_token).toMatch(/^[0-9a-f]{32}$/)
  expect(new URL(page.url()).searchParams.get('bulk_job')).toBe(BULK_JOB_IDS[0])
  await expectNoOverlay(page)

  await expect(panel(page)).toContainText(`${failedRuns.length} requested · ${failedRuns.length - 1} queued · 1 already running`)
  await expect(panel(page)).toContainText('0 completed · 0 passed · 0 failed · 2 running · 3 not started')
  await expect(panel(page)).toContainText('2 completed · 1 passed · 1 failed · 2 running · 1 not started', POLL)
  await expect(panel(page)).toContainText('5 completed · 4 passed · 1 failed · 0 running · 0 not started', POLL)
  await expect(panel(page).locator('[data-bulk-job-state]')).toHaveText('Finished')
  // Finished: no Cancel, and the reads stop.
  await expect(panel(page).getByRole('button', { name: 'Cancel' })).toHaveCount(0)
  const reads = requestsTo(api, `/api/v1/agents/pipelines/bulk-jobs/${BULK_JOB_IDS[0]}`).length
  expect(reads).toBe(3)
  await page.waitForTimeout(6_000)
  expect(requestsTo(api, `/api/v1/agents/pipelines/bulk-jobs/${BULK_JOB_IDS[0]}`)).toHaveLength(reads)
  assertHermetic(api, errors)
})

test('a second job is cancelled through its own question; its reads stop', async ({ page }) => {
  const world = bulkJobWorld({ steps: [{ completed: 0, failed: 0, running: 1 }] })
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready })
  await moreActions(page, `Trigger all failed (${failedRuns.length})`)
  await expect(panel(page)).toContainText(`${failedRuns.length} requested`)

  // A second job: two selected runs. The panel follows the newest job.
  await page.getByRole('checkbox', { name: `Select #${failedRuns[0].build_number}` }).check()
  await page.getByRole('checkbox', { name: `Select #${failedRuns[1].build_number}` }).check()
  await page.getByRole('button', { name: 'Bulk trigger' }).click()
  await expect(panel(page)).toContainText('2 requested · 2 queued · 0 already running · 0 not found')
  await expect(page).toHaveURL(new RegExp(`bulk_job=${BULK_JOB_IDS[1]}`))

  await panel(page).getByRole('button', { name: 'Cancel' }).click()
  const question = page.getByTestId('bulk-job-cancel-confirm')
  await expect(question).toContainText('Cancel this bulk job?')
  await question.getByRole('button', { name: 'Cancel job' }).click()
  await expect(panel(page).locator('[data-bulk-job-state]')).toHaveText('Cancelled')
  expect(world.state.writes.map((w) => w.line)).toEqual([
    'POST /api/v1/agents/pipelines/bulk-trigger',
    'POST /api/v1/agents/pipelines/bulk-trigger',
    `POST /api/v1/agents/pipelines/bulk-jobs/${BULK_JOB_IDS[1]}/cancel`,
  ])
  expect((world.state.writes[1].body as { run_ids: string[] }).run_ids).toEqual([failedRuns[0].id, failedRuns[1].id])
  const reads = requestsTo(api, `/api/v1/agents/pipelines/bulk-jobs/${BULK_JOB_IDS[1]}`).length
  await page.waitForTimeout(6_000)
  expect(requestsTo(api, `/api/v1/agents/pipelines/bulk-jobs/${BULK_JOB_IDS[1]}`)).toHaveLength(reads)
  assertHermetic(api, errors)
})

test('bulk deep under Rules: ONE refused request, the server\'s sentence, no job', async ({ page }) => {
  const world = bulkJobWorld({ deepRefused: true })
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready })
  for (const run of failedRuns.slice(0, 3)) {
    await page.getByRole('checkbox', { name: `Select #${run.build_number}` }).check()
  }
  await page.getByRole('button', { name: 'Bulk deep' }).click()
  const note = page.locator('[data-bulk-note]')
  await expect(note).toHaveText(RULES_SENTENCE)
  await expect(note).toHaveAttribute('role', 'note')
  await expect(panel(page)).toHaveCount(0)
  expect(world.state.writes).toHaveLength(1)
  expect(world.state.writes[0].body).toMatchObject({ run_ids: failedRuns.slice(0, 3).map((r) => r.id), workflow_type: 'deep' })
  await networkQuiet(page, api)
  expect(requestsTo(api, /^\/api\/v1\/deep-investigate\//), 'nothing fanned out per run').toEqual([])
  expect(requestsTo(api, /^\/api\/v1\/agents\/pipelines\/bulk-jobs\//)).toEqual([])
  assertHermetic(api, errors)
})

test('?bulk_job= reopens the job after a reload; Close clears it', async ({ page }) => {
  const world = retriggerWorld()
  const { api, errors } = await openRollout(page, `/runs?bulk_job=${BULK_JOB_IDS[0]}`, { handlers: world.handlers, ready })
  await expect(panel(page)).toContainText(`${failedRuns.length} requested · ${failedRuns.length - 1} queued · 1 already running`)
  await expect(panel(page)).toContainText('2 completed · 1 passed · 1 failed · 2 running · 1 not started')
  await expect(panel(page).getByRole('link', { name: 'Open Pipeline runs' })).toHaveAttribute('href', '/agents')
  await page.reload()
  await expect(panel(page)).toContainText(`${failedRuns.length} requested`)
  await panel(page).getByRole('button', { name: 'Close' }).click()
  await expect(panel(page)).toHaveCount(0)
  expect(new URL(page.url()).searchParams.get('bulk_job')).toBeNull()
  expect(world.state.writes).toEqual([])
  assertHermetic(api, errors)
})
