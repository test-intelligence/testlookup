/**
 * W21d ACT-02 baseline (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §6.5, G-C's ONE new region), signal,
 * lab and paper (`THEMES_WITH_PAPER`), at `PINNED` (1280 × 2400, nothing scrolled, as `visualRegion` requires):
 *
 *  - `test-case-mention-suggestions`: a case's Comments › the comment composer with `@al` typed and its two
 *    suggestions (`Alan Ortiz · @alan.ortiz`, `Alice Ng · @alice`) listed under the textarea, focus blurred.
 *
 * No existing region changes: no region shoots a test case's Comments tab or the bell's list. Hermetic worlds:
 * `production/fixtures-activity-live.ts` over `production/fixtures-test-case-editing.ts`. PNGs are rendered by CI only
 * (Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { activityLiveWorld, defaultComments } from './production/fixtures-activity-live'
import { tmCaseId, tmCaseTitle, tmMembers } from './production/fixtures-in-page-b'
import { EDIT_CASE, TESTER, testCaseEditingWorld } from './production/fixtures-test-case-editing'

test.use(PINNED)

const CASE_ID = tmCaseId(EDIT_CASE)
const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const caseRow = (p: Page) => p.locator(`[data-primary] tbody tr[data-case-row="${CASE_ID}"]:not([data-in-page-row])`)
const pane = (p: Page) => p.locator('[data-case-panel]')
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

function members() {
  const base = tmMembers()
  const like = (n: number, username: string, fullName: string) => ({
    ...base[1],
    id: `00000000-0000-4000-8000-0000000002${String(n).padStart(2, '0')}`,
    user_id: `00000000-0000-4000-8000-0000000003${String(n).padStart(2, '0')}`,
    email: `${username}@example.test`,
    username,
    full_name: fullName,
  })
  return [...base, like(1, 'alice', 'Alice Ng'), like(2, 'alan.ortiz', 'Alan Ortiz')]
}

for (const theme of THEMES_WITH_PAPER) {
  test(`the comment composer offering two members for @al — ${theme}`, async ({ page }) => {
    const live = activityLiveWorld({
      role: TESTER,
      members: members(),
      comments: [{ ...defaultComments()[0], test_case_id: CASE_ID }],
      base: testCaseEditingWorld({ user: TESTER }).handlers,
    })
    const { api, errors } = await openRollout(page, '/test-management', { theme, handlers: live.handlers, ready: tmReady, user: TESTER })
    await caseRow(page).click()
    await expect(pane(page).getByRole('heading', { name: tmCaseTitle(EDIT_CASE) })).toBeVisible()
    await pane(page).getByRole('button', { name: 'Comments', exact: true }).click()
    const composer = pane(page).locator('[data-comment-composer]')
    const box = composer.getByPlaceholder('Add a comment…')
    await box.click()
    await box.pressSequentially('@al')
    await expect(composer.locator('[data-mention-suggestions]').getByRole('button')).toHaveText([
      'Alan Ortiz · @alan.ortiz',
      'Alice Ng · @alice',
    ])
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'test-case-mention-suggestions', theme, composer)
    expect(live.writes).toEqual([])
    assertHermetic(api, errors)
  })
}
