/**
 * W15 AGT-03 §2.3-11 (owner decision D16), hermetic (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §2.3,
 * §4.1, §4.2):
 *
 *  - `/settings/ai-agents`: Act is enabled for the Investigator; choosing it shows the action types (nothing renders
 *    in Shadow); tick "Quarantine flaky offenders" → Save → the policy PUT, then the ticks' PUT carrying
 *    `extensions.investigator.act.allowed_action_types: ['quarantine.apply']` and the `If-Match` of the document it
 *    re-read; the fieldset fits the settings column at 1280 and 1440;
 *  - Inbox › Approvals › Agent actions: Approve the quarantine → it runs on the next poll → `Executed · approved by …`
 *    with its `quarantined` link → Undo (asked in the row) → `Undone by …`;
 *  - a row the server answers `approvable: false` (its report awaits review): Approve disabled with that sentence as
 *    its title, and no PATCH can be sent.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agent-actions.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import {
  AGENT_ACTIONS_PATH,
  agentActionsWorld,
  aiAgentsWorld,
  DECIDER,
  INVESTIGATOR_CONFIG_PATH,
  pendingActions,
} from '../visual/production/fixtures-agent-actions'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const card = (p: Page) => p.getByTestId('agent-policy-investigator')
const fieldset = (p: Page) => p.getByRole('group', { name: 'Action types Act may run' })
const inboxReady = (p: Page) => p.locator('[data-page-header] h1')
const section = (page: Page) => page.locator('[data-primary]').getByRole('region', { name: 'Agent actions' })
const decidedRow = (page: Page, id: string) =>
  section(page).locator(`[data-testid="agent-action-decided-row"][data-agent-action-id="${id}"]`)

test('Act → tick Quarantine flaky offenders → Save: the policy PUT, then the ticks with the If-Match it re-read', async ({ page }) => {
  const world = aiAgentsWorld({ mode: 'shadow' })
  const { api, errors } = await openRollout(page, '/settings/ai-agents', { handlers: world.handlers, ready: card })
  const act = card(page).getByRole('radio', { name: 'Act' })
  await expect(act).toBeEnabled()
  await expect(act).not.toHaveAttribute('title', /.+/)
  await expect(fieldset(page)).toHaveCount(0)
  await act.click()
  await expect(fieldset(page)).toBeVisible()
  await expect(fieldset(page)).toContainText('Act runs nothing until at least one action type is ticked.')
  await fieldset(page).getByRole('checkbox', { name: 'Quarantine flaky offenders' }).check()
  await expect(fieldset(page)).not.toContainText('Act runs nothing until')
  await card(page).getByRole('button', { name: 'Save policy' }).click()
  await expect(page.getByText('Investigator policy saved')).toBeVisible()
  await networkQuiet(page, api)
  expect(world.writes.map((w) => [w.line, w.ifMatch, w.body?.mode])).toEqual([
    [`PUT ${INVESTIGATOR_CONFIG_PATH}`, '"3"', 'act'],
    [`PUT ${INVESTIGATOR_CONFIG_PATH}`, '"4"', 'act'],
  ])
  expect(world.writes[0].body?.extensions?.investigator?.act?.allowed_action_types).toEqual([])
  expect(world.writes[1].body?.extensions?.investigator?.act?.allowed_action_types).toEqual(['quarantine.apply'])
  // The second PUT re-read the document first: a GET between the two writes.
  const lines = requestsTo(api, INVESTIGATOR_CONFIG_PATH)
  const puts = lines.map((l, i) => (l.startsWith('PUT') ? i : -1)).filter((i) => i >= 0)
  expect(lines.slice(puts[0] + 1, puts[1])).toContain(`GET ${INVESTIGATOR_CONFIG_PATH}`)
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test(`the Act fieldset fits the settings column at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const world = aiAgentsWorld({ mode: 'act', ticks: ['quarantine.apply'] })
    const { api, errors } = await openRollout(page, '/settings/ai-agents', { handlers: world.handlers, ready: card })
    await expect(fieldset(page)).toBeVisible()
    await expect(fieldset(page).getByRole('checkbox', { name: 'Quarantine flaky offenders' })).toBeChecked()
    const fit = await fieldset(page).evaluate((el) => {
      const box = el.getBoundingClientRect()
      const card = el.closest('[data-testid="agent-policy-investigator"]')?.getBoundingClientRect() ?? box
      // The TEXT's extent (a label row and a note are full-width blocks; their words are not).
      const textRight = (n: Element) => {
        const range = document.createRange()
        range.selectNodeContents(n)
        return range.getBoundingClientRect().right
      }
      const widest = Math.max(...[...el.querySelectorAll('label, p')].map(textRight))
      return { scroll: el.scrollWidth, client: el.clientWidth, card: Math.round(card.width), fieldset: Math.round(box.width), slack: Math.round(box.right - widest) }
    })
    console.log(`AGENT-ACT-FIELDSET ${width} ${JSON.stringify(fit)}`)
    expect(fit.scroll, `the fieldset overflows at ${width}`).toBeLessThanOrEqual(fit.client)
    expect(fit.slack, `the fieldset's slack at ${width}`).toBeGreaterThanOrEqual(40)
    await networkQuiet(page, api)
    expect(world.writes).toEqual([])
    assertHermetic(api, errors)
  })
}

test('Approve a quarantine → executing → Executed with its quarantined link → Undo → Undone', async ({ page }) => {
  const world = agentActionsWorld()
  const { api, errors } = await openRollout(page, '/my-failures?tab=approvals&source=actions', { handlers: world.handlers, ready: inboxReady })
  await section(page).locator('[data-agent-action-id="act-quarantine-1"]').getByTestId('agent-action-approve').click()
  const ask = page.getByTestId('agent-action-approve-confirm')
  await expect(ask).toContainText('Approve "Quarantine checkout-retry"? It runs within a minute and can be undone.')
  await ask.getByRole('button', { name: 'Approve', exact: true }).click()
  const done = decidedRow(page, 'act-quarantine-1')
  await expect(done).toContainText('Running…')
  await expect(done).toContainText(`Executed · approved by ${DECIDER} · quarantined.`, { timeout: 15_000 })
  await expect(done.getByRole('link', { name: 'quarantined' })).toHaveAttribute('href', '/flaky?tab=quarantined')
  await done.getByTestId('agent-action-undo').click()
  const undo = page.getByTestId('agent-action-undo-confirm')
  await expect(undo).toContainText('Undo "Quarantine checkout-retry"? The quarantine is released.')
  await undo.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(done).toContainText(`Undone by ${DECIDER}.`)
  await expect(done.getByTestId('agent-action-undo')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PATCH ${AGENT_ACTIONS_PATH}/act-quarantine-1`, body: { status: 'approved' } },
    { line: `PATCH ${AGENT_ACTIONS_PATH}/act-quarantine-1`, body: { status: 'rolled_back' } },
  ])
  assertHermetic(api, errors)
})

test('a row answered approvable: false shows why on its disabled Approve, and no PATCH can be sent', async ({ page }) => {
  const blocked = pendingActions().filter((r) => r.id === 'act-reassign-1')
  const world = agentActionsWorld({ pending: blocked })
  const { api, errors } = await openRollout(page, '/my-failures?tab=approvals&source=actions', { handlers: world.handlers, ready: inboxReady })
  const approve = section(page).getByTestId('agent-action-approve')
  await expect(approve).toBeDisabled()
  await expect(approve).toHaveAttribute('title', 'Accept the AI report first: an approved action runs only on an accepted report.')
  await approve.click({ force: true })
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})
