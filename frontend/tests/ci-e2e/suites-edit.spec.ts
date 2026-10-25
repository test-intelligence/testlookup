/**
 * W13 TCL-07 "a suite exists before its first ingest; edit a suite; the latest run as a date"
 * (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §2.1, §4.2), hermetic, 1440 × 900:
 *
 *  - `/suites` as a QA ENGINEER: each row's **Edit** (`Edit <name>`) opens `Edit suite` IN the page under its row
 *    (no overlay), prefilled; a changed name shows the rename note; Save sends ONE `PATCH /suites/<id>` of ONLY the
 *    changed fields, the toast `Suite “…” saved.`, the list shows the new name; a name another suite uses renders the
 *    server's 409 in the form; a TESTER has no Edit;
 *  - `/suites/<id>`: **Edit suite** in the header (QA engineer+) opens the form under the header; the Tests tab's
 *    latest-run cell keeps `run <id>` and adds the run's DATE as a second line;
 *  - `/test-management?tab=suites` (a QA lead): a first-class suite no run reports (`Checkout API`) is listed at once
 *    as an `Empty` card with the waiting sentence, no counts, its name a link to `/suites/<id>`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `suitesEditWorld()` (`fixtures-test-library.ts`,
 * over `suitesInPageWorld()` and `TEST_CASES`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { SUITE, SUITE_ID } from '../visual/production/fixtures'
import {
  EMPTY_SUITE_ID,
  EMPTY_SUITE_NAME,
  LIBRARY_TEXT,
  suitesEditWorld,
  type LibraryRole,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const suiteRow = (p: Page, name: string) =>
  p.locator('[data-primary] tbody > tr:not([data-in-page-row])').filter({ has: p.locator('[data-suite-name]', { hasText: name }) })
const editForm = (p: Page) => opened(p, 'Edit suite')
const patches = (world: ReturnType<typeof suitesEditWorld>) => world.writes.filter((w) => w.line.startsWith('PATCH '))

async function open(page: Page, path: string, role: LibraryRole, ready: (p: Page) => ReturnType<Page['locator']>) {
  const world = suitesEditWorld({ role })
  const opened_ = await openRollout(page, path, { handlers: world.handlers, ready, user: world.user })
  return { world, ...opened_ }
}

test('TCL-07 /suites as a QA engineer: Edit under the row, prefilled; the rename note; ONE PATCH of only the changed fields; the toast; the new name', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, '/suites', 'QA_ENGINEER', h1('Test Suites'))
  await networkQuiet(page, api)
  await suiteRow(page, EMPTY_SUITE_NAME).getByRole('button', { name: `Edit ${EMPTY_SUITE_NAME}`, exact: true }).click()
  const form = editForm(page)
  await expect(form).toBeVisible()
  await expect(form.locator('#suite-edit-title')).toHaveText('Edit suite')
  await expect(page.locator('[data-in-page-row]').locator(form), 'the form opens under its row').toHaveCount(1)
  await expectNoOverlay(page)
  await expect(form.getByLabel('Name')).toHaveValue(EMPTY_SUITE_NAME)
  await expect(form.getByLabel('Description')).toHaveValue('The public checkout endpoints')
  await expect(form.getByLabel('Tags')).toHaveValue('api')
  await expect(form.getByRole('note'), 'no note while the name is unchanged').toHaveCount(0)

  await form.getByLabel('Name').fill(`${EMPTY_SUITE_NAME} v2`)
  await expect(form.getByRole('note')).toHaveText(
    `Past runs keep reporting “${EMPTY_SUITE_NAME}”: their results stay under that name on Test Cases › Suites. `
    + 'A run that still sends the old name creates a suite of that name again for tests new to this project.',
  )
  await form.getByLabel('Description').fill('Public checkout endpoints, v2')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText(`Suite “${EMPTY_SUITE_NAME} v2” saved.`, { exact: true })).toBeVisible()
  await expect(form).toHaveCount(0)
  await expect(suiteRow(page, `${EMPTY_SUITE_NAME} v2`)).toHaveCount(1)
  await networkQuiet(page, api)
  expect(patches(world)).toEqual([{
    line: `PATCH /api/v1/suites/${EMPTY_SUITE_ID}`,
    body: { name: `${EMPTY_SUITE_NAME} v2`, description: 'Public checkout endpoints, v2' },
  }])
  assertHermetic(api, errors)
})

test('TCL-07 a name another suite uses renders the 409 sentence in the form; a tester has no Edit', async ({ page }) => {
  const { world, api, errors } = await open(page, '/suites', 'QA_ENGINEER', h1('Test Suites'))
  await suiteRow(page, EMPTY_SUITE_NAME).getByRole('button', { name: `Edit ${EMPTY_SUITE_NAME}`, exact: true }).click()
  const form = editForm(page)
  await form.getByLabel('Name').fill(SUITE)
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(form.getByRole('alert')).toHaveText(LIBRARY_TEXT.suiteNameTaken)
  await expect(form).toBeVisible()
  expect(patches(world)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('TCL-07 a tester sees no Edit on /suites nor on the suite page', async ({ page }) => {
  const { api, errors } = await open(page, '/suites', 'TESTER', h1('Test Suites'))
  await expect(suiteRow(page, EMPTY_SUITE_NAME)).toHaveCount(1)
  await expect(page.getByRole('button', { name: /^Edit / })).toHaveCount(0)
  await page.goto(`/suites/${SUITE_ID}`)
  await expect(page.locator('[data-suite-tests]')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit suite', exact: true })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('TCL-07 /suites/<id>: Edit suite under the header; the Tests tab shows the latest run\'s date as a second line', async ({ page }) => {
  const { api, errors } = await open(page, `/suites/${SUITE_ID}`, 'QA_ENGINEER', (p) => p.locator('[data-suite-tests]'))
  const dates = page.locator('[data-suite-tests] [data-last-seen-at]')
  await expect(dates).toHaveCount(4)
  // `last_seen_at` = the newest run's created_at, 2 hours before the pinned NOW (2026-09-18 12:00 UTC).
  await expect(dates.first()).toHaveText('Sep 18, 10:00')
  await expect(dates.first()).toHaveAttribute('data-last-seen-at', '2026-09-18T10:00:00.000Z')
  await expect(page.locator('[data-suite-tests] tbody tr').first().getByRole('link', { name: /^run / })).toBeVisible()

  await page.getByRole('button', { name: 'Edit suite', exact: true }).click()
  const form = editForm(page)
  await expect(form).toBeVisible()
  await expect(form.getByLabel('Name')).toHaveValue(SUITE)
  await expectNoOverlay(page)
  await form.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(form).toHaveCount(0)
  assertHermetic(api, errors)
})

test('TCL-07 Test Cases › Suites (a QA lead): a suite before its first run is an Empty card, its name a link to its page', async ({ page }) => {
  const { api, errors } = await open(page, '/test-management?tab=suites', 'QA_LEAD', tmReady)
  const card = page.locator('[data-suite-empty="true"]')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText(EMPTY_SUITE_NAME)
  await expect(card).toContainText('Empty')
  await expect(card).toContainText(LIBRARY_TEXT.suiteEmptyLine)
  await expect(card.getByText(/pass rate|passed|failed/i)).toHaveCount(0)
  expect(await card.getByRole('link', { name: EMPTY_SUITE_NAME, exact: true }).getAttribute('href')).toBe(`/suites/${EMPTY_SUITE_ID}`)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
