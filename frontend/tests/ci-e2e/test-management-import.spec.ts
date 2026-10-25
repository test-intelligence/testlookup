/**
 * W13 TCL-04 "import test cases from CSV or Excel, with a preview in the page" (`docs/deferred-scope-2026-10-13/work/
 * W13-contract.md` §2.5, §4.2), hermetic, 1440 × 900. The CSV is BUILT IN THE SPEC (`setInputFiles({ name, mimeType,
 * buffer })`, the `characterize-runs.spec.ts` precedent):
 *
 *  - as a TESTER: **Import** opens `Import test cases` IN the page's top slot (no overlay); choosing the file sends ONE
 *    multipart `POST /cases/import?project_id=<P>&dry_run=true` (the part `file`, the file's name); the preview
 *    `20 rows: 17 to create · 0 duplicates · 3 invalid`, the header report, the reasons; a formula title is shown as
 *    those characters (never a link); **Import 17 cases** sends the same file with `dry_run=false` → the result line,
 *    the toast, the cases roll AND the health re-read, a created row opens its case;
 *  - a 413 renders the server's sentence in the section (no toast);
 *  - a VIEWER has no Import.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `importWorld()` (`fixtures-test-library.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  CASES_API,
  FORMULA_TITLE,
  HEALTH_API,
  IMPORT_API,
  IMPORT_FILE_NAME,
  importCsv,
  importedCaseId,
  importTitle,
  importWorld,
  LIBRARY_TEXT,
  LIBRARY_USERS,
  type ImportWorldOptions,
  type LibraryRole,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const importButton = (p: Page) => p.getByRole('button', { name: 'Import', exact: true })
const section = (p: Page) => opened(p, 'Import test cases')
const FILE = () => ({ name: IMPORT_FILE_NAME, mimeType: 'text/csv', buffer: Buffer.from(importCsv(), 'utf8') })

async function open(page: Page, role: LibraryRole = 'TESTER', options: ImportWorldOptions = {}) {
  const world = importWorld({ role, ...options })
  const opened_ = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady, user: LIBRARY_USERS[role] })
  await expect(page.locator('[data-primary] tbody tr[data-case-row]').first()).toBeVisible()
  return { world, ...opened_ }
}

const importPosts = (world: ReturnType<typeof importWorld>) => world.writes.filter((w) => w.line.startsWith(`POST ${IMPORT_API}`))

test('TCL-04 as a tester: the dry run on file choice, the preview, Import 17 cases → the result, the toast, the re-reads', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page)
  await networkQuiet(page, api)
  await importButton(page).click()
  const form = section(page)
  await expect(form).toBeVisible()
  await expect(form.locator('#tm-import-title')).toHaveText('Import test cases')
  await expect(form).toContainText(LIBRARY_TEXT.importLimits)
  await expect(form.locator('input[type="file"]')).toHaveAttribute('accept', '.csv,.xlsx')
  await expectNoOverlay(page)
  expect(importPosts(world), 'opening the section sends nothing').toEqual([])

  await form.locator('input[type="file"]').setInputFiles(FILE())
  await expect(form.locator('[data-import-summary]')).toHaveText('20 rows: 17 to create · 0 duplicates · 3 invalid')
  await expect(form).toContainText('Ignored columns: Status, Version')
  await expect(form).toContainText('Unknown columns: Owner')
  await expect(form.locator('[data-import-row]')).toHaveCount(20)
  await expect(form.locator('[data-import-row="19"]')).toContainText(LIBRARY_TEXT.importRowTitle)
  await expect(form.locator('[data-import-row="3"]')).toContainText(LIBRARY_TEXT.importWarnStatus)
  const formulaCell = form.locator('[data-import-row="2"] td').nth(1)
  await expect(formulaCell).toHaveText(FORMULA_TITLE)
  await expect(formulaCell.locator('a')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(importPosts(world)).toEqual([{
    line: `POST ${IMPORT_API}?project_id=${PROJECT_ID}&dry_run=true`,
    body: { contentType: 'multipart/form-data', part: 'file', fileName: IMPORT_FILE_NAME, bytes: expect.any(Number) },
  }])

  await form.locator('[data-import-chip="invalid"]').click()
  await expect(form.locator('[data-import-row]')).toHaveCount(3)
  await form.locator('[data-import-chip="all"]').click()

  const rollBefore = requestsTo(api, CASES_API).length
  const healthBefore = requestsTo(api, HEALTH_API).length
  await form.getByRole('button', { name: 'Import 17 cases', exact: true }).click()
  await expect(form.locator('[data-import-summary]')).toHaveText('17 cases imported as Draft. 3 rows were not imported.')
  await expect(page.getByText('17 test cases imported', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const posts = importPosts(world)
  expect(posts).toHaveLength(2)
  expect(posts[1].line).toBe(`POST ${IMPORT_API}?project_id=${PROJECT_ID}&dry_run=false`)
  expect(posts[1].body).toEqual(posts[0].body)
  expect(requestsTo(api, CASES_API).length, 'the cases roll is re-read').toBeGreaterThan(rollBefore)
  expect(requestsTo(api, HEALTH_API).length, 'the library health is re-read').toBeGreaterThan(healthBefore)

  await form.getByRole('button', { name: importTitle(4), exact: true }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('case')).toBe(importedCaseId(4))
  assertHermetic(api, errors)
})

test('TCL-04 a 413 renders the server sentence in the section', async ({ page }) => {
  const { world, api, errors } = await open(page, 'TESTER', { refuse: { status: 413, detail: LIBRARY_TEXT.importTooLarge } })
  await importButton(page).click()
  const form = section(page)
  await form.locator('input[type="file"]').setInputFiles(FILE())
  await expect(form.getByRole('alert')).toHaveText(LIBRARY_TEXT.importTooLarge)
  await expect(form.getByRole('button', { name: /^Import \d/ })).toHaveCount(0)
  expect(importPosts(world)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('TCL-04 a viewer has no Import', async ({ page }) => {
  const { api, errors } = await open(page, 'VIEWER')
  await networkQuiet(page, api)
  await expect(importButton(page)).toHaveCount(0)
  assertHermetic(api, errors)
})
