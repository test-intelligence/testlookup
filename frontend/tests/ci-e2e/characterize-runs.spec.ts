/**
 * W27a PAN-03 CHARACTERIZATION, `/runs` (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.3, rows
 * U1-U3), hermetic, with `manual_upload` ON. Written against the MODAL tree (the upload in a modal dialog, the
 * failure signatures in an edge side panel) and kept green, UNEDITED, after the in-page conversion: each
 * container is found by the name it keeps (`opened`).
 *
 *  - U1 Upload report → a JUnit file, format, build `b-42`, branch `main`, AI on → Upload: ONE
 *    `POST /api/v1/ingest/file`, a real multipart body (never a JSON `Content-Type`) whose parts are exactly
 *    `file`, `project_id`, `build_number`, `format`, `run_ai`, `branch`, in that order (no `commit_hash` /
 *    `release_name` part when blank); then `GET /api/v1/ingest/uploads/{task}` until it succeeds (twice:
 *    parsing, succeeded), the counts, `Report processed`, and View run lands on `/runs/{run_id}`;
 *    with nothing typed: the default `upload-<time>-<rand>` build label, `run_ai=false` when AI is skipped,
 *    and Close stays on `/runs`;
 *  - U2 `/runs?upload=1`: a QA engineer gets the form (nothing sent until Upload) and Escape, the X and Cancel
 *    close it and drop `?upload=1`; a viewer gets the role line `QA Engineer role required to upload
 *    reports.`, no form, a disabled header button, and sends nothing;
 *  - U3 Failure signatures from the header and from a row's chip: shown, closed by `Close panel`, and no new
 *    request (the signatures are read with the page).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `runsUploadWorld()`
 * (`fixtures-in-page.ts`, over `RUNS_PAGE` and `RUN_PAGE`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, RUN_ID } from '../visual/production/fixtures'
import { runsUploadWorld, UPLOAD_TASK_ID } from '../visual/production/fixtures-in-page'
import { QA_ENGINEER, VIEWER } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.locator('[data-status-banner]')
const UPLOAD_ON = { manual_upload: true }
const JUNIT = '<?xml version="1.0"?>\n<testsuite name="checkout" tests="3"><testcase name="a"/><testcase name="b"/><testcase name="c"><failure/></testcase></testsuite>\n'
const FILE = { name: 'junit-results.xml', mimeType: 'application/xml', buffer: Buffer.from(JUNIT) }
const uploadButton = (page: Page) => page.locator('[data-page-header]').getByRole('button', { name: 'Upload report' })

/** The whole URL is `<origin><path>` (never a substring match). */
async function expectPath(page: Page, path: string) {
  const origin = new URL(page.url()).origin
  await expect(page).toHaveURL(`${origin}${path}`)
}

const polls = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, `/api/v1/ingest/uploads/${UPLOAD_TASK_ID}`).length
/** The kinds of request made so far (a poll repeating an existing line is not a new kind). */
const kinds = (api: Parameters<typeof observed>[0]) => [...new Set(observed(api))].sort()

test('U1 Upload report: one multipart POST with exactly its parts, the status read until done, then View run opens the run', async ({ page }) => {
  const world = runsUploadWorld()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready, flags: UPLOAD_ON })
  await uploadButton(page).click()
  const form = opened(page, 'Upload test report')
  await expect(form).toBeVisible()
  const upload = form.getByRole('button', { name: 'Upload', exact: true })
  await expect(upload, 'nothing to upload until a file is chosen').toBeDisabled()

  await form.locator('input[type="file"]').setInputFiles(FILE)
  await expect(form.getByText('junit-results.xml')).toBeVisible()
  await form.getByLabel('Format').selectOption('junit')
  await form.getByRole('button', { name: 'Optional metadata' }).click()
  await form.getByLabel('Build label').fill('b-42')
  await form.getByPlaceholder('e.g. main').fill('main')
  await upload.click()

  await expect(form.getByText('Processed 3 tests')).toBeVisible({ timeout: 10_000 })
  await expect(form.getByText('2 passed · 1 failed')).toBeVisible()
  await expect(page.getByText('Report processed')).toBeVisible()
  expect(world.uploads).toHaveLength(1)
  const [sent] = world.uploads
  expect(sent.line).toBe('POST /api/v1/ingest/file')
  expect(sent.contentType, 'a multipart body with its boundary, never JSON').toMatch(/^multipart\/form-data; boundary=\S+$/)
  expect(sent.parts).toEqual([
    { name: 'file', filename: 'junit-results.xml', contentType: 'application/xml', value: JUNIT },
    { name: 'project_id', value: PROJECT_ID },
    { name: 'build_number', value: 'b-42' },
    { name: 'format', value: 'junit' },
    { name: 'run_ai', value: 'true' },
    { name: 'branch', value: 'main' },
  ])
  expect(polls(api), 'the status is read until it succeeds: parsing, then succeeded').toBe(2)

  await form.getByRole('button', { name: 'View run' }).click()
  await expectPath(page, `/runs/${RUN_ID}`)
  await expect(page.locator('[data-primary] table')).toBeVisible()
  await expect(opened(page, 'Upload test report')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.uploads, 'one upload, nothing else sent').toHaveLength(1)
  expect(polls(api), 'no status read after it succeeded').toBe(2)
  assertHermetic(api, errors)
})

test('U1 with nothing typed: the default build label, run_ai=false when AI is skipped, no optional part; Close stays on /runs', async ({ page }) => {
  const world = runsUploadWorld()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready, flags: UPLOAD_ON })
  await uploadButton(page).click()
  const form = opened(page, 'Upload test report')
  await form.locator('input[type="file"]').setInputFiles(FILE)
  await form.getByRole('checkbox', { name: /Skip AI analysis/ }).check()
  await form.getByRole('button', { name: 'Upload', exact: true }).click()
  await expect(form.getByText('Processed 3 tests')).toBeVisible({ timeout: 10_000 })

  const [sent] = world.uploads
  expect(sent.parts.map((part) => part.name)).toEqual(['file', 'project_id', 'build_number', 'format', 'run_ai'])
  // `defaultBuildLabel()`: the (pinned) time to the millisecond and a random suffix.
  expect(sent.parts[2].value).toMatch(/^upload-2026-09-18T12-00-00-000-[a-z0-9]{1,4}$/)
  expect(sent.parts[3]).toEqual({ name: 'format', value: 'auto' })
  expect(sent.parts[4]).toEqual({ name: 'run_ai', value: 'false' })

  // On success two controls read "Close": the X in the header, then the footer's Close (the last one).
  await form.getByRole('button', { name: 'Close', exact: true }).last().click()
  await expect(opened(page, 'Upload test report')).toHaveCount(0)
  await expectPath(page, '/runs')
  await networkQuiet(page, api)
  expect(world.uploads).toHaveLength(1)
  assertHermetic(api, errors)
})

test('U2 /runs?upload=1 as a QA engineer: the form opens, sends nothing, and Escape, the X and Cancel close it and drop ?upload=1', async ({ page }) => {
  const world = runsUploadWorld()
  const { api, errors } = await openRollout(page, '/runs?upload=1', { handlers: world.handlers, ready, flags: UPLOAD_ON, user: QA_ENGINEER })
  const form = opened(page, 'Upload test report')
  await expect(form).toBeVisible()
  await expect(page.getByText('QA Engineer role required to upload reports.')).toHaveCount(0)

  // Escape (focus inside the form) closes it and drops the deep link's key.
  await form.getByLabel('Format').focus()
  await page.keyboard.press('Escape')
  await expect(form).toHaveCount(0)
  await expectPath(page, '/runs')

  // The header button opens it again; the X closes it.
  await uploadButton(page).click()
  await expect(form).toBeVisible()
  await form.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(form).toHaveCount(0)

  // Cancel, with a file chosen, sends nothing.
  await uploadButton(page).click()
  await form.locator('input[type="file"]').setInputFiles(FILE)
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await expectPath(page, '/runs')
  await networkQuiet(page, api)
  expect(world.uploads).toEqual([])
  expect(polls(api)).toBe(0)
  assertHermetic(api, errors)
})

test('U2 /runs?upload=1 as a viewer: the role line, no form, a disabled header button, nothing sent', async ({ page }) => {
  const world = runsUploadWorld()
  const { api, errors } = await openRollout(page, '/runs?upload=1', { handlers: world.handlers, ready, flags: UPLOAD_ON, user: VIEWER })
  await expect(page.getByText('QA Engineer role required to upload reports.')).toBeVisible()
  await expect(opened(page, 'Upload test report')).toHaveCount(0)
  await expect(uploadButton(page)).toBeDisabled()
  await expect(uploadButton(page)).toHaveAttribute('title', 'QA Engineer role required')
  await networkQuiet(page, api)
  await expect(opened(page, 'Upload test report')).toHaveCount(0)
  expect(world.uploads).toEqual([])
  expect(requestsTo(api, /^\/api\/v1\/ingest\//)).toEqual([])
  assertHermetic(api, errors)
})

test('U3 Failure signatures from the header and from a row chip: shown, closed by "Close panel", and no new request', async ({ page }) => {
  const world = runsUploadWorld()
  const { api, errors } = await openRollout(page, '/runs', { handlers: world.handlers, ready, flags: UPLOAD_ON })
  await networkQuiet(page, api)
  const loaded = kinds(api)
  const signatures = opened(page, 'Failure signatures')
  await expect(signatures).toHaveCount(0)

  await page.locator('[data-page-header]').getByRole('button', { name: /^Failure signatures/ }).click()
  await expect(signatures).toBeVisible()
  await expect(signatures.getByRole('meter', { name: 'Failed builds by signature' })).toBeVisible()
  await expect(signatures.locator('[data-signature-clusters]')).toBeVisible()
  await signatures.getByRole('button', { name: 'Close panel' }).click()
  await expect(signatures).toHaveCount(0)

  await page.locator('[data-signature-chip]').first().click()
  await expect(signatures).toBeVisible()
  await expect(signatures.getByRole('meter', { name: 'Failed builds by signature' })).toBeVisible()
  await networkQuiet(page, api)
  expect(kinds(api), 'opening the signatures asks for nothing new').toEqual(loaded)
  assertHermetic(api, errors)
})
