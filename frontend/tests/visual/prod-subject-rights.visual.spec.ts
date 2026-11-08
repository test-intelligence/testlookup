/**
 * W31 STRAT-12 (`docs/deferred-scope-2026-10-13/work/W31-contract.md` §4.1, §6.3 G-D, §6.5): three NEW regions, every
 * theme (`THEMES_WITH_PAPER`), each a state the product produces together:
 *
 *  - `profile-my-data` — Settings › Profile's "Your data" card with a finished export of my own data: the body,
 *    "Prepare a new copy" + Download (W32), "Ready — 1.4 MB, signed. Available until Sep 21, 11:59." and (W38) the
 *    erasure request block with nothing asked yet ("Ask to have your account erased");
 *  - `users-personal-data` — a Users row's Personal data section as an ADMIN opens it for a person with no export yet:
 *    the Export block ("No export yet."), and the Erase panel's preview — 6 changes, 4 kept (one "not counted (took
 *    too long)"), 2 open items — with the username typed into the confirm, so "Erase permanently" is enabled;
 *  - `users-erased-row` — an erased person's row in the table: "Erased user" over the pseudonym, the "Erased" badge,
 *    the role as text and the Personal data action alone.
 *
 * W38 (W31 F-1 / F-2) changes `profile-my-data` (the erasure line became the request block) and adds three regions:
 * `profile-erasure-pending` (the card with a pending request: the line + "Withdraw request"),
 * `users-erasure-requests` (the queue: filter + a pending row with Review / Decline + the admin's own row with its
 * note) and `sso-scim-delete-erase` (SSO › SCIM Tokens' delete setting with erase chosen, the warning and the
 * acknowledgement ticked, Save enabled).
 *
 * No other existing region changes (`users-row-edit` shoots only the Edit section). World: `subjectRightsWorld({...})` in
 * `production/fixtures-subject-rights.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux) and READ
 * before merge; nobody generates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { USER } from './production/fixtures'
import { ssoInPageWorld } from './production/fixtures-in-page-b'
import { ADMIN } from './production/fixtures-releases'
import {
  ADMIN_ROW,
  ERASED,
  erasureRequestRow,
  exportJob,
  myErasureRequest,
  OWN_REQUEST_ID,
  SUBJECT,
  subjectRightsWorld,
} from './production/fixtures-subject-rights'

test.use(PINNED)

const ME = { ...USER, id: SUBJECT.id, username: SUBJECT.username, email: SUBJECT.email, full_name: SUBJECT.full_name, role: 'TESTER' }
const myData = (p: Page) => p.locator('[data-my-data]')
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())

for (const theme of THEMES_WITH_PAPER) {
  test(`"Your data" with a finished export — ${theme}`, async ({ page }) => {
    const world = subjectRightsWorld({ myExports: [exportJob('succeeded')] })
    const { api, errors } = await openRollout(page, '/settings/profile', { theme, handlers: world.handlers, ready: myData, user: ME })
    await expect(myData(page).getByText('Ready — 1.4 MB, signed. Available until Sep 21, 11:59.', { exact: true })).toBeVisible()
    await expect(myData(page).getByRole('button', { name: 'Download', exact: true })).toBeEnabled()
    // W38: the erasure block has answered (nothing asked yet) before the shot.
    await expect(myData(page).locator('[data-my-erasure]')).toHaveAttribute('data-erasure-request-state', 'none')
    await networkQuiet(page, api)
    await visualRegion(page, 'profile-my-data', theme, myData(page))
    assertHermetic(api, errors)
  })

  test(`a person's Personal data with the erasure preview and the confirm typed — ${theme}`, async ({ page }) => {
    const world = subjectRightsWorld()
    const { api, errors } = await openRollout(page, '/users', { theme, handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
    await page.getByRole('button', { name: `Personal data for ${SUBJECT.username}`, exact: true }).click()
    const section = page.locator(`[data-personal-data="${SUBJECT.id}"]`)
    await expect(section.getByText('No export yet.', { exact: true })).toBeVisible()
    await expect(section.getByText('6 changes · 4 kept · 2 open items', { exact: true })).toBeVisible()
    await expect(section.locator('[data-erase-list="changes"] li')).toHaveCount(6)
    await expect(section.locator('[data-erase-list="kept"] li')).toHaveCount(4)
    await expect(section.locator('[data-erase-list="open"] li')).toHaveCount(2)
    await expect(section.getByText('not counted (took too long)', { exact: true })).toBeVisible()
    await section.getByLabel(`Type ${SUBJECT.username} to confirm`).fill(SUBJECT.username)
    await expect(section.getByRole('button', { name: 'Erase permanently' })).toBeEnabled()
    await blur(page)
    await networkQuiet(page, api)
    expect(world.writes, 'opening and typing write nothing').toEqual([])
    await visualRegion(page, 'users-personal-data', theme, section)
    assertHermetic(api, errors)
  })

  // ── W38 (W31 F-1 / F-2): three NEW regions ──

  test(`"Your data" with a pending erasure request — ${theme}`, async ({ page }) => {
    const world = subjectRightsWorld({ myExports: [exportJob('succeeded')], myErasureRequest: myErasureRequest('pending') })
    const { api, errors } = await openRollout(page, '/settings/profile', { theme, handlers: world.handlers, ready: myData, user: ME })
    await expect(myData(page).locator('[data-my-erasure]')).toHaveAttribute('data-erasure-request-state', 'pending')
    await expect(myData(page).getByText('You asked on Sep 15, 2026. An administrator will review it.', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    await visualRegion(page, 'profile-erasure-pending', theme, myData(page))
    assertHermetic(api, errors)
  })

  test(`Users › Erasure requests: a pending request and the admin's own — ${theme}`, async ({ page }) => {
    const own = erasureRequestRow(ADMIN_ROW, { id: OWN_REQUEST_ID, requester_is_you: true, requested_at: '2026-09-17T09:30:00Z' })
    const world = subjectRightsWorld({ erasureRequests: [erasureRequestRow(), own] })
    const { api, errors } = await openRollout(page, '/users?tab=erasure-requests', { theme, handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
    const tab = page.locator('[data-erasure-requests-tab]')
    await expect(tab.getByText('Another administrator must review your own request.', { exact: true })).toBeVisible()
    await networkQuiet(page, api)
    await visualRegion(page, 'users-erasure-requests', theme, tab)
    assertHermetic(api, errors)
  })

  test(`SSO › SCIM: erase chosen, the warning and the acknowledgement — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, '/settings/sso', { theme, handlers: ssoInPageWorld().handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
    await page.getByRole('button', { name: 'SCIM Tokens' }).click()
    const section = page.locator('[data-scim-delete-action]')
    await section.getByRole('radio', { name: /Erase the person's data/ }).check()
    await section.getByRole('checkbox', { name: 'I understand an erasure cannot be undone' }).check()
    await expect(section.getByRole('button', { name: 'Save', exact: true })).toBeEnabled()
    await blur(page)
    await networkQuiet(page, api)
    await visualRegion(page, 'sso-scim-delete-erase', theme, section)
    assertHermetic(api, errors)
  })

  test(`an erased person's row — ${theme}`, async ({ page }) => {
    const world = subjectRightsWorld()
    const { api, errors } = await openRollout(page, '/users', { theme, handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
    const row = page.locator(`tr[data-user-row="${ERASED.id}"]`)
    await expect(row.getByText('Erased user', { exact: true })).toBeVisible()
    await expect(row.locator('[data-erased-badge]')).toHaveText('Erased')
    await expect(row.getByRole('button')).toHaveCount(1)
    await networkQuiet(page, api)
    await visualRegion(page, 'users-erased-row', theme, row)
    assertHermetic(api, errors)
  })
}
