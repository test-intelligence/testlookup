/**
 * W13 "test-library" baselines (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §6.5, the 9 NEW regions), in
 * signal, lab and paper (`THEMES_WITH_PAPER`):
 *
 *  - `/test-management?status=draft,review_requested&owner=me` (a QA engineer, Automation tests on):
 *    `tm-filter-bar` (the bar with two filters set; the authored-only note under it is asserted, outside the region:
 *    the page gives the two no common box), `tm-case-views` (the Views
 *    row with the presets, a saved view of mine, another member's shared one, and the Save form open);
 *  - `/test-management` Insights (a QA lead): `tm-strategy-gap-list` (Strategy gaps with Flaky and active's first
 *    five open);
 *  - `/test-management` Import (a tester): `tm-import-preview` (the dry run: create, duplicate and invalid rows);
 *  - a case's Timeline tab: `tm-case-timeline` (six kinds on the first page, Load more under them);
 *  - the traceability flag ON: `tm-case-requirements` (a case's three links and the Add form), `tm-requirement-coverage`
 *    (Insights' sixth card);
 *  - `/suites` (a QA engineer): `suite-edit-inline` (Checkout API's Edit form under its row, renamed: the note);
 *  - `/test-management?tab=suites`: `tm-suites-empty-card` (Checkout API before its first run).
 *
 * CHANGED (shot by their own specs, unedited here): `tm-insights-inline`, `suites-list`, `suite-page-tests` (§6.5).
 * The focus ring is not part of a baseline: the page is blurred before each region. The 4000 px viewport
 * (`PINNED_TALL`) keeps every region on screen with nothing scrolled. Hermetic fixtures:
 * `production/fixtures-test-library.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux); nobody
 * regenerates them locally (a local run passes `--update-snapshots=none` to prove each region is reached).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, cardByHeading, PINNED_TALL, THEMES_WITH_PAPER, visualRegion, type ApiHandlers, type Theme } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import {
  caseFiltersWorld,
  EMPTY_SUITE_NAME,
  IMPORT_FILE_NAME,
  importCsv,
  importDryRun,
  importWorld,
  LIBRARY_USERS,
  REQUIREMENTS_CASE,
  requirementsWorld,
  suitesEditWorld,
  testLibraryHealthWorld,
  TIMELINE_CASE,
  timelineWorld,
  tmCaseId,
  tmCaseTitle,
  TRACEABILITY_FLAG,
  type LibraryRole,
} from './production/fixtures-test-library'

test.use(PINNED_TALL)

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')

interface OpenTm {
  theme: Theme
  handlers: ApiHandlers
  role: LibraryRole
  path?: string
  flags?: Record<string, boolean>
}

async function openTm(page: Page, { theme, handlers, role, path = '/test-management', flags }: OpenTm) {
  return openRollout(page, path, { theme, handlers, ready: tmReady, user: LIBRARY_USERS[role], flags })
}

/** Open case `i`'s pane (its Details tab) from its row. */
async function openCase(page: Page, i: number) {
  await caseRow(page, i).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(i) })).toBeVisible()
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the filter bar with two filters and the authored-only note; the Views row with the Save form — ${theme}`, async ({ page }) => {
    const world = caseFiltersWorld({ role: 'QA_ENGINEER' })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'QA_ENGINEER', path: '/test-management?status=draft,review_requested&owner=me' })
    const note = page.locator('[data-cases-filter-note]')
    await expect(note.getByRole('note')).toHaveText('Authored cases only: automation-ingested tests are left out under these filters.')
    await expect(page.locator('[data-case-filter="status"]')).toHaveAttribute('aria-label', 'Status, 2 selected')
    await networkQuiet(page, api)
    await blur(page)
    // The bar itself (the search box's row): the page gives the bar and the note line under it no common box of their
    // own, so the note is asserted above and the region is the bar with its two filters set.
    await visualRegion(page, 'tm-filter-bar', theme, page.getByRole('searchbox', { name: 'Search cases' }).locator('xpath=../..'))

    const views = page.locator('[data-case-views]')
    await expect(views.locator('[data-case-view]')).toHaveCount(2)
    await views.getByRole('button', { name: 'Save view' }).click()
    const form = opened(page, 'Save this view')
    await form.getByLabel('Name').fill('My open reviews')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-case-views', theme, views)
    assertHermetic(api, errors)
  })

  test(`Insights' Strategy gaps with Flaky and active open — ${theme}`, async ({ page }) => {
    const world = testLibraryHealthWorld({ role: 'QA_LEAD' })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'QA_LEAD' })
    await page.getByRole('button', { name: /^Insights/ }).click()
    const insights = opened(page, 'Insights')
    const gaps = cardByHeading(insights, 'Strategy gaps')
    const flaky = gaps.locator('[data-strategy-gap="flaky_active"]')
    await flaky.getByRole('button', { name: 'Show 5' }).click()
    await expect(flaky.locator('[data-library-case]')).toHaveCount(5)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-strategy-gap-list', theme, gaps)
    assertHermetic(api, errors)
  })

  test(`the Import preview: create, duplicate and invalid rows — ${theme}`, async ({ page }) => {
    const dry = importDryRun()
    const rows = dry.rows.map((r) => (r.row === 4
      ? { ...r, outcome: 'duplicate', reasons: ['Same title, objective, steps and expected result as an existing case.'], duplicate_of: { case_id: tmCaseId(0), title: tmCaseTitle(0), row: null } }
      : r))
    const world = importWorld({ role: 'TESTER', dryRun: { ...dry, summary: { rows: 20, create: 16, duplicate: 1, invalid: 3, created: 0 }, rows } })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'TESTER' })
    await page.getByRole('button', { name: 'Import', exact: true }).click()
    const section = page.locator('[data-tm-import]')
    await section.locator('input[type="file"]').setInputFiles({ name: IMPORT_FILE_NAME, mimeType: 'text/csv', buffer: Buffer.from(importCsv(), 'utf8') })
    await expect(section.locator('[data-import-summary]')).toHaveText('20 rows: 16 to create · 1 duplicate · 3 invalid')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-import-preview', theme, section)
    assertHermetic(api, errors)
  })

  test(`a case's Timeline: six kinds on the first page — ${theme}`, async ({ page }) => {
    const world = timelineWorld({ role: 'QA_LEAD' })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'QA_LEAD' })
    await openCase(page, TIMELINE_CASE)
    await pane(page).getByRole('button', { name: 'Timeline', exact: true }).click()
    const timeline = pane(page).locator('[data-case-timeline]')
    await expect(timeline.locator('[data-timeline-entry]')).toHaveCount(6)
    await expect(timeline.getByRole('button', { name: 'Load more' })).toBeVisible()
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-case-timeline', theme, timeline)
    assertHermetic(api, errors)
  })

  test(`the traceability flag on: a case's Requirements with the Add form, and Insights' coverage card — ${theme}`, async ({ page }) => {
    const world = requirementsWorld({ role: 'TESTER' })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'TESTER', flags: { [TRACEABILITY_FLAG]: true } })
    await openCase(page, REQUIREMENTS_CASE)
    const section = pane(page).locator('[data-case-requirements]')
    await expect(section.locator('[data-requirement-link]')).toHaveCount(3)
    await section.getByRole('button', { name: 'Add requirement' }).click()
    await opened(section, 'Add requirement').getByLabel('Key').fill('REQ-PAY-14')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-case-requirements', theme, section)
    await opened(section, 'Add requirement').getByRole('button', { name: 'Close' }).click()

    await page.getByRole('button', { name: /^Insights/ }).click()
    const card = opened(page, 'Insights').getByTestId('tm-requirement-coverage')
    await expect(card.locator('[data-requirements-verified]')).toContainText('12 of 15')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-requirement-coverage', theme, card)
    assertHermetic(api, errors)
  })

  test(`/suites: a suite's Edit form under its row with the rename note — ${theme}`, async ({ page }) => {
    const world = suitesEditWorld({ role: 'QA_ENGINEER' })
    const { api, errors } = await openRollout(page, '/suites', { theme, handlers: world.handlers, ready: h1('Test Suites'), user: world.user })
    await page.getByRole('button', { name: `Edit ${EMPTY_SUITE_NAME}`, exact: true }).click()
    const form = opened(page, 'Edit suite')
    await form.getByLabel('Name').fill(`${EMPTY_SUITE_NAME} v2`)
    await expect(form.getByRole('note')).toContainText(`Past runs keep reporting “${EMPTY_SUITE_NAME}”`)
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'suite-edit-inline', theme, form)
    assertHermetic(api, errors)
  })

  test(`the Test Cases › Suites tab: a suite before its first run — ${theme}`, async ({ page }) => {
    const world = suitesEditWorld({ role: 'QA_LEAD' })
    const { api, errors } = await openTm(page, { theme, handlers: world.handlers, role: 'QA_LEAD', path: '/test-management?tab=suites' })
    const card = page.locator('[data-suite-empty="true"]')
    await expect(card).toContainText(EMPTY_SUITE_NAME)
    await expect(card).toContainText('Waiting for its first run: no result reports this suite yet.')
    await networkQuiet(page, api)
    await blur(page)
    await visualRegion(page, 'tm-suites-empty-card', theme, card)
    assertHermetic(api, errors)
  })
}
