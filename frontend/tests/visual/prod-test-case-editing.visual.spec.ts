/**
 * W28 "test-case editing" baselines (`docs/deferred-scope-2026-10-13/work/W28-contract.md` §6.5, §7.3, the eight NEW
 * regions), both themes, each region the in-page container itself, blurred, at `PINNED_TALL` (1280 × 4000, nothing
 * scrolled, as `visualRegion` requires):
 *
 *  - `tm-case-edit-inline`: the edit form open in the case pane, expanded (a Draft with two steps, a masked and a plain
 *    parameter);
 *  - `tm-case-edit-conflict`: the 409 notice inside the form (`Changed by Priya Shah since you opened it (v1 → v3).`);
 *  - `tm-case-toolbar-review-off`: a Draft's Status moves as a TESTER, review optional;
 *  - `tm-case-toolbar-review-on`: an edited-after-approval Active case as the QA lead, review required;
 *  - `tm-bulk-bar`: the bulk bar after an Apply with one case skipped (its title and reason listed);
 *  - `tm-history-diff`: a History card with step changes and its Compare with current block open;
 *  - `tm-plan-item-version`: a plan's items with the Ran against column and v1's steps open under its row;
 *  - `chat-suggestions-row`: the chat's suggested questions above the box, a conversation open.
 *
 * The three CHANGED regions are NOT shot here: `projects-edit-inline` (Edit Project's review checkbox),
 * `api-key-created-inline` (the snippet's comment) and `tm-insights-inline` (the automation split now adds to 100) are
 * shot, unedited, by `prod-in-page-b.visual.spec.ts`: a baseline's path is its region name alone
 * (`{testDir}/__screenshots__/{platform}/{arg}{ext}`), so a second spec shooting one would be a second writer of the
 * same PNG. Their new content is asserted, without a shot, by the last test below.
 *
 * Hermetic fixtures: `production/fixtures-test-case-editing.ts`. PNGs are rendered by CI only
 * (`visual-baselines.yml`, Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, PINNED_TALL, THEMES, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { USER } from './production/fixtures'
import { keysInPageWorld, LEGACY_PROJECT, testLibraryWorld, TM_PLAN_NAME, tmCaseId, tmCaseTitle } from './production/fixtures-in-page-b'
import {
  ACTIVE_CASE,
  ADMIN,
  BULK_CASES,
  chatSuggestionsWorld,
  EDIT_CASE,
  HISTORY_CASE,
  projectsReviewWorld,
  QA_ENGINEER,
  TESTER,
  testCaseEditingWorld,
  type FixtureUser,
  type TestCaseEditingOptions,
} from './production/fixtures-test-case-editing'

test.use(PINNED_TALL)

const V2 = { test_case_lifecycle_v2: true }
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
const caseRow = (p: Page, i: number) => p.locator(`[data-primary] tbody tr[data-case-row="${tmCaseId(i)}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')

async function openTm(page: Page, theme: (typeof THEMES)[number], options: TestCaseEditingOptions & { user?: FixtureUser; path?: string } = {}) {
  const world = testCaseEditingWorld({ lifecycleV2: true, ...options })
  const opened_ = await openRollout(page, options.path ?? '/test-management', { theme, handlers: world.handlers, ready: tmReady, user: options.user ?? USER, flags: V2 })
  return { world, ...opened_ }
}

async function openCase(page: Page, i: number) {
  await caseRow(page, i).click()
  await expect(pane(page).getByRole('heading', { name: tmCaseTitle(i) })).toBeVisible()
}

for (const theme of THEMES) {
  test(`the edit form in the expanded case pane — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme, { user: QA_ENGINEER })
    await openCase(page, EDIT_CASE)
    await pane(page).getByRole('group', { name: 'Case actions' }).getByRole('button', { name: 'Edit', exact: true }).click()
    const form = opened(page, 'Edit test case')
    await expect(form.getByRole('textbox', { name: 'Title', exact: true })).toBeVisible()
    await pane(page).getByRole('button', { name: 'Expand the case to full width' }).click()
    await expect(pane(page)).toHaveAttribute('data-expanded', 'true')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-case-edit-inline', theme, form)
    assertHermetic(api, errors)
  })

  test(`the conflict notice inside the edit form — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme, { user: QA_ENGINEER, conflictOnFirstPatch: true })
    await openCase(page, EDIT_CASE)
    await pane(page).getByRole('group', { name: 'Case actions' }).getByRole('button', { name: 'Edit', exact: true }).click()
    const form = opened(page, 'Edit test case')
    await form.getByLabel('Description', { exact: true }).fill('The cart keeps its items across a refresh and a new tab.')
    await form.getByRole('button', { name: 'Save changes' }).click()
    const notice = form.locator('[data-edit-conflict]')
    await expect(notice).toContainText('Changed by Priya Shah since you opened it (v1 → v3).')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-case-edit-conflict', theme, notice)
    assertHermetic(api, errors)
  })

  test(`a Draft's Status moves as a tester, review optional — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme, { user: TESTER })
    await openCase(page, EDIT_CASE)
    const control = pane(page).locator('[data-case-status-control]')
    await expect(control.getByRole('button', { name: 'Make active', exact: true })).toBeVisible()
    await expect(control).toContainText('Review is optional in this project.')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-case-toolbar-review-off', theme, control)
    assertHermetic(api, errors)
  })

  test(`an edited-after-approval Active case as the QA lead, review required — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme, { reviewRequired: true, editedAfterApproval: true })
    await openCase(page, ACTIVE_CASE)
    const control = pane(page).locator('[data-case-status-control]')
    await expect(control.locator('[data-edited-after-approval]')).toContainText('Edited after approval (since v2; now v2).')
    await expect(control).toContainText('Review is required before Active in this project.')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-case-toolbar-review-on', theme, control)
    assertHermetic(api, errors)
  })

  test(`the bulk bar after an Apply with one case skipped — ${theme}`, async ({ page }) => {
    const [draft, approved] = BULK_CASES
    const { api, errors } = await openTm(page, theme, { user: QA_ENGINEER, bulkSkips: [tmCaseId(approved)] })
    await expect(caseRow(page, draft)).toBeVisible()
    for (const i of [draft, approved]) await page.getByRole('checkbox', { name: `Select ${tmCaseTitle(i)}`, exact: true }).check()
    const bar = page.getByRole('toolbar', { name: 'Bulk edit' })
    await bar.getByLabel('Action', { exact: true }).selectOption({ label: 'Change status' })
    await bar.getByLabel('New status', { exact: true }).selectOption({ label: 'Make active' })
    await bar.getByRole('button', { name: 'Apply', exact: true }).click()
    await expect(bar.getByRole('status')).toHaveText('1 changed · 1 skipped')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-bulk-bar', theme, bar)
    assertHermetic(api, errors)
  })

  test(`a History card's step changes with Compare with current open — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme)
    await openCase(page, HISTORY_CASE)
    await pane(page).getByRole('button', { name: 'History', exact: true }).click()
    const card = pane(page).locator('[data-history-version="2"]')
    await expect(card.getByTestId('history-changes-v2')).toBeVisible()
    await card.getByRole('button', { name: 'Compare with current' }).click()
    await expect(card.locator('[data-history-compare]')).toContainText('v2 → current (v3)')
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-history-diff', theme, card)
    assertHermetic(api, errors)
  })

  test(`a plan's items with the version a result ran against, its steps open — ${theme}`, async ({ page }) => {
    const { api, errors } = await openTm(page, theme, { path: '/test-management?tab=plans', planItems: true })
    await page.getByRole('heading', { name: TM_PLAN_NAME }).click()
    const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: 'Ran against' }) })
    const row = table.locator('tbody > tr:not([data-in-page-row])').filter({ hasText: tmCaseId(ACTIVE_CASE).slice(0, 8) })
    await row.getByRole('button', { name: 'v1', exact: true }).click()
    await expect(table.locator('[data-plan-item-steps="1"] [data-plan-item-step]')).toHaveCount(2)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'tm-plan-item-version', theme, table)
    assertHermetic(api, errors)
  })

  test(`the chat's suggested questions above the box — ${theme}`, async ({ page }) => {
    const world = chatSuggestionsWorld()
    const { api, errors } = await openRollout(page, '/chat', { theme, handlers: world.handlers, ready: (p) => p.getByRole('heading', { name: 'Ask about Checkout' }), flags: { ask_ai_chat: true } })
    await page.getByTestId('chat-input').fill('Why did build 105 fail?')
    await page.getByTestId('chat-input').press('Enter')
    await expect(page.getByTestId('chat-assistant-message').first()).toContainText('Answer 1:')
    const row = page.getByRole('group', { name: 'Suggested questions' })
    await expect(row.getByTestId('chat-suggestion')).toHaveCount(5)
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'chat-suggestions-row', theme, row)
    assertHermetic(api, errors)
  })
}

// The three CHANGED regions are shot by `prod-in-page-b.visual.spec.ts` (unedited); here only their new content is
// asserted, in the same containers and worlds, so the reader of the re-shot PNGs knows what each must show.
test.describe('the CHANGED regions (shot by prod-in-page-b) hold the W28 change', () => {
  test('tm-insights-inline: the automation split adds to 100 (3 of 8 automated: 38 % and 62 %)', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/test-management', { handlers: testLibraryWorld().handlers, ready: tmReady })
    await page.getByRole('button', { name: /^Insights/ }).click()
    const insights = opened(page, 'Insights')
    await expect(insights).toContainText('Automated · 38%')
    await expect(insights).toContainText('Manual · 62%')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('projects-edit-inline: Edit Project holds the review checkbox', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/projects', { handlers: projectsReviewWorld().handlers, ready: h1('Projects') })
    const card = page.locator('.card').filter({ has: page.getByText(LEGACY_PROJECT.name, { exact: true }) })
    await card.hover()
    await card.getByRole('button', { name: 'Edit' }).click()
    await expect(opened(page, 'Edit Project').getByLabel('Require review before a test case becomes Active', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })

  test('api-key-created-inline: the Python snippet\'s comment names the file shown above on the page', async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/api-keys', { handlers: keysInPageWorld().handlers, ready: h1('Streaming API keys'), user: ADMIN })
    await networkQuiet(page, api)
    await page.getByRole('button', { name: 'Generate streaming key' }).click()
    await page.getByPlaceholder('ci-runner-prod').fill('ci-runner-staging')
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    const created = opened(page, 'API key generated')
    await expect(created).toContainText('# (the file shown above on this page).')
    await expect(created).not.toContainText('tab on this dialog')
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
})
