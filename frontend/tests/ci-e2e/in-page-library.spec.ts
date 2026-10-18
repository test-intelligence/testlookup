/**
 * W27b PAN-05a IN-PAGE SHAPE, the test library (`docs/deferred-scope-2026-10-13/work/W27b-contract.md` §2.1, §1.4,
 * §4.1), hermetic, on the CONVERTED pages at 1280 AND 1440. It FAILS on the modal tree by design: every container
 * below was an overlay. For each container: no overlay (`expectNoOverlay`: no `aria-modal`, no `role="dialog"`, no
 * native dialog), its DOM position as §1.4 places it (`precedes`, the next sibling row for a row form, a descendant of
 * its card), a row form's one cell spanning the table and the table as wide as with nothing open (± 1 px),
 * `#main-content` never scrolling sideways, no clipped table, and one `INPAGE <route> <container> @<w> {…}` line
 * (slack: how much narrower it could get before its content overflows; < 40 px at 1280 is a finding for G-A).
 *
 *  - `/test-management` Cases: Insights right under the banner (and its stat-basis line, shown only for a capped
 *    sample: not in this world), above the filter bar, its
 *    cards in two columns from `lg`; New test case between the filter bar / alerts and the cases row, first field
 *    focused; AI Generate replaces it, asking first when a title was typed; a row's Delete / Deprecate form is the row's
 *    next `<tr>` (5 columns, 4 beside an open case); the orphan card's retirement reason is the `<li>` after its item;
 *  - Plans: New Plan under the toolbar, above the plans; Link Suite between its button row and the items;
 *  - Suites tab: ONE Add test suite form, right under the tab's header row; Approvals: the notes form INSIDE the card;
 *    Strategy: under its header row; Knowledge: a case's citations right after that case's card;
 *  - `/suites`: New suite under the header; a suite's Delete strip in a full-width row under it (9 columns);
 *    `/suites/:id`: Move under its row (9 columns with the checkbox), the bulk form right above the tests table;
 *  - `/canonical-test-cases/:id`: Unlink right under the page header; Inbox › Approvals: a case's notes form under its
 *    row (5 columns), a defect's Reject form as the next item of the list.
 *
 * The flows themselves are the `characterize-test-library / -test-lifecycle / -suites` specs. Harness:
 * `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-in-page-b.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened, watchNativeDialogs } from '../lib/inPage'
import { boxOf, clippedTables, expectMainFits, followedBy, inPageLine, minContentSlack, ownOverflow, precedes, tableBox } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { SUITE_ID } from '../visual/production/fixtures'
import {
  CANONICAL_ID,
  lifecycleHostsWorld,
  LEGACY_SUITE_NAME,
  ORPHANS,
  RAG_ANSWER,
  suitesInPageWorld,
  testLibraryWorld,
  TM_PLAN_NAME,
  tmCaseId,
  tmCaseTitle,
} from '../visual/production/fixtures-in-page-b'

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const headerReady = (p: Page) => p.locator('[data-page-header] h1')
const header = (page: Page) => page.locator('[data-page-header]')
const ownRows = (table: Locator) => table.locator('tbody > tr:not([data-in-page-row])')

/** Focus moved into `container` when it opened (the first field, else the first control). */
async function expectFocusInside(container: Locator, what: string) {
  expect(await container.evaluate((el) => el.contains(document.activeElement)), `${what}: focus moved into it`).toBe(true)
}

/** `container` is the in-page row right after `row` (one cell spanning `columns`), and the table kept its width. */
async function expectRowForm(page: Page, what: string, width: number, table: Locator, row: Locator, columns: number, container: Locator, closed: { table: number }) {
  await expectNoOverlay(page)
  const next = row.locator('xpath=following-sibling::tr[1]')
  await expect(next, `${what}: the next row is the in-page row`).toHaveAttribute('data-in-page-row', '')
  await expect(next.locator(':scope > td'), `${what}: one cell`).toHaveCount(1)
  await expect(next.locator(':scope > td'), `${what}: spanning every column`).toHaveAttribute('colspan', String(columns))
  await expect(next.locator(':scope > td').locator(container), `${what}: the container is in that row`).toHaveCount(1)
  const open = await tableBox(table)
  expect(Math.abs(open.table - closed.table), `${what} @${width}: the table keeps its width`).toBeLessThanOrEqual(1)
  await expectMainFits(page, `${what} @${width}`)
  expect(await clippedTables(page.locator('#main-content')), `${what} @${width}: a clipped table`).toEqual([])
  const [box, edge] = [await boxOf(container), await boxOf(table)]
  expect(box.right, `${what} @${width}: inside the table`).toBeLessThanOrEqual(edge.right + 1)
  expect(await ownOverflow(container), `${what} @${width}: scrolls sideways`).toBeLessThanOrEqual(1)
  const [tableSlack, formSlack] = [await minContentSlack(table), await minContentSlack(container)]
  inPageLine(what, width, { table: open.table, box: open.box, form: box.width, tableSlack, formSlack, slack: Math.min(tableSlack, formSlack) })
}

/** A section in the page's flow: no overlay, main fits, its slack printed. */
async function expectSection(page: Page, what: string, width: number, container: Locator, extra: Record<string, unknown> = {}) {
  await expectNoOverlay(page)
  await expectMainFits(page, `${what} @${width}`)
  expect(await ownOverflow(container), `${what} @${width}: scrolls sideways`).toBeLessThanOrEqual(1)
  const box = await boxOf(container)
  inPageLine(what, width, { width: box.width, slack: await minContentSlack(container), ...extra })
}

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test(`/test-management Cases @${width}: Insights under the banner in two columns; New test case above the cases row; AI Generate asks before replacing it`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = testLibraryWorld()
      const { api, errors } = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady })
      // The banner (the stat-basis line under it shows only when the stats cover part of the library: not here).
      const banner = page.locator('[data-status-banner]').first()
      const search = page.getByPlaceholder('Search by title, tags or ID…')
      const layout = page.locator('[data-cases-layout]')
      await expect(layout).toBeVisible()
      await networkQuiet(page, api)
      await expectNoOverlay(page)

      // Insights: under the banner and the stat-basis line, above the filter bar; two card columns from lg.
      await page.getByRole('button', { name: /^Insights/ }).click()
      const insights = opened(page, 'Insights')
      await expect(insights).toBeVisible()
      expect(await precedes(banner, insights), 'Insights after the banner').toBe(true)
      await expect(page.getByTestId('library-health-stat-basis'), 'no stat-basis line in this world').toHaveCount(0)
      expect(await precedes(insights, search), 'Insights above the filter bar').toBe(true)
      const cards = insights.locator('[data-insights] > *')
      await expect(cards).toHaveCount(5)
      const [first, second] = [await boxOf(cards.nth(0)), await boxOf(cards.nth(1))]
      expect(second.left, 'the second card beside the first (two columns)').toBeGreaterThan(first.right - 1)
      expect(first.width, 'each card at least 440 px (the old drawer)').toBeGreaterThanOrEqual(440)
      await expectSection(page, '/test-management insights', width, insights, { card: first.width })
      await insights.getByRole('button', { name: 'Close panel' }).click()
      await expect(insights).toHaveCount(0)

      // New test case: between the filter bar and the cases row, full width, first field focused.
      await page.getByRole('button', { name: 'New test case from catalog toolbar' }).click()
      const create = opened(page, 'New Test Case')
      await expect(create).toBeVisible()
      expect(await precedes(search, create), 'the form after the filter bar').toBe(true)
      expect(await precedes(create, layout), 'the form above the cases row').toBe(true)
      await expect(create.getByLabel('Title *'), 'the first field is focused').toBeFocused()
      await expectSection(page, '/test-management new-case', width, create)

      // AI Generate replaces it, asking first when a title was typed.
      await create.getByLabel('Title *').fill('Guest checkout keeps the basket')
      await page.getByRole('button', { name: 'AI Generate', exact: true }).first().click()
      const ask = create.locator('[data-inline-confirm]')
      await expect(ask).toContainText('Discard your changes to "New Test Case"?')
      await ask.getByRole('button', { name: 'Keep editing' }).click()
      await expect(create.getByLabel('Title *'), 'Keep editing keeps the text').toHaveValue('Guest checkout keeps the basket')
      await expect(opened(page, 'AI Generate Test Cases')).toHaveCount(0)
      await page.getByRole('button', { name: 'AI Generate', exact: true }).first().click()
      await create.locator('[data-inline-confirm]').getByRole('button', { name: 'Discard changes' }).click()
      const generate = opened(page, 'AI Generate Test Cases')
      await expect(generate).toBeVisible()
      await expect(create).toHaveCount(0)
      expect(await precedes(generate, layout), 'AI Generate above the cases row too').toBe(true)
      await expect(generate.getByLabel('What to generate test cases for')).toBeFocused()
      await expectNoOverlay(page)
      await generate.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(generate).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/test-management Cases @${width}: a row's Deprecate form is its next row (5 columns, 4 beside an open case); Escape closes it`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = testLibraryWorld({ lifecycleV2: true })
      const { api, errors } = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady, flags: { test_case_lifecycle_v2: true } })
      const table = page.locator('[data-primary] table').first()
      const row = page.locator(`[data-primary] tr[data-case-row="${tmCaseId(1)}"]`)
      await expect(row).toBeVisible()
      await networkQuiet(page, api)

      const closedWide = await tableBox(table)
      const trash = page.getByRole('button', { name: `Deprecate ${tmCaseTitle(1)}` })
      await trash.click()
      let form = opened(page, 'Deprecate test case')
      await expect(form).toBeVisible()
      await expectFocusInside(form, 'the reason form')
      await expectRowForm(page, '/test-management deprecate', width, table, row, 5, form, closedWide)
      await form.getByLabel('Reason').press('Escape')
      await expect(form).toHaveCount(0)
      await expect(trash, 'focus returns to the trash button').toBeFocused()

      // Beside an open case the Owner column folds: 4 columns.
      await page.locator(`[data-primary] tr[data-case-row="${tmCaseId(0)}"]`).click()
      await expect(page.locator('[data-case-panel]')).toBeVisible()
      const closedSplit = await tableBox(table)
      await page.getByRole('button', { name: `Deprecate ${tmCaseTitle(1)}` }).click()
      form = opened(page, 'Deprecate test case')
      await expectRowForm(page, '/test-management deprecate (case open)', width, table, row, 4, form, closedSplit)
      await form.getByRole('button', { name: 'Cancel' }).click()
      await expect(form).toHaveCount(0)
      await expect(page.locator('[data-case-panel]'), 'the case stays open').toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/test-management Cases @${width}: the orphan card's retirement reason is the list item after its orphan`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = lifecycleHostsWorld()
      const { api, errors } = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady })
      const card = page.getByRole('region', { name: 'Execution evidence gaps' }).locator('article').filter({ hasText: 'Unconfirmed automation retirements' })
      await card.getByRole('button', { name: /^Unconfirmed automation retirements/ }).click()
      const item = card.locator('li:not([data-in-page-row])').filter({ hasText: ORPHANS[0].test_name })
      await expect(item).toBeVisible()
      await networkQuiet(page, api)
      await item.getByRole('button', { name: 'Confirm retirement' }).click()
      const form = opened(page, 'Confirm automation retirement')
      await expect(form).toBeVisible()
      await expectNoOverlay(page)
      const next = item.locator('xpath=following-sibling::li[1]')
      await expect(next, 'the form is the next list item').toHaveAttribute('data-in-page-row', '')
      await expect(next.locator(form)).toHaveCount(1)
      await expectFocusInside(form, 'the retirement form')
      const cardBox = await boxOf(card)
      const formBox = await boxOf(form)
      expect(formBox.right, 'the form stays inside its card').toBeLessThanOrEqual(cardBox.right + 1)
      await expectSection(page, '/test-management orphan-retirement', width, form, { card: cardBox.width })
      await form.getByRole('button', { name: 'Cancel' }).click()
      await expect(form).toHaveCount(0)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/test-management Plans, Suites, Approvals, Strategy, Knowledge @${width}: each form where its action lives`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = testLibraryWorld()
      const { api, errors } = await openRollout(page, '/test-management?tab=plans', { handlers: world.handlers, ready: tmReady })
      const planHeading = page.getByRole('heading', { name: TM_PLAN_NAME })
      await expect(planHeading).toBeVisible()
      await networkQuiet(page, api)

      // New Plan: under the toolbar, above the plans.
      await page.getByRole('button', { name: 'New Plan' }).first().click()
      const plan = opened(page, 'New Test Plan')
      await expect(plan).toBeVisible()
      expect(await precedes(plan, planHeading), 'New Plan above the plans').toBe(true)
      await expectSection(page, '/test-management new-plan', width, plan)
      await plan.getByRole('button', { name: 'Cancel' }).click()

      // Link Suite: between its button row and the plan's items.
      await planHeading.click()
      const linkButton = page.getByRole('button', { name: 'Link Suite', exact: true }).first()
      const items = page.getByText('No test cases in this plan yet. Link a suite or add test cases.')
      await expect(items).toBeVisible()
      await linkButton.click()
      const link = opened(page, 'Link Test Suite to Plan')
      await expect(link).toBeVisible()
      expect(await precedes(linkButton, link), 'after its button row').toBe(true)
      expect(await precedes(link, items), 'above the items').toBe(true)
      await expectSection(page, '/test-management link-suite', width, link)
      await link.getByRole('button', { name: 'Cancel' }).click()

      // Suites: ONE Add test suite form, right under the header row.
      await page.goto('/test-management?tab=suites')
      const count = page.getByText('2 suites')
      await expect(count).toBeVisible()
      await page.getByRole('button', { name: 'Add test suite' }).click()
      const suite = opened(page, 'Add test suite')
      await expect(suite).toHaveCount(1)
      expect(await precedes(count, suite), 'under the header row').toBe(true)
      expect(await precedes(suite, page.getByRole('link', { name: 'Checkout', exact: true }).first()), 'above the suites').toBe(true)
      await expectSection(page, '/test-management add-suite', width, suite)
      await suite.getByRole('button', { name: 'Cancel' }).click()

      // Approvals: the notes form INSIDE the case's card.
      await page.goto('/test-management?tab=approvals')
      const card = page.locator('[data-tab-panel="approvals"] .card').filter({ has: page.getByRole('heading', { name: tmCaseTitle(2), level: 4 }) })
      await card.getByRole('button', { name: 'Request changes', exact: true }).first().click()
      const notes = opened(page, 'Request changes')
      await expect(card.locator(notes), 'the form is inside its card').toHaveCount(1)
      await expectSection(page, '/test-management approval-notes', width, notes, { card: (await boxOf(card)).width })
      await notes.getByRole('button', { name: 'Cancel' }).click()

      // Strategy: under its header row, above the empty state.
      await page.goto('/test-management?tab=strategy')
      await page.getByRole('button', { name: 'Generate Strategy' }).first().click()
      const strategy = opened(page, 'Generate Test Strategy with AI')
      await expect(strategy).toBeVisible()
      expect(await precedes(strategy, page.getByText('No test strategy')), 'above the strategy').toBe(true)
      await expectSection(page, '/test-management strategy', width, strategy)
      await strategy.getByRole('button', { name: 'Cancel' }).click()

      // Knowledge: the citations right after the case's card; another case's replaces them.
      await page.goto('/test-management?tab=knowledge')
      await page.getByPlaceholder(/^Describe what test cases to generate/).fill('Coupons and tax')
      await page.getByRole('button', { name: 'Generate Test Cases' }).click()
      const caseCard = (title: string) =>
        page.getByRole('heading', { name: title, level: 4 }).locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " space-y-2 ")][1]')
      const [one, two] = RAG_ANSWER.test_cases
      await caseCard(one.title).getByRole('button', { name: 'Citations' }).click()
      let cites = page.getByRole('complementary', { name: 'Citations (2)' })
      await expect(cites).toBeVisible()
      expect(await followedBy(caseCard(one.title), cites), 'the citations right after the case card').toBe(true)
      expect(await precedes(cites, caseCard(two.title)), 'before the next case, not after the list').toBe(true)
      await caseCard(two.title).getByRole('button', { name: 'Citations' }).click()
      cites = page.getByRole('complementary', { name: 'Citations (1)' })
      await expect(cites).toBeVisible()
      await expect(page.getByRole('complementary', { name: /^Citations \(/ }), 'one citations section at a time').toHaveCount(1)
      expect(await followedBy(caseCard(two.title), cites), 'now right after the second case').toBe(true)
      await expectSection(page, '/test-management citations', width, cites)
      await cites.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(cites).toHaveCount(0)
      await expectNoOverlay(page)
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.line)).toEqual(['POST /api/v1/test-management/cases/rag-generate'])
      assertHermetic(api, errors)
    })

    test(`/suites and /suites/:id @${width}: New suite under the header, the Delete strip and Move under their rows, the bulk form above the tests`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = suitesInPageWorld()
      const { api, errors } = await openRollout(page, '/suites', { handlers: world.handlers, ready: (p) => p.getByRole('heading', { name: 'Test Suites', level: 1 }) })
      const list = page.locator('[data-primary]')
      await expect(list.getByText(LEGACY_SUITE_NAME)).toBeVisible()
      await networkQuiet(page, api)

      await page.getByRole('button', { name: 'New suite' }).click()
      const create = opened(page, 'New Test Suite')
      await expect(create).toBeVisible()
      expect(await precedes(header(page), create), 'under the header').toBe(true)
      expect(await precedes(create, list), 'above the list').toBe(true)
      await expect(create.getByLabel('Name'), 'the name is focused').toBeFocused()
      await expectSection(page, '/suites new-suite', width, create)
      await create.getByRole('button', { name: 'Cancel' }).click()

      const table = list.getByRole('table')
      const row = ownRows(table).filter({ has: page.locator('[data-suite-name]', { hasText: LEGACY_SUITE_NAME }) })
      const closed = await tableBox(table)
      await row.getByRole('button', { name: 'Delete' }).click()
      const strip = page.locator('[data-inline-confirm]')
      await expect(strip).toContainText(`Delete suite "${LEGACY_SUITE_NAME}"? Test cases must already be moved out.`)
      await expectRowForm(page, '/suites delete', width, table, row, 9, strip, closed)
      await strip.getByRole('button', { name: 'Cancel' }).click()
      await expect(strip).toHaveCount(0)
      await expect(page, 'the strip never opened the suite').toHaveURL(/\/suites$/)

      await page.goto(`/suites/${SUITE_ID}`)
      const tests = page.locator('[data-suite-tests]')
      const testsTable = tests.getByRole('table')
      const testRow = ownRows(testsTable).filter({ hasText: 'token refresh' })
      await expect(testRow).toBeVisible()
      const closedTests = await tableBox(testsTable)
      await testRow.getByRole('button', { name: 'Move', exact: true }).click()
      const move = opened(page, 'Move test case')
      await expect(move).toBeVisible()
      await expectRowForm(page, '/suites/:id move', width, testsTable, testRow, 9, move, closedTests)
      await move.getByRole('button', { name: 'Cancel' }).click()
      await expect(move).toHaveCount(0)

      await page.getByRole('checkbox', { name: 'Select logout clears session' }).check()
      await page.getByRole('checkbox', { name: 'Select login succeeds' }).check()
      await page.getByRole('region', { name: 'Bulk actions' }).getByRole('button', { name: 'Move', exact: true }).click()
      const bulk = opened(page, 'Move 2 test cases')
      await expect(bulk).toBeVisible()
      expect(await precedes(bulk, tests), 'the bulk form right above the tests table').toBe(true)
      expect(await followedBy(bulk, tests), 'nothing between the bulk form and the tests table').toBe(true)
      await expect(bulk, 'scrolled into view').toBeInViewport()
      await expectSection(page, '/suites/:id bulk-move', width, bulk)
      await bulk.getByRole('button', { name: 'Cancel' }).click()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`/canonical-test-cases/:id and Inbox › Approvals @${width}: Unlink under the header, the case notes under the row, the defect Reject as the next item`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = lifecycleHostsWorld()
      // QA lead (the fixture's default user): the defect Reject is a QA lead's (the sweep pins every gate).
      const { api, errors } = await openRollout(page, `/canonical-test-cases/${CANONICAL_ID}`, { handlers: world.handlers, ready: headerReady })
      await networkQuiet(page, api)
      await page.getByRole('button', { name: 'Unlink managed case' }).click()
      const unlink = opened(page, 'Unlink managed case')
      await expect(unlink).toBeVisible()
      expect(await precedes(header(page), unlink), 'under the page header').toBe(true)
      await expectFocusInside(unlink, 'the unlink form')
      await expectSection(page, '/canonical-test-cases/:id unlink', width, unlink)
      await unlink.getByRole('button', { name: 'Cancel' }).click()

      await page.goto('/my-failures?tab=approvals&source=cases')
      const cases = page.locator('[data-primary]').getByRole('region', { name: 'Test cases' })
      const table = cases.getByRole('table')
      const row = cases.getByTestId('case-approval-row').filter({ hasText: 'Checkout case c1' })
      await expect(row).toBeVisible()
      const closed = await tableBox(table)
      await row.getByRole('button', { name: 'Approve', exact: true }).click()
      const notes = opened(page, 'Approve')
      await expect(notes).toBeVisible()
      await expectRowForm(page, '/my-failures case-approval', width, table, row, 5, notes, closed)
      await notes.getByRole('button', { name: 'Cancel' }).click()

      await page.goto('/my-failures?tab=approvals&source=defects')
      const defect = page.locator('[data-primary]').getByRole('region', { name: 'Defects' }).getByTestId('defect-approval-row')
      await expect(defect).toHaveCount(1)
      await defect.getByTestId('defect-reject').click()
      const reject = opened(page, 'Reject this defect?')
      await expect(reject).toBeVisible()
      const next = defect.locator('xpath=following-sibling::li[1]')
      await expect(next, 'the next list item').toHaveAttribute('data-in-page-row', '')
      await expect(next.locator(reject)).toHaveCount(1)
      await expect(page.getByTestId('defect-approval-row'), 'the form row is not a defect row').toHaveCount(1)
      await expectSection(page, '/my-failures defect-reject', width, reject)
      await reject.getByRole('button', { name: 'Cancel' }).click()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })
  })
}

test.describe('nothing open at load', () => {
  test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('every W27b test-library page loads with no overlay and no in-page container open', async ({ page }) => {
    watchNativeDialogs(page)
    const world = testLibraryWorld()
    const { api, errors } = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady })
    for (const path of ['/test-management', '/test-management?tab=plans', '/test-management?tab=suites', '/test-management?tab=approvals', '/test-management?tab=strategy']) {
      if (path !== '/test-management') await page.goto(path)
      await expect(tmReady(page)).toBeVisible()
      await networkQuiet(page, api)
      await expectNoOverlay(page)
      await expect(page.locator('[data-in-page="form"]'), `${path}: no form open at load`).toHaveCount(0)
    }
    assertHermetic(api, errors)
  })
})
