/**
 * W38 (W31 F-1 / F-2, `docs/deferred-scope-2026-10-13/work/W38-contract.md` §4), hermetic on `subjectRightsWorld({...})`
 * (`visual/production/fixtures-subject-rights.ts`) and `ssoInPageWorld({...})` (`fixtures-in-page-b.ts`), at
 * 1280 × 900 and 1440 × 900 — one journey per state:
 *
 *  - Profile, nothing asked → "Ask to have your account erased" → the confirm IN the card → "Send request" → ONE
 *    `POST /api/v1/me/erasure-request` with no body → pending ("You asked on Sep 18, 2026. …") → "Withdraw request" →
 *    the strip → ONE withdraw → withdrawn (the button again).
 *  - Profile, declined: "Declined on Sep 17, 2026: <the reason's sentence>" + the button.
 *  - Profile, refused (409 `last_admin_self`): the sentence inline, still no request.
 *  - Users › Erasure requests: Review → the person's Personal data under the row says "This person asked for erasure
 *    on Sep 15, 2026." → the typed confirm → Erase → the row reads "Accepted — erasure started" while the phase line
 *    runs; ONE erasure POST.
 *  - Decline → the reason → the person's sentence → ONE decline POST with ONLY the code → Pending is empty; Decided
 *    lists "Declined — Identity not confirmed".
 *  - The admin's own request: the `own_request` note, no buttons.
 *  - SSO › SCIM Tokens: deactivate by default → erase → the warning + the acknowledgement enable Save → ONE PUT
 *    `{action: 'erase'}`; a 403 `instance_interactive_only` reads inline and nothing changes.
 * No horizontal overflow; every request answered (`assertHermetic`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic } from '../lib/production-pages'
import { expectNoHorizontalOverflow, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { USER } from '../visual/production/fixtures'
import { ssoInPageWorld } from '../visual/production/fixtures-in-page-b'
import { ADMIN } from '../visual/production/fixtures-releases'
import {
  ADMIN_ROW,
  erasureRequestRow,
  myErasureRequest,
  OWN_REQUEST_ID,
  QUEUE_REQUEST_ID,
  SUBJECT,
  subjectRightsWorld,
} from '../visual/production/fixtures-subject-rights'

const PROGRESS = { timeout: 20_000 }
const ME = { ...USER, id: SUBJECT.id, username: SUBJECT.username, email: SUBJECT.email, full_name: SUBJECT.full_name, role: 'TESTER' }
const INTERACTIVE_ONLY = 'The instance default is changed by a signed-in administrator; API keys can only read it.'

const myData = (p: Page) => p.locator('[data-my-data]')
const block = (p: Page) => p.locator('[data-my-erasure]')
const askButton = (p: Page) => block(p).getByRole('button', { name: 'Ask to have your account erased', exact: true })
const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })
const queueRow = (p: Page, id: string) => p.locator(`tr[data-erasure-request="${id}"]`)
const scimSection = (p: Page) => p.getByRole('region', { name: 'When your identity provider deletes a user' })

for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 900 }]) {
  test.describe(`${viewport.width} × ${viewport.height}`, () => {
    test.use({ viewport, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

    test('Profile: Ask → the confirm in the card → ONE POST → pending → Withdraw → ONE withdraw → the button again', async ({ page }) => {
      const world = subjectRightsWorld()
      const { api, errors } = await openRollout(page, '/settings/profile', { handlers: world.handlers, ready: myData, user: ME })
      await expect(block(page)).toHaveAttribute('data-erasure-request-state', 'none')
      await askButton(page).click()
      const strip = block(page).locator('[data-inline-confirm]')
      await expect(strip).toContainText('An administrator reviews the request. If they erase your account you are signed out at once and it cannot be undone; audit records are kept, as the law allows.')
      await expect(page.getByRole('dialog')).toHaveCount(0)
      expect(world.writes, 'opening the question sends nothing').toEqual([])
      await expectNoHorizontalOverflow(page, `the erasure confirm at ${viewport.width}`)
      await strip.getByRole('button', { name: 'Send request', exact: true }).click()

      await expect(block(page)).toHaveAttribute('data-erasure-request-state', 'pending')
      await expect(block(page).getByText('You asked on Sep 18, 2026. An administrator will review it.', { exact: true })).toBeVisible()
      await expect(askButton(page)).toHaveCount(0)
      expect(world.writes).toEqual([{ line: 'POST /api/v1/me/erasure-request', body: null }])

      await block(page).getByRole('button', { name: 'Withdraw request', exact: true }).click()
      const withdraw = block(page).locator('[data-inline-confirm]')
      await expect(withdraw).toContainText('Withdraw your erasure request?')
      await withdraw.locator('[data-inline-confirm-action]').click()
      await expect(block(page)).toHaveAttribute('data-erasure-request-state', 'withdrawn')
      await expect(askButton(page)).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([
        { line: 'POST /api/v1/me/erasure-request', body: null },
        { line: 'POST /api/v1/me/erasure-request/withdraw', body: null },
      ])
      expect(requestsTo(api, '/api/v1/me/erasure-request').filter((l) => l.startsWith('GET ')), 'ONE read; the POSTs answer the rest').toHaveLength(1)
      assertHermetic(api, errors)
    })

    test('Profile: a declined request reads its date and the reason\'s sentence, and offers the button again', async ({ page }) => {
      const world = subjectRightsWorld({ myErasureRequest: myErasureRequest('declined') })
      const { api, errors } = await openRollout(page, '/settings/profile', { handlers: world.handlers, ready: myData, user: ME })
      await expect(block(page)).toHaveAttribute('data-erasure-request-state', 'declined')
      await expect(block(page).getByText('Declined on Sep 17, 2026: We must keep your account for a legal obligation. Ask your administrator for details.', { exact: true })).toBeVisible()
      await expect(askButton(page)).toBeEnabled()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      await expectNoHorizontalOverflow(page, `the declined request at ${viewport.width}`)
      assertHermetic(api, errors)
    })

    test('Profile: the last administrator is refused inline (last_admin_self) and nothing is filed', async ({ page }) => {
      const world = subjectRightsWorld({ fileRefusal: 'last_admin_self' })
      const { api, errors } = await openRollout(page, '/settings/profile', { handlers: world.handlers, ready: myData, user: ME })
      await askButton(page).click()
      await block(page).getByRole('button', { name: 'Send request', exact: true }).click()
      await expect(block(page).getByRole('alert')).toHaveText('You are the only administrator, so your account cannot be erased. Make someone else an administrator first.')
      await expect(block(page)).toHaveAttribute('data-erasure-request-state', 'none')
      await expect(page.getByText(/Request failed with status code/)).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: 'POST /api/v1/me/erasure-request', body: null }])
      assertHermetic(api, errors)
    })

    test('Users › Erasure requests: Review → the Personal data under the row names the request → Erase → accepted', async ({ page }) => {
      const world = subjectRightsWorld({ erasureRequests: [erasureRequestRow()] })
      const { api, errors } = await openRollout(page, '/users?tab=erasure-requests', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      await expect(page.getByRole('tab', { name: 'Erasure requests' })).toHaveAttribute('aria-selected', 'true')
      const row = queueRow(page, QUEUE_REQUEST_ID)
      await expect(row.getByText('Pending', { exact: true })).toBeVisible()
      expect(requestsTo(api, '/api/v1/users/erasure-requests')).toEqual(['GET /api/v1/users/erasure-requests?status=pending'])
      await row.getByRole('button', { name: `Review the erasure request of ${SUBJECT.full_name}` }).click()
      const section = page.getByRole('region', { name: `Personal data for ${SUBJECT.full_name}` })
      await expect(section.getByText('This person asked for erasure on Sep 15, 2026.', { exact: true })).toBeVisible()
      await expectNoHorizontalOverflow(page, `the Review section at ${viewport.width}`)
      await section.getByLabel(`Type ${SUBJECT.username} to confirm`).fill(SUBJECT.username)
      await section.getByRole('button', { name: 'Erase permanently' }).click()

      await expect(row.getByText('Accepted — erasure started', { exact: true })).toBeVisible(PROGRESS)
      await expect(section.getByText(/^Erasure in progress — signed out and disabled\./).first()).toBeVisible(PROGRESS)
      await expect(section.getByText('Requested by Admin.', { exact: true })).toBeVisible()
      expect(world.writes).toEqual([{ line: `POST /api/v1/users/${SUBJECT.id}/erasure`, body: { confirm_username: SUBJECT.username } }])
      expect(world.queue[0]).toMatchObject({ status: 'accepted', erasure_job_id: expect.any(String) })
      assertHermetic(api, errors)
    })

    test('Users › Erasure requests: Decline → a reason → ONE POST with ONLY the code → Decided lists it', async ({ page }) => {
      const world = subjectRightsWorld({ erasureRequests: [erasureRequestRow()] })
      const { api, errors } = await openRollout(page, '/users?tab=erasure-requests', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      await queueRow(page, QUEUE_REQUEST_ID).getByRole('button', { name: `Decline the erasure request of ${SUBJECT.full_name}` }).click()
      const form = page.getByRole('region', { name: `Decline the erasure request of ${SUBJECT.full_name}` })
      await expect(form.getByRole('button', { name: 'Decline request' })).toBeDisabled()
      await form.getByLabel('Reason').selectOption('identity_unconfirmed')
      await expect(form.getByText('They will read: “We could not confirm the request came from you. Ask your administrator.”', { exact: true })).toBeVisible()
      await expectNoHorizontalOverflow(page, `the Decline form at ${viewport.width}`)
      await form.getByRole('button', { name: 'Decline request' }).click()
      await expect(page.getByText('No erasure requests are waiting for review.', { exact: true })).toBeVisible()
      expect(world.writes).toEqual([{ line: `POST /api/v1/users/erasure-requests/${QUEUE_REQUEST_ID}/decline`, body: { decline_code: 'identity_unconfirmed' } }])

      await page.getByRole('button', { name: 'Decided', exact: true }).click()
      await expect(queueRow(page, QUEUE_REQUEST_ID).getByText('Declined — Identity not confirmed', { exact: true })).toBeVisible()
      await expect(queueRow(page, QUEUE_REQUEST_ID).getByText(/ by Admin$/)).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes, 'reading Decided writes nothing').toHaveLength(1)
      assertHermetic(api, errors)
    })

    test('Users › Erasure requests: the admin\'s own request reads the note and offers nothing', async ({ page }) => {
      const own = erasureRequestRow(ADMIN_ROW, { id: OWN_REQUEST_ID, requester_is_you: true })
      const world = subjectRightsWorld({ erasureRequests: [erasureRequestRow(), own] })
      const { api, errors } = await openRollout(page, '/users?tab=erasure-requests', { handlers: world.handlers, ready: h1('User Management'), user: ADMIN })
      const row = queueRow(page, OWN_REQUEST_ID)
      await expect(row.getByText('Another administrator must review your own request.', { exact: true })).toBeVisible()
      await expect(row.getByRole('button')).toHaveCount(0)
      await expect(queueRow(page, QUEUE_REQUEST_ID).getByRole('button')).toHaveCount(2)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      await expectNoHorizontalOverflow(page, `the queue at ${viewport.width}`)
      assertHermetic(api, errors)
    })

    test('SSO › SCIM Tokens: erase needs the acknowledgement → ONE PUT {action: "erase"}', async ({ page }) => {
      const world = ssoInPageWorld()
      const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
      await page.getByRole('button', { name: 'SCIM Tokens' }).click()
      await expect(scimSection(page).getByRole('radio', { name: /Deactivate the account \(default\)/ })).toBeChecked()
      await scimSection(page).getByRole('radio', { name: /Erase the person's data — cannot be undone/ }).check()
      await expect(scimSection(page).locator('[data-scim-erase-warning]')).toBeVisible()
      const save = scimSection(page).getByRole('button', { name: 'Save', exact: true })
      await expect(save).toBeDisabled()
      await scimSection(page).getByRole('checkbox', { name: 'I understand an erasure cannot be undone' }).check()
      await expectNoHorizontalOverflow(page, `the SCIM delete setting at ${viewport.width}`)
      await save.click()
      await expect(scimSection(page)).toHaveAttribute('data-scim-delete-saved', 'erase')
      await expect(scimSection(page).getByText(/^Last changed .+ by Admin\.$/)).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: 'PUT /api/v1/settings/scim-delete-action', body: { action: 'erase' } }])
      assertHermetic(api, errors)
    })

    test('SSO › SCIM Tokens: a 403 instance_interactive_only reads inline and the setting stays', async ({ page }) => {
      const world = ssoInPageWorld({ scimDeletePutRefusal: { status: 403, code: 'instance_interactive_only', message: INTERACTIVE_ONLY } })
      const { api, errors } = await openRollout(page, '/settings/sso', { handlers: world.handlers, ready: h1('SSO & Identity Management'), user: ADMIN })
      await page.getByRole('button', { name: 'SCIM Tokens' }).click()
      await scimSection(page).getByRole('radio', { name: /Erase the person's data/ }).check()
      await scimSection(page).getByRole('checkbox', { name: 'I understand an erasure cannot be undone' }).check()
      await scimSection(page).getByRole('button', { name: 'Save', exact: true }).click()
      await expect(scimSection(page).getByRole('alert')).toHaveText(INTERACTIVE_ONLY)
      await expect(scimSection(page)).toHaveAttribute('data-scim-delete-saved', 'deactivate')
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: 'PUT /api/v1/settings/scim-delete-action', body: { action: 'erase' } }])
      assertHermetic(api, errors)
    })
  })
}
