/**
 * W05 REL-02, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.5), on `/releases`:
 * a release has a real owner, chosen from the people who can work on its project.
 *
 *  - 2026.11's card names its owner (Dana Whitfield), never the project; 2026.12 reads "No owner";
 *  - Edit -> Owner offers the project's people only (no synthetic QA lead, no service account, no
 *    deactivated user), and the members list is asked for only when Edit opens;
 *  - Owner Priya -> Save: the PUT body carries `owner_user_id`; the card reads `PN Priya Nair`; a search for
 *    "priya" keeps it and hides the unowned release;
 *  - "No owner" -> Save sends `owner_user_id: null` and the card reads "No owner"; a save that leaves the
 *    owner alone sends no `owner_user_id` at all.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-releases.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RELEASE, RELEASE_PEOPLE, releasesHandlers } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace(/\./g, '\\.')} `) })
const ownerChip = (page: Page, name: string) => card(page, name).locator('[data-owner-chip]')
/** The chip's line: the avatar (initials, or a dashed dash), the name, the due date. */
const ownerLine = (page: Page, name: string) => card(page, name).locator('div:has(> [data-owner-chip])')

async function editOwner(page: Page, release: string) {
  await card(page, release).click()
  const panel = page.getByRole('dialog', { name: release })
  await panel.getByRole('button', { name: 'Edit' }).click()
  const editor = page.getByRole('dialog', { name: 'Edit Release' })
  await expect(editor).toBeVisible()
  return { panel, editor, owner: editor.getByLabel('Owner') }
}

test('Owner Priya -> Save: the PUT carries owner_user_id, the card reads PN Priya Nair, and search finds her', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  await expect(ownerChip(page, '2026.11')).toHaveAttribute('data-owner-chip', 'set')
  await expect(ownerChip(page, '2026.11')).toHaveText('DW')
  await expect(ownerLine(page, '2026.11')).toContainText('Dana Whitfield')
  await expect(ownerChip(page, '2026.12')).toHaveAttribute('data-owner-chip', 'none')
  await expect(ownerLine(page, '2026.12')).toContainText('No owner')
  // The project's name is never an owner.
  await expect(card(page, '2026.12')).not.toContainText('Checkout')
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/members$/), 'no members list until somebody edits').toEqual([])

  const { panel, editor, owner } = await editOwner(page, '2026.11')
  await expect(owner).toHaveValue(RELEASE_PEOPLE.dana.id)
  await expect(owner.locator('option')).toHaveText(['No owner', 'Dana Whitfield', 'Priya Nair', 'QA Lead'])
  await owner.selectOption({ label: 'Priya Nair' })
  await editor.getByRole('button', { name: 'Save Changes' }).click()
  await expect(editor).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)

  // The initials of the first and last name (`mapping.initialsOf`; the contract's "PR" is its typo).
  await expect(ownerChip(page, '2026.11')).toHaveText('PN')
  await expect(ownerLine(page, '2026.11')).toContainText('Priya Nair')
  await page.getByPlaceholder(/Search releases/).fill('priya')
  await expect(card(page, '2026.11')).toBeVisible()
  await expect(card(page, '2026.12')).toHaveCount(0)
  await page.getByPlaceholder(/Search releases/).fill('')
  await expect(ownerLine(page, '2026.12')).toContainText('No owner')

  await networkQuiet(page, api)
  expect(requestsTo(api, /\/members$/)).toHaveLength(1)
  expect(writes).toHaveLength(1)
  expect(writes[0].line).toBe(`PUT /api/v1/releases/${RELEASE.active}`)
  expect(writes[0].body).toMatchObject({ name: '2026.11', status: 'in_progress', owner_user_id: RELEASE_PEOPLE.priya.id })
  assertHermetic(api, errors)
})

test('"No owner" -> Save sends owner_user_id null; leaving the owner alone sends none', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  // A save that does not touch the owner: no owner_user_id in the body.
  let edit = await editOwner(page, '2026.10')
  await expect(edit.owner).toHaveValue(RELEASE_PEOPLE.qaLead.id)
  await edit.editor.getByRole('button', { name: 'Save Changes' }).click()
  await expect(edit.editor).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes).toHaveLength(1)
  expect(writes[0].body).not.toHaveProperty('owner_user_id')

  // Now clear it.
  await edit.panel.getByRole('button', { name: 'Edit' }).click()
  edit = { ...edit, editor: page.getByRole('dialog', { name: 'Edit Release' }) }
  await edit.editor.getByLabel('Owner').selectOption({ label: 'No owner' })
  await edit.editor.getByRole('button', { name: 'Save Changes' }).click()
  await expect(edit.editor).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(ownerChip(page, '2026.10')).toHaveAttribute('data-owner-chip', 'none')
  await expect(ownerLine(page, '2026.10')).toContainText('No owner')
  await networkQuiet(page, api)
  expect(writes).toHaveLength(2)
  expect(writes[1]).toMatchObject({ line: `PUT /api/v1/releases/${RELEASE.shipped}`, body: { owner_user_id: null } })
  assertHermetic(api, errors)
})
