/**
 * W09 FLK-01 (contract §2.5), hermetic, on `/flaky` with `flaky_auto_quarantine` ON (migration 0219's default
 * for an untouched install): the quarantine workflow end to end, and no Experimental badge anywhere.
 *
 *  - as a QA lead: Detected → **Propose quarantine** on `fp-04` → a reason → the POST body (the existing
 *    Propose quarantine modal, unchanged); Proposed → **Approve** (the existing in-row form) → the approve POST;
 *    Quarantined lists it with `SLA due 2 Oct` (14 days after the approval) → **Release** → the release POST,
 *    and it moves to History;
 *  - no `Experimental` on any of the five tabs;
 *  - a QA engineer sees no Propose, no Approve, no Release (proposing and deciding are QA_LEAD+, §0.22);
 *  - with the flag off, the Detected tab's one line, rewritten (the `[data-quarantine-off]` prefix kept).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `flakyQuarantineWorld()`
 * (`fixtures-notify-flaky.ts`) over `flakyHandlers` (`fixtures-pages.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { flakyQuarantineWorld, QA_ENGINEER, QUARANTINE_IDS } from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const QUARANTINE_ON = { flaky_auto_quarantine: true }
const TABS = ['Detected', 'Proposed', 'Quarantined', 'History', 'Policy'] as const
const primary = (page: Page) => page.locator('[data-primary]')
const detected = (page: Page) => primary(page).getByRole('table', { name: 'Flaky tests' })
const tab = (page: Page, name: (typeof TABS)[number]) => page.getByRole('tab', { name: new RegExp(`^${name}`) })

/** The id `flakyQuarantineWorld` gives its first new proposal. */
const NEW_PROPOSAL_ID = '00000000-0000-4000-8000-000000001101'

const OFF_LINE =
  'Quarantine is off for this project, so no test can be proposed for it. Its flag, flaky_auto_quarantine in Settings › Feature flags, is switched off or limited to other projects.'

async function expectNoExperimentalBadge(page: Page, where: string) {
  await expect(page.getByText('Experimental', { exact: true }), `${where}: no Experimental badge`).toHaveCount(0)
  await expect(page.locator('[title^="This feature is experimental"]'), `${where}: no Experimental badge`).toHaveCount(0)
}

test('a QA lead proposes fp-04, approves it in the row, sees its SLA due date, and releases it', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky', { handlers: world.handlers, ready, flags: QUARANTINE_ON })
  await expect(page.locator('[data-quarantine-off]')).toHaveCount(0)

  // Detected → Propose quarantine (the existing modal, unchanged by W09).
  const row = detected(page).locator('tr[data-flaky-row="fp-04"]')
  await row.getByRole('button', { name: 'Propose quarantine' }).click()
  const dialog = opened(page, 'Propose quarantine')
  await dialog.getByRole('textbox').fill('Flips on CI only')
  await dialog.getByRole('button', { name: 'Propose quarantine' }).click()
  await expect(dialog).toHaveCount(0)
  expect(world.writes).toEqual([
    {
      line: 'POST /api/v1/quarantine',
      body: expect.objectContaining({ project_id: P, test_fingerprint: 'fp-04', test_name: 'test_checkout_flow_4', detection_method: 'manual' }),
    },
  ])
  await expect(row.getByRole('button', { name: /Proposed/ })).toBeVisible()
  await expect(tab(page, 'Proposed')).toHaveText('Proposed2')

  // Proposed → Approve: the existing inline form under the row.
  await tab(page, 'Proposed').click()
  await expect(page).toHaveURL(/\/flaky\?tab=proposed$/)
  const proposal = primary(page).locator(`[data-quarantine-id="${NEW_PROPOSAL_ID}"]`)
  await expect(proposal).toContainText('test_checkout_flow_4')
  await proposal.getByTestId('quarantine-approve').click()
  const form = primary(page).getByTestId('quarantine-decision-form')
  await form.getByTestId('quarantine-notes').fill('Agreed: a timing flake')
  await form.getByTestId('quarantine-confirm').click()
  await expect(page.getByText('Quarantine approved')).toBeVisible()
  expect(world.writes.at(-1)).toEqual({ line: `POST /api/v1/quarantine/${NEW_PROPOSAL_ID}/approve`, body: { notes: 'Agreed: a timing flake' } })
  await expect(proposal).toHaveCount(0)

  // Quarantined: the test, with the day its SLA is due (14 days after the approval: 2 Oct).
  await tab(page, 'Quarantined').click()
  await expect(page).toHaveURL(/\/flaky\?tab=quarantined$/)
  const quarantined = primary(page).locator(`[data-quarantine-id="${NEW_PROPOSAL_ID}"]`)
  await expect(quarantined).toContainText('test_checkout_flow_4')
  await expect(quarantined).toContainText('SLA due 2 Oct')
  // A row past its SLA keeps the existing stale flag instead.
  const stale = primary(page).locator(`[data-quarantine-id="${QUARANTINE_IDS.stale}"]`)
  await expect(stale).toContainText('stale — 6d over SLA')
  await expect(stale).not.toContainText('SLA due')

  // Release, in the row.
  await quarantined.getByTestId('quarantine-release').click()
  await primary(page).getByTestId('quarantine-decision-form').getByTestId('quarantine-confirm').click()
  await expect(page.getByText('Quarantine released')).toBeVisible()
  expect(world.writes.at(-1)).toEqual({ line: `POST /api/v1/quarantine/${NEW_PROPOSAL_ID}/release`, body: {} })
  await expect(quarantined).toHaveCount(0)
  await tab(page, 'History').click()
  await expect(primary(page).locator(`[data-quarantine-id="${NEW_PROPOSAL_ID}"]`)).toContainText('test_checkout_flow_4')

  await networkQuiet(page, api)
  expect(world.writes.map((w) => w.line)).toEqual([
    'POST /api/v1/quarantine',
    `POST /api/v1/quarantine/${NEW_PROPOSAL_ID}/approve`,
    `POST /api/v1/quarantine/${NEW_PROPOSAL_ID}/release`,
  ])
  assertHermetic(api, errors)
})

test('no Experimental badge on any of the five tabs', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky', { handlers: world.handlers, ready, flags: QUARANTINE_ON })
  await expect(detected(page)).toBeVisible()
  for (const name of TABS) {
    await tab(page, name).click()
    await expect(tab(page, name)).toHaveAttribute('aria-selected', 'true')
    await networkQuiet(page, api)
    await expectNoExperimentalBadge(page, name)
  }
  assertHermetic(api, errors)
})

test('a QA engineer: no Propose, no Approve, no Release', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky', { handlers: world.handlers, ready, flags: QUARANTINE_ON, user: QA_ENGINEER })
  await expect(detected(page).locator('tr[data-flaky-row="fp-04"]')).toBeVisible()
  await expect(detected(page).getByRole('button', { name: 'Propose quarantine' })).toHaveCount(0)
  await expect(page.locator('[data-quarantine-off]')).toHaveCount(0)

  await tab(page, 'Proposed').click()
  await expect(primary(page).locator(`[data-quarantine-id="${QUARANTINE_IDS.liveProposal}"]`)).toBeVisible()
  await expect(primary(page).getByTestId('quarantine-approve')).toHaveCount(0)
  await expect(primary(page).getByTestId('quarantine-reject')).toHaveCount(0)

  await tab(page, 'Quarantined').click()
  await expect(primary(page).locator(`[data-quarantine-id="${QUARANTINE_IDS.quarantined}"]`)).toBeVisible()
  await expect(primary(page).getByTestId('quarantine-release')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes, 'a QA engineer wrote nothing').toEqual([])
  assertHermetic(api, errors)
})

test('with the flag off: no proposal is offered, and the one line says why and where', async ({ page }) => {
  const world = flakyQuarantineWorld()
  const { api, errors } = await openRollout(page, '/flaky', { handlers: world.handlers, ready })
  await expect(detected(page)).toBeVisible()
  await expect(detected(page).getByRole('button', { name: 'Propose quarantine' })).toHaveCount(0)
  const off = page.locator('[data-quarantine-off]')
  await expect(off).toHaveText(OFF_LINE)
  await expect(off.locator('code')).toHaveText('flaky_auto_quarantine')
  await expectNoExperimentalBadge(page, 'Detected, flag off')
  await networkQuiet(page, api)
  // Detected never reads the quarantine policy (the Quarantined and Policy tabs do).
  expect(requestsTo(api, `/api/v1/projects/${P}/quarantine/policy`)).toEqual([])
  assertHermetic(api, errors)
})
