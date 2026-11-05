/**
 * W31 STRAT-12 (`docs/deferred-scope-2026-10-13/work/W31-contract.md` §4.1, §6.3 G-D, §6.5): three NEW regions, every
 * theme (`THEMES_WITH_PAPER`), each a state the product produces together:
 *
 *  - `profile-my-data` — Settings › Profile's "Your data" card with a finished export of my own data: the body,
 *    "Prepare a new copy" + Download (W32), "Ready — 1.4 MB, signed. Available until Sep 21, 11:59." and the line saying an
 *    erasure is asked of an administrator;
 *  - `users-personal-data` — a Users row's Personal data section as an ADMIN opens it for a person with no export yet:
 *    the Export block ("No export yet."), and the Erase panel's preview — 6 changes, 4 kept (one "not counted (took
 *    too long)"), 2 open items — with the username typed into the confirm, so "Erase permanently" is enabled;
 *  - `users-erased-row` — an erased person's row in the table: "Erased user" over the pseudonym, the "Erased" badge,
 *    the role as text and the Personal data action alone.
 *
 * No existing region changes (`users-row-edit` shoots only the Edit section). World: `subjectRightsWorld({...})` in
 * `production/fixtures-subject-rights.ts`. PNGs are rendered by CI only (`visual-baselines.yml`, Linux) and READ
 * before merge; nobody generates them locally.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, PINNED, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { USER } from './production/fixtures'
import { ADMIN } from './production/fixtures-releases'
import { ERASED, exportJob, SUBJECT, subjectRightsWorld } from './production/fixtures-subject-rights'

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
