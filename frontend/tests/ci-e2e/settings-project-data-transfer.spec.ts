/**
 * W24 ADM-05 phase 1 (`docs/deferred-scope-2026-10-13/work/W24-contract.md` §4, §6.3): Export and Import on
 * /settings/project-data, hermetic on `projectDataWorld({...})` (`production/fixtures-in-page-b.ts`), at 1280 × 900
 * and 1440 × 900.
 *
 *  - ADMIN: Export → ONE `POST …/export` → "Exporting… 12 of 30 tables" → Download (2.1 MB) + "Available until …";
 *    Download → ONE `GET …/download` and a saved file; a scripted 410 reads its sentence OUT OF THE BLOB (never
 *    "Request failed …"); a 409 `export_running` resumes that job.
 *  - ADMIN: Import — a 3 KB ZIP → ONE multipart POST (`file`, `client_token` UUID, no `name`) → "Importing… 5 of 30
 *    tables" → "Created Payment Service (imported)." + Open + the lists (the skipped line in the SINGULAR); the SAME
 *    file again → the SAME token; a 60 MB file → the 50 MB sentence and NO request; Open → /test-management with the
 *    new project selected.
 *  - QA lead: the Export card (and it works), no Import card, no Danger-zone control, no `GET /api/v1/project-imports`.
 * The job polls every 2 s while queued / running, so a progression of three answers needs a few seconds.
 *
 * W30 ADM-05 phase 2 (`work/W30-contract.md` §4, §6.3 "Specs and visual"):
 *  - ADMIN: tick "Include run history" → ONE `GET …/export-estimate` (90 days) → the estimate line; 365 days → ONE
 *    more; Export → ONE `POST …/export` with `{"include_history": true, "history_days": 365, "max_runs": 2000}` →
 *    every phase ("Runs and results… 120 of 412", "Stored documents… 5,000 of 9,120", …) → Download (412.3 MB) and
 *    the counts line; a finished history export over 512 MB adds the CLI hint. Unticked stays W24's body-less POST.
 *  - ADMIN: a 60 MB ZIP GENERATED IN THE PAGE (a Blob, never a file on disk) → ONE start, 2 PUTs with
 *    `X-Part-Sha256` (each header == the body's real SHA-256; the start's `sha256` == the file's) and ONE complete →
 *    "Uploading… 1 of 2 parts" → the phases → the history report (the retention pause, the recent-runs note,
 *    "Search is being rebuilt for the new project."); a proxy's HTML 413 on part 2 → `A part is larger than 32 MB.`
 *    and no complete.
 *  - QA lead: the history checkbox and the estimate (export is QA-lead allowed), no Import card.
 */
import { createHash } from 'node:crypto'
import { statSync, truncateSync, writeFileSync } from 'node:fs'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { assertHermetic, respondRaw, type ApiHandlers } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, PROJECTS, USER } from '../visual/production/fixtures'
import {
  HISTORY_EXPORT_BYTES,
  historyDataJob,
  IMPORTED_PROJECT_ID,
  IMPORTED_PROJECT_NAME,
  PROXY_413_HTML,
  projectDataJob,
  projectDataRefusal,
  projectDataWorld,
} from '../visual/production/fixtures-in-page-b'
import { ADMIN } from '../visual/production/fixtures-releases'

const P = PROJECT_ID
const MB = 1024 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const PROGRESS = { timeout: 15_000 }
const RUNNING_ID = 'dada0000-0000-4000-8000-0000000000e9'

const exportCard = (p: Page) => p.locator('[data-project-export]')
const importCard = (p: Page) => p.locator('[data-project-import]')
const exportButton = (p: Page) => exportCard(p).getByRole('button', { name: 'Export', exact: true })
const downloadButton = (p: Page) => exportCard(p).getByRole('button', { name: 'Download (2.1 MB)' })
const importButton = (p: Page) => importCard(p).getByRole('button', { name: 'Import', exact: true })
const noAxiosWording = (p: Page) => expect(p.getByText(/Request failed with status code/)).toHaveCount(0)
// W30
const historyBox = (p: Page) => exportCard(p).getByRole('checkbox', { name: 'Include run history' })
const windowSelect = (p: Page) => exportCard(p).getByRole('combobox')
const ESTIMATE = `/api/v1/projects/${P}/export-estimate`
const ESTIMATE_LINE = 'About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments'
const SHA256 = /^[0-9a-f]{64}$/
const GENERATED = { name: 'history-export.zip', bytes: 60 * MB, lastModified: 1_790_000_000_000 }

/** The generated ZIP's bytes (the ZIP end record's magic, then zeros) — built the same way in Node and in the page. */
function generatedBytes(bytes: number): Buffer {
  const data = Buffer.alloc(bytes)
  data.set([0x50, 0x4b, 0x05, 0x06], 0)
  return data
}

/** Choose a ZIP generated IN THE PAGE (a Blob → File → the input's FileList), never a file on disk. */
async function chooseGeneratedZip(p: Page) {
  await p.locator('#project-import-file').evaluate((input: HTMLInputElement, spec) => {
    const data = new Uint8Array(spec.bytes)
    data.set([0x50, 0x4b, 0x05, 0x06], 0)
    const transfer = new DataTransfer()
    transfer.items.add(new File([data], spec.name, { type: 'application/zip', lastModified: spec.lastModified }))
    input.files = transfer.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }, GENERATED)
}

/** A file on disk (a stable name, size and modification time, so choosing it twice is the SAME file). */
function fileOnDisk(testInfo: TestInfo, name: string, bytes: number): string {
  const path = testInfo.outputPath(name)
  writeFileSync(path, 'PK\u0005\u0006')
  truncateSync(path, bytes)
  return path
}

/** The imported project in the project list, so Open can select it (the shell's list is `LAYOUT`'s otherwise). */
const WITH_IMPORTED: ApiHandlers = [
  ['/api/v1/projects', () => [
    ...PROJECTS,
    { id: IMPORTED_PROJECT_ID, name: IMPORTED_PROJECT_NAME, slug: 'checkout-imported', description: '', is_active: true, created_at: '2026-09-18T11:00:00Z' },
  ]],
]

for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 900 }]) {
  test.describe(`${viewport.width} × ${viewport.height}`, () => {
    test.use({ viewport })

    test('ADMIN: Export → one POST → "Exporting… 12 of 30 tables" → Download (2.1 MB) → one GET and a saved file', async ({ page }) => {
      const world = projectDataWorld()
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: ADMIN })
      await expect(exportCard(page)).toContainText('No export of this project yet.')
      await exportButton(page).click()
      await expect(exportCard(page).getByText('Exporting… 12 of 30 tables')).toBeVisible(PROGRESS)
      await expect(exportButton(page)).toBeDisabled()
      await expect(downloadButton(page)).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByText(/^Available until .+\.$/)).toBeVisible()
      await expect(exportCard(page).getByText('Signed by this deployment', { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/export`, body: null }])

      const saved = page.waitForEvent('download')
      await downloadButton(page).click()
      const download = await saved
      // The world sends no Content-Disposition (the file saves as project-export-<job id>.zip): assert the bytes.
      const savedPath = await download.path()
      expect(statSync(savedPath).size, 'the saved file is not empty').toBeGreaterThan(0)
      await networkQuiet(page, api)
      expect(requestsTo(api, /^\/api\/v1\/project-data-jobs\/[^/]+\/download$/)).toHaveLength(1)
      expect(world.writes, 'a download writes nothing').toHaveLength(1)
      await noAxiosWording(page)
      assertHermetic(api, errors)
    })

    test('ADMIN: a refused download (410) reads its sentence out of the Blob, never "Request failed …"', async ({ page }) => {
      const world = projectDataWorld({ exports: [projectDataJob('export', 'succeeded')], download: projectDataRefusal(410, 'export_expired') })
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: downloadButton, user: ADMIN })
      await downloadButton(page).click()
      await expect(exportCard(page).getByRole('alert')).toHaveText('This export was removed after 72 hours; start a new one.')
      await networkQuiet(page, api)
      await noAxiosWording(page)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test('ADMIN: a 409 export_running on Export resumes the running job', async ({ page }) => {
      const world = projectDataWorld({
        exportPost: projectDataRefusal(409, 'export_running', { job_id: RUNNING_ID }),
        jobAnswers: { [RUNNING_ID]: [projectDataJob('export', 'running', { id: RUNNING_ID }), projectDataJob('export', 'succeeded', { id: RUNNING_ID })] },
      })
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: ADMIN })
      await exportButton(page).click()
      await expect(exportCard(page).getByText('Exporting… 12 of 30 tables')).toBeVisible(PROGRESS)
      await expect(downloadButton(page)).toBeVisible(PROGRESS)
      await networkQuiet(page, api)
      expect(requestsTo(api, `/api/v1/project-data-jobs/${RUNNING_ID}`).length, 'the running job is polled').toBeGreaterThan(0)
      await expect(exportCard(page).getByRole('alert')).toHaveCount(0)
      await noAxiosWording(page)
      expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/export`, body: null }])
      assertHermetic(api, errors)
    })

    test('ADMIN: Import a 3 KB ZIP → one multipart POST → the report; the same file again → the same token; a .txt → no request; Open', async ({ page }, testInfo) => {
      const world = projectDataWorld()
      const zip = fileOnDisk(testInfo, 'payment-service.zip', 3 * 1024)
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: [...WITH_IMPORTED, ...world.handlers], ready: importCard, user: ADMIN })
      await expect(importButton(page)).toBeDisabled()
      await page.getByLabel('Export file (.zip)').setInputFiles(zip)
      await expect(importCard(page).getByText('payment-service.zip · 3 KB')).toBeVisible()
      await importButton(page).click()
      await expect(importCard(page).getByText('Importing… 5 of 30 tables')).toBeVisible(PROGRESS)
      await expect(importCard(page).getByText(`Created ${IMPORTED_PROJECT_NAME}.`, { exact: true })).toBeVisible(PROGRESS)
      const open = importCard(page).getByRole('button', { name: `Open ${IMPORTED_PROJECT_NAME}` })
      await expect(open).toBeVisible()
      await expect(importCard(page).getByText('1 row was skipped: it pointed at a row that is not in the export', { exact: true })).toBeVisible()
      await expect(importCard(page).getByText('3 rows switched off — secret required', { exact: true })).toBeVisible()
      await expect(importCard(page).locator('[data-report-list="inserted"] li')).toHaveCount(5)
      await networkQuiet(page, api)
      expect(world.writes).toHaveLength(1)
      const first = world.writes[0] as { line: string; body: Record<string, unknown> }
      expect(first.line).toBe('POST /api/v1/projects/import')
      expect(first.body.fields).toEqual(['file', 'client_token'])
      // The type comes from the OS for a file on disk: application/zip on Linux, application/x-zip-compressed on Windows.
      expect(first.body.file).toEqual({ filename: 'payment-service.zip', content_type: expect.stringMatching(/^application\/(x-zip-compressed|zip)$/) })
      expect(first.body.client_token).toMatch(UUID)
      expect(first.body, 'no name was typed').not.toHaveProperty('name')

      // The SAME file chosen again: the same token (the server answers with the one job it has).
      await page.getByLabel('Export file (.zip)').setInputFiles(zip)
      await importButton(page).click()
      await expect.poll(() => world.writes.length).toBe(2)
      const tokens = new Set(world.writes.map((w) => (w.body as Record<string, unknown>).client_token))
      expect(tokens.size, 'one recorded token').toBe(1)
      await expect(open).toBeVisible(PROGRESS)
      await networkQuiet(page, api)

      // Not a ZIP: the server's sentence, and nothing is sent. (W30: a 60 MB file is no longer refused — it travels in
      // parts, the test below; the 2 GB refusal is pinned by ProjectImportCard.test.tsx, a 2 GB file is not built here.)
      await page.getByLabel('Export file (.zip)').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('notes') })
      await expect(importCard(page).getByText('Choose a .zip file exported from TestLookup.', { exact: true })).toBeVisible()
      await expect(importButton(page)).toBeDisabled()
      await networkQuiet(page, api)
      expect(world.writes).toHaveLength(2)
      await noAxiosWording(page)
      assertHermetic(api, errors)

      // Open: the new project is selected and Test Management opens (its own reads are not this world's).
      await open.click()
      await expect(page).toHaveURL(/\/test-management$/)
      await expect
        .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('testlookup-active-project') ?? '{}')?.state?.activeProjectId))
        .toBe(IMPORTED_PROJECT_ID)
    })

    test('ADMIN (W30): run history → ONE estimate GET per window → Export with the body → every phase → Download (412.3 MB)', async ({ page }) => {
      const world = projectDataWorld()
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: ADMIN })
      await networkQuiet(page, api)
      expect(requestsTo(api, ESTIMATE), 'nothing is estimated before the tick').toEqual([])
      await historyBox(page).check()
      await expect(windowSelect(page)).toHaveValue('90')
      await expect(exportCard(page).getByText(ESTIMATE_LINE, { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      expect(requestsTo(api, ESTIMATE)).toEqual([`GET ${ESTIMATE}?include_history=true&history_days=90`])
      await windowSelect(page).selectOption('365')
      await networkQuiet(page, api)
      expect(requestsTo(api, ESTIMATE), 'ONE more read for the new window').toHaveLength(2)
      expect(requestsTo(api, ESTIMATE)[1]).toContain('history_days=365')
      expect(world.estimates).toEqual([90, 365])

      await exportButton(page).click()
      await expect(exportCard(page).getByText('Exporting… 12 of 56 tables')).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByText('Runs and results… 120 of 412')).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByText('Stored documents… 5,000 of 9,120')).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByText('Attachments… 100 of 1,830')).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByRole('button', { name: 'Download (412.3 MB)' })).toBeVisible(PROGRESS)
      await expect(exportCard(page).getByText('412 runs · 9,120 documents · 1,830 attachments', { exact: true })).toBeVisible()
      await expect(exportCard(page).getByText(/download more reliably with the CLI/), 'under 512 MB: no CLI hint').toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([
        { line: `POST /api/v1/projects/${P}/export`, body: { include_history: true, history_days: 365, max_runs: 2000 } },
      ])
      await noAxiosWording(page)
      assertHermetic(api, errors)
    })

    test('ADMIN (W30): a finished history export over 512 MB offers the CLI for the download', async ({ page }) => {
      const big = historyDataJob('export', 'succeeded', { bytes: HISTORY_EXPORT_BYTES + 200 * MB })
      const world = projectDataWorld({ exports: [big] })
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: ADMIN })
      await expect(exportCard(page).getByRole('button', { name: 'Download (612.3 MB)' })).toBeVisible()
      await expect(exportCard(page).getByText(`Large archives download more reliably with the CLI: testlookup projects export ${P} --history`, { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test('ADMIN (W30): a 60 MB ZIP generated in the page → start, 2 PUTs with X-Part-Sha256, complete → the phases → the history report', async ({ page }) => {
      const world = projectDataWorld({ parts: { holdMs: 1_500 } })
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: importCard, user: ADMIN })
      await chooseGeneratedZip(page)
      await expect(importCard(page).getByText('history-export.zip · 60.0 MB', { exact: true })).toBeVisible()
      await importButton(page).click()
      await expect(importCard(page).getByText('Uploading… 1 of 2 parts', { exact: true })).toBeVisible(PROGRESS)
      // The world's import counts ITS archive: 2 runs, 8 documents, 1 attachment.
      await expect(importCard(page).getByText('Stored documents… 6 of 8', { exact: true })).toBeVisible(PROGRESS)
      await expect(importCard(page).getByText('Search index…')).toBeVisible(PROGRESS)
      await expect(importCard(page).getByText(`Created ${IMPORTED_PROJECT_NAME}.`, { exact: true })).toBeVisible(PROGRESS)
      const history = importCard(page).locator('[data-import-history]')
      await expect(history.getByText('2 runs from Sep 14, 2026 to Sep 16, 2026', { exact: true })).toBeVisible()
      await expect(history.getByText('6 documents', { exact: true })).toBeVisible()
      await expect(history.getByText('1 attachment · 2 KB', { exact: true })).toBeVisible()
      await expect(history.getByText('1 attachment was skipped: it is larger than 256 MB', { exact: true })).toBeVisible()
      await expect(history.getByText(/^The retention purge of the new project is paused until .+\.$/)).toBeVisible()
      // retention_days 365 with 0 runs past it: no retention note.
      await expect(history.getByText(/retention window|Retention is off/)).toHaveCount(0)
      await expect(history.getByText('2 imported runs are from the last 7 days; an all-projects digest can list them.', { exact: true })).toBeVisible()
      await expect(history.getByText('Search is being rebuilt for the new project.', { exact: true })).toBeVisible()
      await networkQuiet(page, api)

      // The wire: ONE start, two PUTs (one part in flight), ONE complete — and no single-shot POST.
      const whole = createHash('sha256').update(generatedBytes(GENERATED.bytes)).digest('hex')
      expect(world.writes.map((w) => w.line)).toEqual([
        'POST /api/v1/project-imports/uploads',
        expect.stringMatching(/^POST \/api\/v1\/project-imports\/uploads\/[^/]+\/complete$/),
      ])
      const start = world.writes[0].body as Record<string, unknown>
      // No name typed: `name: null` (the server names the copy "<source> (imported)").
      expect(start).toEqual({ client_token: expect.stringMatching(UUID), name: null, bytes: GENERATED.bytes, parts: 2, sha256: whole })
      expect(world.parts.map((p) => [p.part, p.bytes])).toEqual([[1, 32 * MB], [2, 28 * MB]])
      for (const part of world.parts) {
        expect(part.sha256, `part ${part.part}'s X-Part-Sha256`).toMatch(SHA256)
        expect(part.sha256, `part ${part.part}'s header is its body's SHA-256`).toBe(part.bodySha256)
      }
      expect(requestsTo(api, '/api/v1/projects/import'), 'a parted file never takes the single POST').toEqual([])
      await noAxiosWording(page)
      assertHermetic(api, errors)
    })

    test("ADMIN (W30): a proxy's HTML 413 on part 2 reads \"A part is larger than 32 MB.\" and the upload is not completed", async ({ page }) => {
      const world = projectDataWorld({ parts: { refuse: { 2: respondRaw(PROXY_413_HTML, 'text/html', 413) } } })
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: importCard, user: ADMIN })
      await chooseGeneratedZip(page)
      await importButton(page).click()
      await expect(importCard(page).getByRole('alert')).toHaveText('A part is larger than 32 MB.', PROGRESS)
      await networkQuiet(page, api)
      expect(world.parts.map((p) => p.part)).toEqual([1, 2])
      expect(world.writes.map((w) => w.line), 'no complete after a refused part').toEqual(['POST /api/v1/project-imports/uploads'])
      await noAxiosWording(page)
      assertHermetic(api, errors)
    })

    test('QA lead: the Export card (it works) with the history box and the estimate, no Import card, no Danger-zone control, no imports read', async ({ page }) => {
      const world = projectDataWorld()
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: USER })
      await expect(importCard(page)).toHaveCount(0)
      await expect(page.getByRole('heading', { name: 'Danger zone' })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Delete test runs' })).toHaveCount(0)
      // W30: a QA lead may export with history — the box and the estimate are hers too.
      await historyBox(page).check()
      await expect(exportCard(page).getByText(ESTIMATE_LINE, { exact: true })).toBeVisible()
      await networkQuiet(page, api)
      expect(requestsTo(api, ESTIMATE)).toHaveLength(1)
      // Unticked again: Export is W24's body-less POST (pinned below).
      await historyBox(page).uncheck()
      await exportButton(page).click()
      await expect(downloadButton(page)).toBeVisible(PROGRESS)
      await networkQuiet(page, api)
      expect(requestsTo(api, '/api/v1/project-imports'), 'a QA lead never reads the imports').toEqual([])
      expect(world.writes).toEqual([{ line: `POST /api/v1/projects/${P}/export`, body: null }])
      await noAxiosWording(page)
      assertHermetic(api, errors)
    })
  })
}
