/**
 * W15 G-E baselines (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §6.5, four NEW regions), signal, lab and
 * paper (`THEMES_WITH_PAPER`):
 *
 *  - `/my-failures?tab=approvals&source=actions`: `inbox-agent-actions` (three waiting rows — an approvable
 *    quarantine, a reassign blocked by `AI report: awaiting review`, a release item — with the Reject form open on
 *    the reassign) and `inbox-agent-actions-decided` (Recently decided: executed with its `quarantined` link + Undo,
 *    rejected, outcome unknown, undone);
 *  - `/runs/:id?tab=evidence`: `run-decision-action-chips` (the Decision panel's Proposed actions with their Waiting /
 *    Acknowledged / Rejected chips and the Review in Inbox link);
 *  - `/settings/ai-agents`: `settings-ai-agents-act` (the Investigator card in Act with one action type ticked).
 *
 * No existing region changes (the hermetic inbox and run worlds answer the new reads EMPTY by default). Each region
 * is the container itself, with focus blurred. Hermetic worlds: `production/fixtures-agent-actions.ts`. PNGs are
 * rendered by CI only (Linux); nobody regenerates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { openRollout } from '../lib/rollout'
import { RUN_ID } from './production/fixtures'
import {
  agentActionsWorld,
  aiAgentsWorld,
  decidedActions,
  runDecisionWorld,
} from './production/fixtures-agent-actions'

test.use(PINNED)

const inboxReady = (p: Page) => p.locator('[data-page-header] h1')
const section = (p: Page) => p.locator('[data-primary]').getByRole('region', { name: 'Agent actions' })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

for (const theme of THEMES_WITH_PAPER) {
  test(`the waiting agent actions, one Reject form open — ${theme}`, async ({ page }) => {
    const world = agentActionsWorld()
    const { api, errors } = await openRollout(page, '/my-failures?tab=approvals&source=actions', { theme, handlers: world.handlers, ready: inboxReady })
    await expect(section(page).getByTestId('agent-action-row')).toHaveCount(3)
    await section(page).locator('[data-agent-action-id="act-reassign-1"]').getByTestId('agent-action-reject').click()
    await expect(section(page).getByRole('region', { name: 'Reject agent action' })).toBeVisible()
    await blur(page)
    await visualRegion(page, 'inbox-agent-actions', theme, section(page))
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`recently decided agent actions — ${theme}`, async ({ page }) => {
    const world = agentActionsWorld({ pending: [], decided: decidedActions() })
    const { api, errors } = await openRollout(page, '/my-failures?tab=approvals&source=actions', { theme, handlers: world.handlers, ready: inboxReady })
    const decided = section(page).locator('[data-agent-actions-decided]')
    await expect(decided.getByTestId('agent-action-decided-row')).toHaveCount(4)
    await expect(decided.getByTestId('agent-action-undo')).toHaveCount(1)
    await blur(page)
    await visualRegion(page, 'inbox-agent-actions-decided', theme, decided)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`the Decision panel's proposed actions with their ledger chips — ${theme}`, async ({ page }) => {
    const world = runDecisionWorld()
    const { api, errors } = await openRollout(page, `/runs/${RUN_ID}?tab=evidence`, {
      theme,
      handlers: world.handlers,
      ready: (p) => p.getByRole('heading', { name: 'Decision intelligence' }),
    })
    const proposed = page.locator('[data-proposed-actions]')
    await expect(proposed.locator('[data-proposed-action-status]')).toHaveCount(3)
    await expect(proposed.getByRole('link', { name: 'Review in Inbox' })).toHaveCount(1)
    await blur(page)
    await visualRegion(page, 'run-decision-action-chips', theme, proposed)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })

  test(`the Investigator card in Act with one action type ticked — ${theme}`, async ({ page }) => {
    const world = aiAgentsWorld({ mode: 'act', ticks: ['quarantine.apply'] })
    const card = (p: Page) => p.getByTestId('agent-policy-investigator')
    const { api, errors } = await openRollout(page, '/settings/ai-agents', { theme, handlers: world.handlers, ready: card })
    await expect(card(page).getByRole('checkbox', { name: 'Quarantine flaky offenders' })).toBeChecked()
    await blur(page)
    await visualRegion(page, 'settings-ai-agents-act', theme, card(page))
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })
}
