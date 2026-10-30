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
 */
import { statSync, truncateSync, writeFileSync } from 'node:fs'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { assertHermetic, type ApiHandlers } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, PROJECTS, USER } from '../visual/production/fixtures'
import {
  IMPORTED_PROJECT_ID,
  IMPORTED_PROJECT_NAME,
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

    test('ADMIN: Import a 3 KB ZIP → one multipart POST → the report; the same file again → the same token; 60 MB → no request; Open', async ({ page }, testInfo) => {
      const world = projectDataWorld()
      const zip = fileOnDisk(testInfo, 'payment-service.zip', 3 * 1024)
      const big = fileOnDisk(testInfo, 'too-big.zip', 60 * MB)
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
      await expect(importCard(page).locator('[data-report-list="inserted"] li')).toHaveCount(3)
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

      // Over 50 MB: the server's sentence, and nothing is sent.
      await page.getByLabel('Export file (.zip)').setInputFiles(big)
      await expect(importCard(page).getByText('The file is larger than 50 MB.', { exact: true })).toBeVisible()
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

    test('QA lead: the Export card (it works), no Import card, no Danger-zone control, no imports read', async ({ page }) => {
      const world = projectDataWorld()
      const { api, errors } = await openRollout(page, '/settings/project-data', { handlers: world.handlers, ready: exportCard, user: USER })
      await expect(importCard(page)).toHaveCount(0)
      await expect(page.getByRole('heading', { name: 'Danger zone' })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Delete test runs' })).toHaveCount(0)
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
