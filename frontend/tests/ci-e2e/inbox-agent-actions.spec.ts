/**
 * W15 AGT-01 §2.1-8, hermetic (`docs/deferred-scope-2026-10-13/work/W15-contract.md` §2.1, §4.1, §4.2), on
 * `/my-failures?tab=approvals&source=actions`:
 *
 *  - a QA lead sees `Agent actions 3`: an approvable quarantine, a reassign whose AI report awaits review (Approve
 *    disabled, its title says why, no PATCH can be sent), a release item; each with its review fact;
 *  - Reject with `unsupported_claim` → the row leaves and the PATCH body is exactly the decision;
 *  - Approve the acknowledge → asked in the row → `Acknowledged by …` under Recently decided once it ran;
 *  - a QA engineer gets no chip and the queue is never requested;
 *  - the request inventory adds exactly the pending read on load and the decided read with the section;
 *  - widths (§4.1, CI's DejaVu fonts): the source chips stay on one line and no row overflows, at 1280 and 1440.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-agent-actions.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectNoOverlay, opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { AGENT_ACTIONS_PATH, agentActionsWorld, DECIDER, decidedActions } from '../visual/production/fixtures-agent-actions'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const SOURCE_URL = '/my-failures?tab=approvals&source=actions'
const PENDING_READ = `GET ${AGENT_ACTIONS_PATH}?view=pending&limit=200`
const DECIDED_READ = `GET ${AGENT_ACTIONS_PATH}?view=decided&limit=20`
const REVIEW_BLOCKED = 'Accept the AI report first: an approved action runs only on an accepted report.'

const ready = (p: Page) => p.locator('[data-page-header] h1')
const chip = (page: Page) => page.locator('[data-chip-option="actions"]')
const section = (page: Page) => page.locator('[data-primary]').getByRole('region', { name: 'Agent actions' })
const rows = (page: Page) => section(page).getByTestId('agent-action-row')
const row = (page: Page, id: string) => section(page).locator(`[data-testid="agent-action-row"][data-agent-action-id="${id}"]`)
const unique = (lines: string[]) => [...new Set(lines)]

test('a QA lead: Agent actions 3, each with its AI-report fact; the inventory adds only the queue reads', async ({ page }) => {
  const world = agentActionsWorld()
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: world.handlers, ready })
  await expect(chip(page)).toHaveAttribute('aria-checked', 'true')
  await expect(chip(page)).toHaveText('Agent actions 3')
  await expect(rows(page)).toHaveCount(3)
  await expect(row(page, 'act-quarantine-1')).toContainText('Quarantine checkout-retry')
  await expect(row(page, 'act-quarantine-1')).toContainText('Investigator · run #128')
  await expect(row(page, 'act-quarantine-1')).toContainText('AI report: accepted')
  await expect(row(page, 'act-reassign-1')).toContainText('AI report: awaiting review')
  await expect(row(page, 'act-reassign-1').getByRole('link', { name: 'AI report: awaiting review' }))
    .toHaveAttribute('href', '/my-failures?tab=approvals&source=reports')
  await expect(row(page, 'act-ack-1')).toContainText('Decision report · run #128')
  await expect(section(page).getByRole('link', { name: 'Open AI agents settings' })).toHaveAttribute('href', '/settings/ai-agents')
  await networkQuiet(page, api)
  expect(unique(requestsTo(api, AGENT_ACTIONS_PATH)).sort()).toEqual([DECIDED_READ, PENDING_READ].sort())
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('a report awaiting review: Approve is disabled with the reason, and no PATCH can be sent', async ({ page }) => {
  const world = agentActionsWorld()
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: world.handlers, ready })
  const approve = row(page, 'act-reassign-1').getByTestId('agent-action-approve')
  await expect(approve).toBeDisabled()
  await expect(approve).toHaveAttribute('title', REVIEW_BLOCKED)
  await approve.click({ force: true })
  await expect(page.getByTestId('agent-action-approve-confirm')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  assertHermetic(api, errors)
})

test('Reject with a reason: in the row, the row leaves, and the PATCH body is exactly the decision', async ({ page }) => {
  const world = agentActionsWorld()
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: world.handlers, ready })
  await row(page, 'act-quarantine-1').getByTestId('agent-action-reject').click()
  const form = opened(page, 'Reject agent action')
  await expect(form).toBeVisible()
  const send = form.getByTestId('agent-action-reject-confirm')
  await expect(send).toBeDisabled()
  await form.getByLabel('Reason').selectOption('unsupported_claim')
  await send.click()
  await expect(form).toHaveCount(0)
  await expect(rows(page)).toHaveCount(2)
  await expect(chip(page)).toHaveText('Agent actions 2')
  await expect(section(page).getByTestId('agent-action-decided-row')).toContainText(`Rejected by ${DECIDER} (unsupported_claim).`)
  await expectNoOverlay(page)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([
    { line: `PATCH ${AGENT_ACTIONS_PATH}/act-quarantine-1`, body: { status: 'rejected', reason_code: 'unsupported_claim' } },
  ])
  assertHermetic(api, errors)
})

test('Approve the release item: asked in the row, then Acknowledged under Recently decided once it ran', async ({ page }) => {
  const world = agentActionsWorld()
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: world.handlers, ready })
  await row(page, 'act-ack-1').getByTestId('agent-action-approve').click()
  const ask = page.getByTestId('agent-action-approve-confirm')
  await expect(ask).toContainText('Approve "Ship behind the payments flag"? It is recorded as acknowledged.')
  expect(world.writes).toEqual([])
  await ask.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(row(page, 'act-ack-1')).toHaveCount(0)
  const decided = section(page).locator('[data-testid="agent-action-decided-row"][data-agent-action-id="act-ack-1"]')
  await expect(decided).toContainText(`Acknowledged by ${DECIDER}.`, { timeout: 15_000 })
  await networkQuiet(page, api)
  expect(world.writes).toEqual([{ line: `PATCH ${AGENT_ACTIONS_PATH}/act-ack-1`, body: { status: 'approved' } }])
  assertHermetic(api, errors)
})

for (const width of [1280, 1440]) {
  test(`widths at ${width}: the source chips on one line, no row or list wider than its box`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const world = agentActionsWorld({ decided: decidedActions() })
    const { api, errors } = await openRollout(page, '/my-failures?tab=approvals', { handlers: world.handlers, ready })
    await expect(chip(page)).toHaveText('Agent actions 3')
    await expect(section(page).getByTestId('agent-action-decided-row')).toHaveCount(4)
    // The Reject form open on one row, as the region shows it.
    await row(page, 'act-reassign-1').getByTestId('agent-action-reject').click()
    await expect(opened(page, 'Reject agent action')).toBeVisible()
    const chipTops = await page.getByRole('radiogroup', { name: 'Approval source' }).locator('[data-chip-option]')
      .evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)))
    expect(new Set(chipTops).size, `the source chips wrap at ${width}: ${chipTops.join(', ')}`).toBe(1)
    const boxes = await section(page).locator('ul, li, [data-in-page="form"]').evaluateAll((els) =>
      els.map((el) => ({ what: `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? `#${el.getAttribute('data-testid')}` : ''}`, scroll: el.scrollWidth, client: el.clientWidth })))
    const lists = boxes.filter((b) => b.what === 'ul')
    console.log(`AGENT-ACTIONS-WIDTH ${width} ${JSON.stringify({ chipTops: [...new Set(chipTops)], lists })}`)
    for (const box of boxes) expect(box.scroll, `${box.what} overflows at ${width}`).toBeLessThanOrEqual(box.client)
    // The slack of the widest chip row against the content box (≥ 40 px planned, §4.1).
    const slack = await page.getByRole('radiogroup', { name: 'Approval source' }).evaluate((el) => {
      const last = el.lastElementChild?.getBoundingClientRect()
      // radiogroup → the ChipFilter → the Approvals tab's column (the content box).
      const box = (el.parentElement?.parentElement ?? el).getBoundingClientRect()
      return last ? Math.round(box.right - last.right) : 0
    })
    console.log(`AGENT-ACTIONS-CHIP-SLACK ${width} ${slack}`)
    expect(slack, `the chip row's slack at ${width}`).toBeGreaterThanOrEqual(40)
    await networkQuiet(page, api)
    assertHermetic(api, errors)
  })
}

test('a QA engineer gets no Agent actions source and never requests the queue', async ({ page }) => {
  const world = agentActionsWorld({ role: 'qa_engineer' })
  const { api, errors } = await openRollout(page, SOURCE_URL, { handlers: world.handlers, ready, user: world.user })
  const sources = page.getByRole('radiogroup', { name: 'Approval source' })
  await expect(sources.locator('[data-chip-option="all"]')).toHaveAttribute('aria-checked', 'true')
  await expect(chip(page)).toHaveCount(0)
  await expect(section(page)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(requestsTo(api, AGENT_ACTIONS_PATH), "the queue is a QA lead's: never asked").toEqual([])
  assertHermetic(api, errors)
})
