/**
 * W22b ACT-03 export before delete on `/settings/retention`, hermetic (`docs/deferred-scope-2026-10-13/work/
 * W22b-contract.md` §4.1, §6.3), at 1440 and 1280:
 *
 *  - ADMIN: the criteria preview shows `Summary — archive ≈ …`, `Reclaims ≈ …` and `Archive, not a backup — it
 *    cannot be re-imported.`; the over-cap `Full` tier is disabled; `Download an archive first` + `Evidence` → the
 *    execute body carries `{export: {tier: "evidence", target: "collect"}}`; the jobs card shows `Awaiting
 *    collection`; `Collect` → `Download archive` saves the ZIP (the download event, its full size) → `I have saved
 *    it` → the body `{manifest_sha256: <the X-Manifest-Sha256 header>}` → the row reads `Queued`; nothing in the DOM
 *    names a storage key or a URL;
 *  - every refusal the world scripts reads as its sentence, inline: a 409 `manifest_mismatch` on acknowledge, a 409
 *    `collect_expired` on collect (a JSON body on a blob request), an incomplete download, a 409 on execute;
 *  - an `expired` row shows `Not collected within 72 hours — nothing was deleted; the archive was removed.`;
 *  - the jobs table keeps ≥ 40 px of slack (CI's DejaVu is wider than Windows' fonts); nothing scrolls sideways.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-retention-b.ts` `retentionExportWorld`.
 */
import { statSync } from 'node:fs'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { expectMainFits, inPageLine } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  WORLD_ARCHIVE,
  retentionExportWorld,
  worldAwaitingJob,
  worldExpiredJob,
  worldJobs,
  type RetentionExportWorld,
  type RetentionExportWorldOptions,
} from '../visual/production/fixtures-retention-b'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const NOT_A_BACKUP = 'Archive, not a backup — it cannot be re-imported.'
const JOB_ID = 'del-job-w22a-1'
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const jobsCard = (page: Page) => page.locator('[data-deletion-jobs]')
const jobRow = (page: Page, id: string) => jobsCard(page).locator(`tr[data-deletion-job="${id}"]`)
const collectRow = (page: Page, id: string) => jobsCard(page).locator(`[data-deletion-job-collect="${id}"]`)
const writesTo = (world: RetentionExportWorld, suffix: string) =>
  world.writes.filter((w) => w.line.endsWith(suffix))

/** `target`'s width minus its max-content width (no line wrapped), px — the DejaVu plan's measure. */
async function maxContentSlack(target: Locator): Promise<number> {
  await target.evaluate(() => document.fonts.ready)
  return target.evaluate((el: HTMLElement) => {
    const width = el.getBoundingClientRect().width
    const before = el.style.width
    el.style.width = 'max-content'
    const max = el.getBoundingClientRect().width
    el.style.width = before
    if (el.getAttribute('style') === '') el.removeAttribute('style')
    return Math.round(width - max)
  })
}

async function open(page: Page, options: RetentionExportWorldOptions = {}) {
  const world = retentionExportWorld({ role: 'ADMIN', ...options })
  const opened_ = await openRollout(page, '/settings/retention', { handlers: world.handlers, ready: h1('Retention & Purge'), user: world.user })
  await expect(jobsCard(page)).toBeVisible()
  await networkQuiet(page, opened_.api)
  return { world, ...opened_ }
}

/** Older than 30 days → Preview; the preview box. */
async function preview(page: Page): Promise<Locator> {
  await page.getByLabel('Older than days').fill('30')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  const box = page.getByTestId('criteria-preview')
  await expect(box.locator('[data-criteria-export]')).toBeVisible()
  return box
}

/** Delete 3 runs → type the name → Delete permanently. */
async function deleteTyped(page: Page, box: Locator) {
  await box.getByRole('button', { name: 'Delete 3 runs' }).click()
  const form = opened(page, 'Delete 3 runs')
  await form.getByLabel('Type Checkout to confirm.').fill('Checkout')
  await form.getByRole('button', { name: 'Delete permanently' }).click()
  return form
}

/** Collect → Download archive; the download event. */
async function download(page: Page, id: string) {
  await jobRow(page, id).getByRole('button', { name: 'Collect', exact: true }).click()
  const row = collectRow(page, id)
  await expect(row.getByRole('button', { name: 'I have saved it' })).toBeDisabled()
  const saved = page.waitForEvent('download')
  await row.getByRole('button', { name: /^Download archive/ }).click()
  return { row, file: await saved }
}

for (const [width, height] of [[1440, 900], [1280, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`estimates → collect + evidence → download → I have saved it → Queued — ${width}`, async ({ page }) => {
      const { world, api, errors } = await open(page)
      await expect(jobsCard(page).getByText('No deletion jobs yet.')).toBeVisible()
      const box = await preview(page)
      const step = box.locator('[data-criteria-export]')
      await expect(step.getByText(/^Summary — archive ≈ \d/)).toBeVisible()
      await expect(step.getByText('Reclaims ≈ 180 MB', { exact: true })).toBeVisible()
      await expect(step.getByText(NOT_A_BACKUP, { exact: true })).toBeVisible()
      await expect(step.getByRole('radio', { name: 'Delete without an archive' })).toBeChecked()

      await step.getByRole('radio', { name: 'Download an archive first' }).check()
      const full = step.getByRole('radio', { name: /^Full — archive/ })
      await expect(full, 'the over-cap tier').toBeDisabled()
      await expect(step.locator('label[title="Over the 512 MB limit"]')).toHaveText(/^Full — archive ≈ 1\.2 GB$/)
      await step.getByRole('radio', { name: /^Evidence — archive/ }).check()

      const form = await deleteTyped(page, box)
      await expect(form).toHaveCount(0)
      await expect(page.getByText('Archiving 3 runs — download it from Deletion jobs when it is ready', { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      expect(writesTo(world, '/deletion/execute').map((w) => w.body)).toEqual([
        { job_id: JOB_ID, confirmation_name: 'Checkout', export: { tier: 'evidence', target: 'collect' } },
      ])

      await expect(jobRow(page, JOB_ID).locator('[data-deletion-job-status]')).toHaveText('Awaiting collection')
      const { row, file } = await download(page, JOB_ID)
      expect(file.suggestedFilename()).toMatch(/^testlookup-deletion-archive-.+\.zip$/)
      expect(statSync(await file.path()).size, 'the whole archive was saved').toBe(WORLD_ARCHIVE.bytes.length)
      await expect(row.getByText(NOT_A_BACKUP, { exact: true })).toBeVisible()
      await expect(row.getByText(/^Collect by .+ — after that nothing is deleted and the archive is removed\.$/)).toBeVisible()
      const ack = row.getByRole('button', { name: 'I have saved it' })
      await expect(ack).toBeEnabled()
      await ack.click()
      await expect(jobRow(page, JOB_ID).locator('[data-deletion-job-status]')).toHaveText('Queued')
      await networkQuiet(page, api)
      expect(writesTo(world, '/acknowledge').map((w) => w.body)).toEqual([{ manifest_sha256: WORLD_ARCHIVE.manifestSha256 }])

      // No storage key, presigned URL or object URL is left in the page.
      expect(await page.locator('a[download], a[href^="blob:"], [href*="exports/"]').count()).toBe(0)
      expect(await page.locator('#main-content').innerHTML()).not.toContain('exports/')
      await expectMainFits(page, `retention export @${width}`)
      assertHermetic(api, errors)
    })

    test(`the jobs table keeps ≥ 40 px unwrapped, with every kind of row — ${width}`, async ({ page }) => {
      const { api, errors } = await open(page, { jobs: worldJobs() })
      const table = jobsCard(page).locator('table')
      await expect(table.locator('tbody tr[data-deletion-job]')).toHaveCount(4)
      await expect(jobsCard(page).getByText('net 166 MB · ratio 1:13', { exact: true })).toBeVisible()
      const slack = await maxContentSlack(table)
      console.log(inPageLine('deletion jobs table (max-content)', width, { slack }))
      expect(slack, `the jobs table @${width}: ≥ 40 px of slack with no line wrapped`).toBeGreaterThanOrEqual(40)
      await table.locator('tbody tr[data-status="awaiting_collection"]').getByRole('button', { name: 'Collect', exact: true }).click()
      await expect(table.locator('[data-deletion-job-collect]')).toBeVisible()
      await expectMainFits(page, `deletion jobs @${width}`)
      assertHermetic(api, errors)
    })
  })
}

test.describe('refusals read as their sentences, inline', () => {
  test.use({ viewport: { width: 1280, height: 900 } })

  test('a 409 manifest_mismatch on acknowledge', async ({ page }) => {
    const job = worldAwaitingJob()
    const { world, api, errors } = await open(page, { jobs: [job], acknowledgeError: { status: 409, code: 'manifest_mismatch' } })
    const { row } = await download(page, job.id)
    await row.getByRole('button', { name: 'I have saved it' }).click()
    await expect(row.getByRole('alert')).toHaveText("That manifest checksum is not this job's archive; download it again.")
    await expect(jobRow(page, job.id).locator('[data-deletion-job-status]')).toHaveText('Awaiting collection')
    await networkQuiet(page, api)
    expect(writesTo(world, '/acknowledge')).toHaveLength(1)
    assertHermetic(api, errors)
  })

  test('a 409 collect_expired on collect (a JSON body on a blob request)', async ({ page }) => {
    const job = worldAwaitingJob()
    const { api, errors } = await open(page, { jobs: [job], archive: { error: { status: 409, code: 'collect_expired' } } })
    await jobRow(page, job.id).getByRole('button', { name: 'Collect', exact: true }).click()
    const row = collectRow(page, job.id)
    await row.getByRole('button', { name: /^Download archive/ }).click()
    await expect(row.getByRole('alert')).toHaveText('The collection window has closed; nothing was deleted. Preview again to start over.')
    await expect(row.getByRole('button', { name: 'I have saved it' })).toBeDisabled()
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('an incomplete download keeps "I have saved it" disabled', async ({ page }) => {
    const job = worldAwaitingJob()
    const { world, api, errors } = await open(page, { jobs: [job], archive: { truncate: true } })
    const { row } = await download(page, job.id)
    await expect(row.getByRole('alert')).toHaveText('The download was incomplete; download it again.')
    await expect(row.getByRole('button', { name: 'I have saved it' })).toBeDisabled()
    await networkQuiet(page, api)
    expect(writesTo(world, '/acknowledge')).toEqual([])
    assertHermetic(api, errors)
  })

  test('a 409 on execute stays in the export step; the job stays previewed', async ({ page }) => {
    const { world, api, errors } = await open(page, { executeError: { status: 409, code: 'too_many_runs' } })
    const box = await preview(page)
    const step = box.locator('[data-criteria-export]')
    await step.getByRole('radio', { name: 'Keep an archive in storage' }).check()
    const form = await deleteTyped(page, box)
    await expect(step.getByRole('alert')).toHaveText('An archive covers at most 2000 runs; narrow the criteria.')
    await expect(form, 'the typed confirmation stays open').toBeVisible()
    await networkQuiet(page, api)
    expect(writesTo(world, '/deletion/execute').map((w) => w.body)).toEqual([
      { job_id: JOB_ID, confirmation_name: 'Checkout', export: { tier: 'summary', target: 'retain' } },
    ])
    expect(world.state.jobs).toEqual([])
    assertHermetic(api, errors)
  })

  test('an expired job says nothing was deleted', async ({ page }) => {
    const job = worldExpiredJob()
    const { api, errors } = await open(page, { jobs: [job] })
    await expect(jobRow(page, job.id).locator('[data-deletion-job-status]')).toHaveText('Expired')
    await expect(jobsCard(page).getByText('Not collected within 72 hours — nothing was deleted; the archive was removed.', { exact: true })).toBeVisible()
    await expect(jobRow(page, job.id).getByRole('button', { name: 'Collect' })).toHaveCount(0)
    assertHermetic(api, errors)
  })
})

test('a QA lead gets the refusal page and makes no jobs request', async ({ page }) => {
  const world = retentionExportWorld({ role: 'QA_LEAD' })
  const { api, errors } = await openRollout(page, '/settings/retention', {
    handlers: world.handlers,
    ready: (p) => p.getByText('Admin access required'),
    user: world.user,
  })
  await networkQuiet(page, api)
  await expect(jobsCard(page)).toHaveCount(0)
  expect(api.seen.filter((line) => line.includes(`/projects/${P}/deletion/jobs`))).toEqual([])
  assertHermetic(api, errors)
})
