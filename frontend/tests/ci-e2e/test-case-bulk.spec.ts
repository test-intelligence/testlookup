/**
 * W28 TCM-03 "bulk actions on selected cases" and the "⌘E bulk edit" hint made true
 * (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §2.3, §4.4; the AMENDMENT: the bar offers Deprecate / Archive /
 * Reinstate only to QA leads and admins), hermetic, lifecycle v2 on, review off, 1440 × 900 (the geometry at 1280 and
 * 1440):
 *
 *  - as a QA ENGINEER: each authored row has its checkbox INSIDE the Test case cell (the table keeps five columns), a
 *    click on it never opens the case; three ticked (two Drafts and an Approved) → the bar (`toolbar` "Bulk edit")
 *    `3 selected` → Change status (no Deprecate / Archive / Reinstate offered) → Make active → Apply → exactly ONE
 *    `POST /api/v1/test-management/cases/bulk` with `{ project_id, case_ids (list order), expected_versions,
 *    action: 'transition', transition: 'activate' }` → `3 changed`, both case rolls re-read, the rows Active, the
 *    selection emptied (the bar stays with its result);
 *  - a world that skips one: `2 changed · 1 skipped` and `<title> — Changed since you selected it (v1 → v2)`, the
 *    skipped case still selected;
 *  - Ctrl+E (the footer says `Ctrl+E bulk edit`) with nothing selected opens the bar with its hint and focuses
 *    Action; not while typing in the search box;
 *  - Owner → a member (the members read only when Owner is chosen) → Apply → the body's `assignee_id`;
 *  - as a VIEWER: no checkbox, no header box, no hint in the footer, and Ctrl+E opens nothing;
 *  - as a QA LEAD the retire moves are offered, a Deprecate needs a Reason; the bar with a Reason showing (its widest
 *    state) stays in the page at 1280 and 1440 (`INPAGE /test-management bulk-bar …`), and Apply sends the reason.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testCaseEditingWorld()`
 * (`fixtures-test-case-editing.ts`, over `testLibraryWorld()`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack, ownOverflow, precedes } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID, USER } from '../visual/production/fixtures'
import { tmCaseId, tmCaseTitle } from '../visual/production/fixtures-in-page-b'
import {
  BULK_CASES,
  NEW_OWNER,
  QA_ENGINEER,
  testCaseEditingWorld,
  VIEWER,
  type FixtureUser,
  type TestCaseEditingOptions,
} from '../visual/production/fixtures-test-case-editing'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const CASES = '/api/v1/test-management/cases'
const BULK = `${CASES}/bulk`
const MEMBERS = `/api/v1/projects/${P}/members`
const V2 = { test_case_lifecycle_v2: true }
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const selectBox = (p: Page, i: number) => p.getByRole('checkbox', { name: `Select ${tmCaseTitle(i)}`, exact: true })
const bar = (p: Page) => p.getByRole('toolbar', { name: 'Bulk edit' })
const footer = (p: Page) => p.locator('[data-cases-footer-count]')
const HINT = 'Select cases with the checkboxes to edit them together.'
/** The two reads a write refreshes: the table's page (`size=25`) and the library health (W13: `GET /test-management/health`, was the `size=200` roll). */
const rollReads = (api: Parameters<typeof requestsTo>[0]) => {
  const lines = requestsTo(api, CASES)
  return { table: lines.filter((l) => l.includes('size=25')).length, health: requestsTo(api, '/api/v1/test-management/health').length }
}

async function open(page: Page, options: TestCaseEditingOptions & { user?: FixtureUser } = {}) {
  const world = testCaseEditingWorld({ lifecycleV2: true, ...options })
  const opened_ = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady, user: options.user ?? USER, flags: V2 })
  await expect(caseRow(page, 0)).toBeVisible()
  return { world, ...opened_ }
}

/** How many lines a wrapping row's children take (a child starts a new line when it begins below the line's bottom). */
async function wrappedLines(row: Locator): Promise<number> {
  return row.evaluate((el: HTMLElement) => {
    const rects = Array.from(el.children).map((child) => child.getBoundingClientRect()).sort((a, b) => a.top - b.top)
    let lines = 0
    let bottom = -Infinity
    for (const r of rects) {
      if (r.top >= bottom - 1) {
        lines += 1
        bottom = r.bottom
      } else bottom = Math.max(bottom, r.bottom)
    }
    return lines
  })
}

/** Tick `cases` (in that order). */
async function tick(page: Page, cases: readonly number[]) {
  for (const i of cases) await selectBox(page, i).check()
}

const [DRAFT_A, APPROVED, DRAFT_B] = BULK_CASES
const ids = (cases: readonly number[]) => cases.map(tmCaseId)
const versionsOf = (cases: readonly number[]) => Object.fromEntries(cases.map((i) => [tmCaseId(i), 1]))

test('as a QA engineer: three ticked in the first cell → Change status › Make active → ONE bulk POST → 3 changed; no retire move offered', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER })
  const table = page.locator('[data-primary] table').first()
  await expect(table.getByRole('columnheader'), 'still five columns').toHaveCount(5)
  await expect(caseRow(page, DRAFT_A).locator('td').first().getByRole('checkbox'), 'the box is inside the Test case cell').toHaveCount(1)
  await expect(page.getByRole('checkbox', { name: 'Select all on this page' })).toBeVisible()
  await expect(footer(page)).toHaveText('9 of 9 · Ctrl+E bulk edit')
  await expect(bar(page), 'no bar before a selection').toHaveCount(0)
  await networkQuiet(page, api)

  await tick(page, [DRAFT_A, APPROVED, DRAFT_B])
  await expect(page.locator('[data-case-panel]'), 'a checkbox never opens the case').toHaveCount(0)
  await expect(bar(page)).toBeVisible()
  expect(await precedes(bar(page), page.locator('[data-cases-layout]')), 'the bar is above the cases row').toBe(true)
  await expect(bar(page).locator('[data-bulk-count]')).toHaveText('3 selected')
  await expect(bar(page).getByRole('button', { name: 'Select all 9 on this page' })).toBeVisible()
  await bar(page).getByLabel('Action', { exact: true }).selectOption({ label: 'Change status' })
  const status = bar(page).getByLabel('New status', { exact: true })
  await expect(status.locator('option'), 'a QA engineer is offered no retire move').toHaveText([
    'Choose a status change…', 'Make active', 'Back to draft', 'Needs update', 'Request review',
  ])
  await expect(bar(page).getByRole('button', { name: 'Apply', exact: true }), 'nothing chosen yet').toBeDisabled()
  await status.selectOption({ label: 'Make active' })
  await expectNoOverlay(page)
  const before = rollReads(api)
  await bar(page).getByRole('button', { name: 'Apply', exact: true }).click()

  await expect(bar(page).getByRole('status')).toHaveText('3 changed')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    {
      line: `POST ${BULK}`,
      body: { project_id: P, case_ids: ids([DRAFT_A, APPROVED, DRAFT_B]), expected_versions: versionsOf([DRAFT_A, APPROVED, DRAFT_B]), action: 'transition', transition: 'activate' },
    },
  ])
  const after = rollReads(api)
  expect(after.table, 'the table roll re-reads').toBeGreaterThan(before.table)
  expect(after.health, 'the health roll re-reads').toBeGreaterThan(before.health)
  for (const i of [DRAFT_A, APPROVED, DRAFT_B]) await expect(caseRow(page, i).getByRole('cell', { name: 'Active', exact: true })).toBeVisible()
  await expect(bar(page), 'the bar stays with its result').toBeVisible()
  await expect(bar(page).getByText(HINT), 'every changed case left the selection').toBeVisible()
  for (const i of [DRAFT_A, APPROVED, DRAFT_B]) await expect(selectBox(page, i)).not.toBeChecked()
  assertHermetic(api, errors)
})

test('a case changed since it was selected is skipped and named; it stays selected', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER, bulkSkips: [tmCaseId(APPROVED)] })
  await tick(page, [DRAFT_A, APPROVED, DRAFT_B])
  await bar(page).getByLabel('Action', { exact: true }).selectOption({ label: 'Change status' })
  await bar(page).getByLabel('New status', { exact: true }).selectOption({ label: 'Make active' })
  await bar(page).getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(bar(page).getByRole('status')).toHaveText('2 changed · 1 skipped')
  const skipped = bar(page).locator('[data-bulk-skipped]')
  await expect(skipped).toContainText('Skipped')
  await expect(skipped.locator('li')).toHaveText([`${tmCaseTitle(APPROVED)} — Changed since you selected it (v1 → v2)`])
  await expect(bar(page).locator('[data-bulk-count]')).toHaveText('1 selected')
  await expect(selectBox(page, APPROVED), 'the skipped case stays selected').toBeChecked()
  await expect(selectBox(page, DRAFT_A)).not.toBeChecked()
  await networkQuiet(page, api)
  expect(world.writes).toHaveLength(1)
  assertHermetic(api, errors)
})

test('Ctrl+E opens the bar with its hint and focuses Action, never while typing in the search box; Owner reads the members only when chosen', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: QA_ENGINEER })
  await networkQuiet(page, api)
  const membersAtLoad = requestsTo(api, MEMBERS).length

  // Typing in the search box: the shortcut is the field's, not the page's.
  await page.getByRole('searchbox', { name: 'Search cases' }).click()
  await page.keyboard.press('Control+e')
  await expect(bar(page), 'not while typing').toHaveCount(0)

  await page.getByRole('heading', { name: 'Test Case Management', level: 1 }).click()
  await page.keyboard.press('Control+e')
  await expect(bar(page)).toBeVisible()
  await expect(bar(page).getByText(HINT, { exact: true })).toBeVisible()
  await expect(bar(page).getByLabel('Action', { exact: true }), 'Action is focused').toBeFocused()
  await expect(bar(page).getByRole('button', { name: 'Apply', exact: true }), 'nothing selected: Apply waits').toBeDisabled()
  await networkQuiet(page, api)
  expect(requestsTo(api, MEMBERS).length, 'no member read before Owner is chosen').toBe(membersAtLoad)

  await tick(page, [DRAFT_A, DRAFT_B])
  await bar(page).getByLabel('Action', { exact: true }).selectOption({ label: 'Owner' })
  const owner = bar(page).getByLabel('Owner', { exact: true })
  await expect(owner.locator('option', { hasText: NEW_OWNER.name })).toHaveCount(1)
  expect(requestsTo(api, MEMBERS), 'W13: the members are read ONCE (on load, the Owner filter\'s shared key); choosing Owner reuses them').toEqual([`GET ${MEMBERS}`])
  await owner.selectOption({ label: NEW_OWNER.name })
  await bar(page).getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(bar(page).getByRole('status')).toHaveText('2 changed')
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `POST ${BULK}`, body: { project_id: P, case_ids: ids([DRAFT_A, DRAFT_B]), expected_versions: versionsOf([DRAFT_A, DRAFT_B]), action: 'set_owner', assignee_id: NEW_OWNER.id } },
  ])
  await expectNoOverlay(page)
  assertHermetic(api, errors)
})

test('a viewer: no checkbox, no header box, no hint in the footer; Ctrl+E opens nothing', async ({ page }) => {
  watchNativeDialogs(page)
  const { world, api, errors } = await open(page, { user: VIEWER })
  await expect(page.getByRole('checkbox', { name: /^Select / })).toHaveCount(0)
  await expect(footer(page)).toHaveText('9 of 9')
  await page.getByRole('heading', { name: 'Test Case Management', level: 1 }).click()
  await page.keyboard.press('Control+e')
  await networkQuiet(page, api)
  await expect(bar(page)).toHaveCount(0)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test.describe(`${width} x 900`, () => {
    test.use({ viewport: { width, height: 900 } })

    test(`@${width}: a QA lead's Deprecate needs a Reason; the bar with the Reason showing stays in the page; Apply sends it`, async ({ page }) => {
      watchNativeDialogs(page)
      const { world, api, errors } = await open(page)
      await tick(page, [DRAFT_A, DRAFT_B])
      await bar(page).getByLabel('Action', { exact: true }).selectOption({ label: 'Change status' })
      const status = bar(page).getByLabel('New status', { exact: true })
      await expect(status.locator('option')).toHaveText([
        'Choose a status change…', 'Make active', 'Back to draft', 'Needs update', 'Request review', 'Deprecate', 'Archive', 'Reinstate',
      ])
      await status.selectOption({ label: 'Deprecate' })
      const reason = bar(page).getByLabel('Reason', { exact: true })
      await expect(reason).toBeVisible()
      const apply = bar(page).getByRole('button', { name: 'Apply', exact: true })
      await expect(apply, 'no reason: Apply waits').toBeDisabled()
      await reason.fill('Replaced by the guest checkout cases')
      await expect(apply).toBeEnabled()
      await networkQuiet(page, api)

      await expectNoOverlay(page)
      await expectMainFits(page, `/test-management bulk-bar @${width}`)
      expect(await ownOverflow(bar(page)), `the bar @${width} scrolls sideways`).toBeLessThanOrEqual(1)
      const [barBox, layoutBox] = [await boxOf(bar(page)), await boxOf(page.locator('[data-cases-layout]'))]
      expect(barBox.right, 'the bar stays inside the tab').toBeLessThanOrEqual(layoutBox.right + 1)
      const controls = bar(page).locator(':scope > div').first()
      inPageLine('/test-management bulk-bar (reason)', width, { width: barBox.width, lines: await wrappedLines(controls), slack: await minContentSlack(controls) })

      await apply.click()
      await expect(bar(page).getByRole('status')).toHaveText('2 changed')
      await networkQuiet(page, api)
      expect(world.writes).toEqual([
        {
          line: `POST ${BULK}`,
          body: {
            project_id: P,
            case_ids: ids([DRAFT_A, DRAFT_B]),
            expected_versions: versionsOf([DRAFT_A, DRAFT_B]),
            action: 'transition',
            transition: 'deprecate',
            reason: 'Replaced by the guest checkout cases',
          },
        },
      ])
      assertHermetic(api, errors)
    })
  })
}
