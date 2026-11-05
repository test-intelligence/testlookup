/**
 * W31 STRAT-12 (`docs/deferred-scope-2026-10-13/work/W31-contract.md` §4, §6.3 G-D), hermetic on
 * `subjectRightsWorld({...})` (`visual/production/fixtures-subject-rights.ts`), at 1280 × 900 and 1440 × 900:
 *
 *  - Profile: "Download my data" → ONE `POST /api/v1/me/data-export` → "Preparing your archive…" while the job
 *    polls → "Ready — 1.4 MB, signed. Available until …" → Download asks the job's OWN download path and saves a file.
 *  - Users: no "Personal data" action on a service account row, a synthetic row or the admin's own row; the table's
 *    load asks nothing of the subject-rights API; an erased row reads "Erased user" + the badge with no other action.
 *  - Personal data → a preview with a blocker lists it and keeps "Erase permanently" disabled on the exact username;
 *    without blockers the button enables only on the exact username → erase → the phase line → `erase_done` → the
 *    row renders erased. No horizontal overflow; every request answered (`assertHermetic`).
 * The jobs poll every 2 s, so a progression through every phase takes several seconds.
 */
import { statSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { USER } from '../visual/production/fixtures'
import { ADMIN } from '../visual/production/fixtures-releases'
import {
  ADMIN_ROW,
  ERASED,
  ERASURE_JOB_ID,
  erasurePreview,
  MY_EXPORT_JOB_ID,
  SERVICE,
  SUBJECT,
  SUBJECT_PSEUDONYM,
  subjectRightsWorld,
  SYNTHETIC,
} from '../visual/production/fixtures-subject-rights'

const PROGRESS = { timeout: 20_000 }
const ME = { ...USER, id: SUBJECT.id, username: SUBJECT.username, email: SUBJECT.email, full_name: SUBJECT.full_name, role: 'TESTER' }
const ERASED_LINE = `Erased. This account now reads as “Erased user” (${SUBJECT_PSEUDONYM}).`

const myData = (p: Page) => p.locator('[data-my-data]')
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const userRow = (p: Page, id: string) => p.locator(`tr[data-user-row="${id}"]`)
const personalData = (p: Page, username: string) => p.getByRole('button', { name: `Personal data for ${username}`, exact: true })
const section = (p: Page) => p.getByRole('region', { name: `Personal data for ${SUBJECT.full_name}` })
const eraseButton = (p: Page) => section(p).getByRole('button', { name: 'Erase permanently' })
const confirmInput = (p: Page) => section(p).getByLabel(`Type ${SUBJECT.username} to confirm`)

for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 900 }]) {
  test.describe(`${viewport.width} × ${viewport.height}`, () => {
    test.use({ viewport, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test('Profile: Download my data → one POST → polled → ready → Download asks the job\'s own path', async ({ page }) => {
      const world = subjectRightsWorld()
      const { api, errors } = await openRollout(page, '/settings/profile', { handlers: world.handlers, ready: myData, user: ME })
      await expect(myData(page).getByText('No export yet.', { exact: true })).toBeVisible()
      await myData(page).getByRole('button', { name: 'Download my data' }).click()
      await expect(myData(page).getByText('Preparing your archive…', { exact: true })).toBeVisible(PROGRESS)
      await expect(myData(page).getByRole('button', { name: 'Download my data' })).toBeDisabled()
      await expect(myData(page).getByText('Ready — 1.4 MB, signed. Available until Sep 21, 11:59.', { exact: true })).toBeVisible(PROGRESS)
      await expect(myData(page).getByText('To have your account erased, ask an administrator.', { exact: false })).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: 'POST /api/v1/me/data-export', body: null }])

      const saved = page.waitForEvent('download')
      await myData(page).getByRole('button', { name: 'Download', exact: true }).click()
      const download = await saved
      expect(statSync(await download.path()).size, 'the saved file is not empty').toBeGreaterThan(0)
      expect(world.downloads).toEqual([`/api/v1/subject-data-jobs/${MY_EXPORT_JOB_ID}/download`])
      expect(world.writes, 'a download writes nothing').toHaveLength(1)
      await expectNoHorizontalOverflow(page, `/settings/profile at ${viewport.width}`)
      assertHermetic(api, errors)
    })

    test('Users: no Personal data on a service, synthetic or own row; an erased row reads "Erased user" with no other action', async ({ page }) => {
      const world = subjectRightsWorld()
      const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      await expect(personalData(page, SUBJECT.username)).toBeVisible()
      for (const row of [SERVICE, SYNTHETIC, ADMIN_ROW]) {
        await expect(userRow(page, row.id)).toBeVisible()
        await expect(personalData(page, row.username), row.username).toHaveCount(0)
      }
      const erased = userRow(page, ERASED.id)
      await expect(erased.getByText('Erased user', { exact: true })).toBeVisible()
      await expect(erased.getByText(ERASED.username, { exact: true })).toBeVisible()
      await expect(erased.locator('[data-erased-badge]')).toHaveText('Erased')
      await expect(erased.getByRole('button')).toHaveCount(1)
      await expect(erased.getByRole('combobox')).toHaveCount(0)
      await expect(page.locator('#main-content')).not.toContainText('@erased.invalid')
      await networkQuiet(page, api)
      expect(requestsTo(api, /^\/api\/v1\/(users\/[^/]+\/(data-jobs|erasure-preview)|subject-data-jobs\/.*)$/), 'the table load reads no job').toEqual([])
      await expectNoHorizontalOverflow(page, `/users at ${viewport.width}`)
      assertHermetic(api, errors)
    })

    test('Personal data: a blocker is listed and keeps "Erase permanently" disabled on the exact username', async ({ page }) => {
      const blocked = erasurePreview({ blockers: [{ code: 'export_blocks_erasure', message: "An export of this person's data is still running; erase after it finishes." }] })
      const world = subjectRightsWorld({ previews: { [SUBJECT.id]: blocked } })
      const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      await personalData(page, SUBJECT.username).click()
      await expect(section(page).getByText("An export of this person's data is still running; erase after it finishes.", { exact: true })).toBeVisible()
      await confirmInput(page).fill(SUBJECT.username)
      await expect(eraseButton(page)).toBeDisabled()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      await expectNoHorizontalOverflow(page, `the Personal data section at ${viewport.width}`)
      assertHermetic(api, errors)
    })

    test('Personal data: the exact username enables Erase → the phase line → erase_done → the row reads erased', async ({ page }) => {
      const world = subjectRightsWorld()
      const { api, errors } = await openRollout(page, '/users', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      await personalData(page, SUBJECT.username).click()
      await expect(section(page).getByText('6 changes · 4 kept · 2 open items', { exact: true })).toBeVisible()
      await expect(section(page).getByText('not counted (took too long)', { exact: true })).toBeVisible()
      for (const wrong of ['Quinn.Tester', 'quinn.tester ', 'quinn']) {
        await confirmInput(page).fill(wrong)
        await expect(eraseButton(page), JSON.stringify(wrong)).toBeDisabled()
      }
      await confirmInput(page).fill(SUBJECT.username)
      await expect(eraseButton(page)).toBeEnabled()
      await expectNoHorizontalOverflow(page, `the Erase panel at ${viewport.width}`)
      await eraseButton(page).click()

      await expect(section(page).getByText(/^Erasure in progress — signed out and disabled\. Step \d of 4: .+\.$/)).toBeVisible(PROGRESS)
      await expect(page.getByText(ERASED_LINE, { exact: true })).toBeVisible(PROGRESS)
      expect(world.writes).toEqual([{ line: `POST /api/v1/users/${SUBJECT.id}/erasure`, body: { confirm_username: SUBJECT.username } }])
      expect(requestsTo(api, `/api/v1/subject-data-jobs/${ERASURE_JOB_ID}`).length).toBeGreaterThanOrEqual(2)

      const row = userRow(page, SUBJECT.id)
      await expect(row.getByText('Erased user', { exact: true })).toBeVisible(PROGRESS)
      await expect(row.getByText(SUBJECT_PSEUDONYM, { exact: true })).toBeVisible()
      await expect(row.locator('[data-erased-badge]')).toHaveText('Erased')
      await expect(row.getByRole('button')).toHaveCount(1)
      await expect(row.getByRole('button', { name: `Personal data for ${SUBJECT_PSEUDONYM}` })).toBeVisible()
      await expect(page.locator('#main-content')).not.toContainText('@erased.invalid')
      await networkQuiet(page, api)
      await expectNoHorizontalOverflow(page, `/users after the erasure at ${viewport.width}`)
      assertHermetic(api, errors)
    })
  })
}
