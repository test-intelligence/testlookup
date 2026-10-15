/**
 * W04 AIC-07, hermetic (`docs/deferred-scope-2026-10-13/work/W04-contract.md` §2.6, §4.3), on
 * `/settings/feature-flags` as an ADMIN, with the rows migrations 0208/0209 seed on a fresh install:
 *
 *  - the group "Agent stages (deep pipeline)" heads the table, its seven flags in the group's order, the
 *    four read-only stages and `aiq_gap_refinement` ON, `defect_commander` OFF with a MUTATING pill,
 *    `async_decision_report_supersession` OFF; every other flag follows, ungrouped;
 *  - `defect_commander` for one project = switch it ON globally, then list the project under Edit (the
 *    PATCH bodies are asserted);
 *  - a QA lead gets "Admin access required" and the ADMIN-only list is never asked for.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agents-billing-flags.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { ADMIN, flagsHandlers, PROJECT_ID } from '../visual/production/fixtures-agents-billing-flags'
import { USER } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const group = (p: Page) => p.locator('[data-flag-group-body="agent-stages"]')
const flagRow = (p: Page, key: string) => p.locator(`tr[data-flag-key="${key}"]`)
const toggle = (p: Page, key: string) => p.getByRole('button', { name: `Toggle ${key}` })

const STAGES = [
  'contract_validation',
  'log_intelligence',
  'regression_watchman',
  'change_ownership',
  'defect_commander',
  'aiq_gap_refinement',
  'async_decision_report_supersession',
]

test('the agent stage flags are one group at the top: read-only stages ON, defect_commander OFF and marked MUTATING', async ({ page }) => {
  const { handlers } = flagsHandlers()
  const { api, errors } = await openRollout(page, '/settings/feature-flags', { handlers, user: ADMIN, ready: group })
  const header = page.locator('tr[data-flag-group="agent-stages"]')
  await expect(header).toContainText('Agent stages (deep pipeline)')
  await expect(header).toContainText('A stage that is off is skipped and the run says why.')
  // The group first, in its own order; then the rest in the server's order.
  const keys = await page.locator('tbody tr[data-flag-key]').evaluateAll((rows) => rows.map((r) => r.getAttribute('data-flag-key')))
  expect(keys).toEqual([...STAGES, 'ask_ai_chat', 'llm_cost_budget', 'manual_upload'])
  await expect(group(page).locator('tr[data-flag-key]')).toHaveCount(STAGES.length)

  for (const key of ['contract_validation', 'log_intelligence', 'regression_watchman', 'change_ownership', 'aiq_gap_refinement']) {
    await expect(toggle(page, key)).toHaveText('ON')
  }
  await expect(toggle(page, 'defect_commander')).toHaveText('OFF')
  await expect(toggle(page, 'async_decision_report_supersession')).toHaveText('OFF')
  await expect(page.locator('[data-flag-mutating]')).toHaveCount(1)
  await expect(flagRow(page, 'defect_commander').locator('[data-flag-mutating]')).toHaveText('MUTATING')
  // A truncated description keeps its full text in the tooltip.
  await expect(flagRow(page, 'log_intelligence').locator('td').nth(1)).toHaveAttribute('title', /needs Splunk, which stays off while AI_OFFLINE_MODE is on/)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('defect_commander for one project: ON globally, then the project listed under Edit', async ({ page }) => {
  const { handlers, state } = flagsHandlers()
  const { api, errors } = await openRollout(page, '/settings/feature-flags', { handlers, user: ADMIN, ready: group })
  await toggle(page, 'defect_commander').click()
  await expect(toggle(page, 'defect_commander')).toHaveText('ON')

  await flagRow(page, 'defect_commander').getByRole('button', { name: 'Edit' }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit defect_commander' })
  await dialog.getByLabel(/Enabled projects/).fill(JSON.stringify([PROJECT_ID]))
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(flagRow(page, 'defect_commander').locator('td').nth(4)).toHaveText('1p / 0r')
  await networkQuiet(page, api)
  expect(state.writes.map((w) => w.line)).toEqual([
    'PATCH /api/v1/feature-flags/defect_commander',
    'PATCH /api/v1/feature-flags/defect_commander',
  ])
  expect(state.writes[0].body).toEqual({ enabled_global: true })
  expect(state.writes[1].body).toMatchObject({ enabled_projects: [PROJECT_ID], enabled_roles: null })
  assertHermetic(api, errors)
})

test('a QA lead is told the page is admin-only and never asks for the ADMIN-only list', async ({ page }) => {
  const { handlers } = flagsHandlers()
  const { api, errors } = await openRollout(page, '/settings/feature-flags', {
    handlers,
    user: USER,
    ready: (p) => p.getByText('Admin access required'),
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/feature-flags')).toEqual([])
  await expect(page.locator('[data-flag-group]')).toHaveCount(0)
  assertHermetic(api, errors)
})
