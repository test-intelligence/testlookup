/**
 * W04 AIC-01, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.2, §4.5): the deep
 * investigation triggers follow `GET /settings/ai/mode` (the toggle and the mode, every role may read it).
 *
 *  - Deep Investigation switched OFF: `/deep-investigate` shows "Deep investigation is turned off in
 *    Settings › AI." in place of "Run Deep Analysis" and of the verdict card's "Run on all N failures"; the
 *    run's Analysis tab shows it in place of "Analyze failures"; the run's ⋯ menu has no "Deep
 *    investigation" (the standard "Trigger pipeline" stays);
 *  - switched back ON: all three are offered again;
 *  - Rules mode: the same three, with "Deep investigation needs LLM or Auto mode (current mode: Rules)."
 *
 * The toggle itself is G-D's spec (`settings-ai-toggles.spec.ts`); here the mode answer is switched between
 * loads, the way a save of Settings › AI changes it.
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agents-billing-flags.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { deepHandlers, RUN_ID } from '../visual/production/fixtures-agents-billing-flags'
import { RUN_PAGE } from '../visual/production/fixtures-run'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const OFF_NOTE = 'Deep investigation is turned off in Settings › AI.'
const RULES_NOTE = 'Deep investigation needs LLM or Auto mode (current mode: Rules).'
const verdict = (p: Page) => p.getByRole('region', { name: 'Investigation verdict' })
const deepRegion = (p: Page) => p.getByRole('region', { name: 'Deep investigation' })
const DEEP_PAGE = `/deep-investigate?run=${RUN_ID}`
const ANALYSIS = `/runs/${RUN_ID}?tab=analysis`

async function menuItems(page: Page): Promise<string[]> {
  await page.getByRole('button', { name: 'More actions' }).click()
  const items = (await page.getByRole('menuitem').allTextContents()).map((t) => t.trim())
  await page.keyboard.press('Escape')
  return items
}

async function expectOffered(page: Page, offered: boolean, note: string) {
  await page.goto(DEEP_PAGE)
  await expect(verdict(page)).toBeVisible()
  if (offered) {
    await expect(page.getByRole('button', { name: 'Run Deep Analysis' })).toBeVisible()
    await expect(verdict(page).getByRole('button', { name: /Run on all/ })).toBeVisible()
    await expect(page.getByTestId('deep-disabled-note')).toHaveCount(0)
  } else {
    await expect(page.getByRole('button', { name: 'Run Deep Analysis' })).toHaveCount(0)
    await expect(verdict(page).getByRole('button', { name: /Run on all/ })).toHaveCount(0)
    await expect(page.getByTestId('deep-disabled-note')).toHaveText([note, note])
    await expect(page.locator('[data-page-toolbar] [data-testid="deep-disabled-note"]')).toHaveCount(1)
  }

  await page.goto(ANALYSIS)
  await expect(deepRegion(page)).toBeVisible()
  if (offered) {
    await expect(deepRegion(page).getByRole('button', { name: /Analyze failures/ })).toBeVisible()
    await expect(deepRegion(page).getByTestId('deep-disabled-note')).toHaveCount(0)
  } else {
    await expect(deepRegion(page).getByRole('button', { name: /Analyze failures/ })).toHaveCount(0)
    await expect(deepRegion(page).getByTestId('deep-disabled-note')).toHaveText(note)
  }
  const items = await menuItems(page)
  expect(items).toContain('Trigger pipeline')
  if (offered) expect(items).toContain('Deep investigation')
  else expect(items).not.toContain('Deep investigation')
}

test('switched off: the three deep triggers give way to the note; switched back on: they return', async ({ page }) => {
  const { handlers, state } = deepHandlers({ deepEnabled: false })
  const { api, errors } = await openRollout(page, DEEP_PAGE, { handlers: [...handlers, ...RUN_PAGE], ready: verdict })
  await expectOffered(page, false, OFF_NOTE)

  state.deepEnabled = true
  await expectOffered(page, true, OFF_NOTE)
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/settings/ai/mode').length).toBeGreaterThan(0)
  // Nothing was triggered by any of it.
  expect(state.writes).toEqual([])
  assertHermetic(api, errors)
})

test('Rules mode: the same three give way to "needs LLM or Auto mode"', async ({ page }) => {
  const { handlers, state } = deepHandlers({ mode: 'rules' })
  const { api, errors } = await openRollout(page, DEEP_PAGE, { handlers: [...handlers, ...RUN_PAGE], ready: verdict })
  await expectOffered(page, false, RULES_NOTE)
  await networkQuiet(page, api)
  expect(state.writes).toEqual([])
  assertHermetic(api, errors)
})
