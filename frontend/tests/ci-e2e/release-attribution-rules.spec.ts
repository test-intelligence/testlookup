/**
 * W11 REL-09 on `/releases/attribution-rules`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.4,
 * §4.1, §4.2), at 1280 and 1440:
 *
 *  - as QA LEAD `/releases` **⋯ → Attribution rules** opens the page (one project selected): its load reads the
 *    rules and the fields route only (+ the shell's cached release list); the ladder is explained in the server's
 *    order; the rules are listed in evaluation order (`Order` 1..n: Hotfix branches, Staging soak, Nightly builds)
 *    with `Matches` (`Branch hotfix/*`) and the target release (`(created on first match)` for one no release has);
 *  - **New rule** opens the `New attribution rule` form IN the page; `branch release/2.5* → 2.5` + **Preview** sends
 *    ONE `POST …/preview?detail=true` with the form's body and prints `Matches 12 of the last 200 runs (183 carry a
 *    branch).`, the shadowed / explicit sentences, `No release named "2.5" yet: …`, the sample table (`here` /
 *    `earlier rule` / `stays (named)`) and the footer's Unattributed count; a preview saves nothing; **Save** POSTs
 *    exactly the rule and the new row is listed;
 *  - **Delete** asks in the page (an inline strip naming the rule) and sends the DELETE only on confirm;
 *  - as QA ENGINEER the page is read-only: the explanation and the table, no New rule / Edit / Delete / toggle /
 *    Preview, and nothing is written;
 *  - in All Projects (an admin) the page asks for one project and reads no rule;
 *  - `#main-content` never scrolls sideways (the sample table scrolls in its own box).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { answerConfirm, expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { clippedTables, expectMainFits } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { openProductionPage } from '../lib/production-pages'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  attributionRulesWorld,
  NEW_RULE,
  PREVIEW_SENTENCES,
  QA_ENGINEER,
  QA_LEAD,
  RULE_IDS,
  RULES_PATH,
} from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ROUTE = '/releases/attribution-rules'
const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Attribution rules', exact: true })
const releasesReady = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const rulesTable = (page: Page): Locator => page.locator('[data-primary]').getByRole('table').first()
const ruleRow = (page: Page, name: string): Locator => rulesTable(page).getByRole('row').filter({ hasText: name })
const LADDER = [
  'The release the run names',
  'A GitHub milestone on its pull request',
  'These rules, top to bottom',
  'A release whose cutoff window holds the run',
  'The release that was active when it ran',
] as const

/** The rules table's rows (header excluded), each row's first cells as text. */
async function tableRows(page: Page): Promise<string[][]> {
  return rulesTable(page).locator('tbody tr').evaluateAll((rows) =>
    rows.map((tr) => Array.from(tr.querySelectorAll('td')).slice(0, 4).map((td) => (td.textContent ?? '').replace(/\s+/g, ' ').trim())),
  )
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`as QA LEAD: ⋯ → Attribution rules, New rule, Preview (detail), Save — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = attributionRulesWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready: releasesReady, user: QA_LEAD })
      await networkQuiet(page, api)
      await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
      await page.getByRole('menuitem', { name: 'Attribution rules' }).click()
      await expect(page).toHaveURL(new URL(ROUTE, page.url()).href)
      await expect(h1(page)).toBeVisible()
      await networkQuiet(page, api)
      expect(requestsTo(api, RULES_PATH), 'the rules, once').toEqual([`GET ${RULES_PATH}`])
      expect(requestsTo(api, `${RULES_PATH}/fields`), 'the fields route, once').toEqual([`GET ${RULES_PATH}/fields`])
      expect(requestsTo(api, `${RULES_PATH}/preview`), 'no preview on load').toEqual([])

      // The ladder, in the order the server applies it.
      const text = (await page.getByRole('region', { name: 'How a run gets its release' }).textContent()) ?? ''
      const at = LADDER.map((step) => text.indexOf(step))
      expect(at.every((i) => i >= 0), `the explanation names every rung: ${JSON.stringify(at)}`).toBe(true)
      expect([...at].sort((a, b) => a - b)).toEqual(at)

      // Evaluation order: priority, then creation.
      const rows = await tableRows(page)
      expect(rows.map((r) => r[0])).toEqual(['1', '2', '3'])
      expect(rows.map((r) => r[1])).toEqual(['Hotfix branches', 'Staging soak', 'Nightly builds'])
      expect(rows[0][2]).toBe('Branch hotfix/*')
      expect(rows[0][3]).toContain('2026.11.1')
      expect(rows[0][3], 'no release of the project has that name').toContain('(created on first match)')
      expect(rows[1][3]).toBe('2026.11')
      expect(await clippedTables(page.locator('[data-primary]'))).toEqual([])
      await expectMainFits(page, `rules page @${width}`)

      await page.getByRole('button', { name: 'New rule', exact: true }).click()
      const form = page.getByRole('region', { name: 'New attribution rule', exact: true })
      await expect(form).toBeVisible()
      await expectNoOverlay(page)
      await form.getByLabel('Name', { exact: true }).fill(NEW_RULE.name)
      await form.getByLabel('Field', { exact: true }).selectOption(NEW_RULE.match_field)
      await expect(form.getByText('The run\'s git branch. Wildcards: * and ?, e.g. release/2.5*.', { exact: true })).toBeVisible()
      await form.getByLabel('Pattern', { exact: true }).fill(NEW_RULE.match_pattern)
      await form.getByLabel('Release', { exact: true }).fill(NEW_RULE.target_release_name)
      await form.getByLabel('Priority', { exact: true }).fill(String(NEW_RULE.priority))

      await form.getByRole('button', { name: 'Preview', exact: true }).click()
      await expect(page.getByText(PREVIEW_SENTENCES.matches, { exact: true })).toBeVisible()
      await expect(page.getByText(PREVIEW_SENTENCES.shadowed, { exact: true })).toBeVisible()
      await expect(page.getByText(PREVIEW_SENTENCES.explicit, { exact: true })).toBeVisible()
      await expect(page.getByText(PREVIEW_SENTENCES.missingTarget, { exact: true })).toBeVisible()
      const sample = form.getByRole('table', { name: 'Matched runs' })
      await expect(sample.locator('tbody tr')).toHaveCount(10)
      for (const word of ['here', 'earlier rule', 'stays (named)']) await expect(sample.getByText(word, { exact: true }).first()).toBeVisible()
      const footer = page.locator('[data-attribution-footer]')
      await expect(footer).toContainText(PREVIEW_SENTENCES.footer)
      // The Unattributed runs: the Runs list with the top bar's Unattributed bucket (`?release=unattributed`).
      await expect(footer.getByRole('link', { name: 'Show them in Runs', exact: true })).toHaveAttribute('href', '/runs?release=unattributed')
      expect(world.previews).toEqual([{ target: `${RULES_PATH}/preview?detail=true`, body: { ...NEW_RULE } }])
      expect(world.writes, 'a preview saves nothing').toEqual([])
      expect(await clippedTables(page.locator('[data-primary]')), 'the sample table scrolls in its own box').toEqual([])
      await expectMainFits(page, `preview @${width}`)

      await form.getByRole('button', { name: 'Save', exact: true }).click()
      await expect(page.getByRole('status').filter({ hasText: 'Rule created.' })).toBeVisible()
      await expect(form).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: `POST ${RULES_PATH}`, body: { ...NEW_RULE } }])
      await expect(ruleRow(page, NEW_RULE.name)).toHaveCount(1)
      expect((await tableRows(page)).map((r) => r[1])).toEqual(['Hotfix branches', 'Staging soak', 'Nightly builds', NEW_RULE.name])
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })

    test(`Delete asks in the page and sends the DELETE only on confirm — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = attributionRulesWorld()
      const { api, errors } = await openRollout(page, ROUTE, { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await networkQuiet(page, api)
      const del = ruleRow(page, 'Hotfix branches').getByRole('button', { name: /^Delete/ })
      const dismissed = await answerConfirm(page, del, 'dismiss')
      expect(dismissed).toEqual({ kind: 'inline', message: 'Delete rule "Hotfix branches"? Runs it already matched stay where they are.' })
      expect(world.writes).toEqual([])
      await answerConfirm(page, del, 'accept')
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: `DELETE ${RULES_PATH}/${RULE_IDS.hotfix}`, body: null }])
      await expect(ruleRow(page, 'Hotfix branches')).toHaveCount(0)
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })
  })
}

test('as QA ENGINEER: read-only — the explanation and the table, no New rule, Edit, Delete, toggle or Preview', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const world = attributionRulesWorld()
  const { api, errors } = await openRollout(page, ROUTE, { handlers: world.handlers, ready: h1, user: QA_ENGINEER })
  await networkQuiet(page, api)
  expect((await tableRows(page)).map((r) => r[1])).toEqual(['Hotfix branches', 'Staging soak', 'Nightly builds'])
  for (const name of ['New rule', 'Preview', 'Save']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0)
  await expect(rulesTable(page).getByRole('button', { name: /^(Edit|Delete)/ })).toHaveCount(0)
  await expect(rulesTable(page).getByRole('switch')).toHaveCount(0)
  await expect(rulesTable(page).getByRole('checkbox')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(requestsTo(api, `${RULES_PATH}/preview`)).toEqual([])
  assertHermetic(api, errors)
})

test('in All Projects the page asks for one project and reads no rule', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const world = attributionRulesWorld()
  const { api, errors } = await openProductionPage(page, ROUTE, {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    // The store's All Projects sentinel (`ALL_PROJECTS_ID`).
    projectId: 'all',
    handlers: world.handlers,
    ready: (p) => p.getByText('Select a project', { exact: true }),
  })
  await expect(page.getByLabel('Choose a project to continue')).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/attribution-rules(\/|$)/), 'no rule is read for no project').toEqual([])
  expect(requestsTo(api, RULES_PATH.replace(PROJECT_ID, 'all'))).toEqual([])
  assertHermetic(api, errors)
})
