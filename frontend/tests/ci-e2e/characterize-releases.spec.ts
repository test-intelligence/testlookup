/**
 * W27a PAN-02 CHARACTERIZATION (`docs/deferred-scope-2026-10-13/work/W27a-contract.md` §2.2, rows R2-R5, R8, R9),
 * hermetic, on `/releases` and `/releases/:id`. Written against the MODAL tree (a release in a modal side panel,
 * its forms in dialogs, native `confirm()`s) and kept green, UNEDITED, after the in-page conversion: every
 * container is found by the name it keeps (`opened`), every question answered whichever way it is asked
 * (`answerConfirm`). What is pinned is the flow: the exact request (method, path, whole body), the re-reads
 * after it, what the page then shows, and who may do it.
 *
 *  - R2 New release → Create Release: `POST /api/v1/releases` with every empty field OMITTED (never `""`); the
 *    list re-reads and the new card appears; Cancel and the X send nothing;
 *  - R3 Edit → Save Changes: `PUT /api/v1/releases/{id}` with the form's values (no `owner_user_id` when the
 *    owner is untouched); the list AND the release re-read; the release stays open showing the new text;
 *  - R4 Edit → Cancel, and Escape in the editor: nothing sent, the release stays open;
 *  - R5 Link Run → a run (+ a phase): `POST …/test-runs` `{ test_run_id, phase_id }` (`phase_id` absent without a
 *    phase); the release re-reads and lists the run; Cancel sends nothing;
 *  - R8 Remove phase (admin): dismissed → nothing; confirmed → `DELETE …/phases/{pid}`, the release re-reads;
 *  - R9 Delete release (admin): dismissed → nothing; confirmed → `DELETE /api/v1/releases/{id}`, `Release
 *    deleted`, the release closes, the list re-reads without its card; on `/releases/:id` → `/releases`;
 *  - role gating: a QA lead's Delete and Remove phase are disabled with `Admin role required`.
 *
 * R1 (the five detail reads) is `releases-panel.spec.ts`; R6, R10 `releases-activate-sync.spec.ts`; R7
 * `releases-phase-reason.spec.ts`; R11 `releases-gate.spec.ts`; R12 `fold-release-gate.spec.ts`; the owner part
 * of R3 `releases-owner.spec.ts`.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `releasesInPageWorld()`
 * (`fixtures-in-page.ts`, over `fixtures-releases.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { answerConfirm, opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { LINKABLE_RUN_ID, releasesInPageWorld } from '../visual/production/fixtures-in-page'
import { ADMIN, RELEASE } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${escape(name)} `) })
const SECOND_RUN_ID = '44444444-4444-4444-8444-100000000001'
const ADMIN_REQUIRED = 'Admin role required'

/** The whole URL is `<origin><path>` (never a substring match). */
async function expectPath(page: Page, path: string) {
  const origin = new URL(page.url()).origin
  await expect(page).toHaveURL(`${origin}${path}`)
}

/** Open a release from its card; its detail has loaded. */
async function openRelease(page: Page, name: string) {
  await card(page, name).click()
  const release = opened(page, name)
  await expect(release).toBeVisible()
  await expect(release.getByRole('heading', { name: /^Linked Test Runs/ })).toBeVisible()
  return release
}

type Api = Parameters<typeof requestsTo>[0]
const reads = (api: Api, path: string) => requestsTo(api, path).filter((line) => line.startsWith('GET ')).length
const detailReads = (api: Api, id: string) => reads(api, `/api/v1/releases/${id}`)
const listReads = (api: Api) => reads(api, '/api/v1/releases')

test('R2 New release → Create Release: the POST omits every empty field; the list re-reads and the card appears', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  const header = page.locator('[data-page-header]')

  // Cancel and the X close the form and send nothing.
  await header.getByRole('button', { name: 'New release' }).click()
  let form = opened(page, 'New Release')
  await expect(form).toBeVisible()
  await form.getByLabel('Release Name *').fill('2027.01')
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await header.getByRole('button', { name: 'New release' }).click()
  form = opened(page, 'New Release')
  await expect(form.getByLabel('Release Name *'), 'a reopened form starts empty').toHaveValue('')
  await form.getByRole('button', { name: 'Close release dialog' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes).toEqual([])

  // A name only: every other field is left out of the body, the status is the default.
  const before = listReads(api)
  await header.getByRole('button', { name: 'New release' }).click()
  form = opened(page, 'New Release')
  await expect(form.getByLabel('Status')).toHaveValue('planning')
  await expect(form.getByLabel('Owner'), 'a new release has no owner field').toHaveCount(0)
  await form.getByLabel('Release Name *').fill('  2027.01  ')
  await form.getByRole('button', { name: 'Create Release' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Release created')).toBeVisible()
  await expect(card(page, '2027.01')).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: 'POST /api/v1/releases', body: { project_id: PROJECT_ID, name: '2027.01', status: 'planning' } }])
  expect(listReads(api), 'the list re-reads after the create').toBeGreaterThan(before)
  assertHermetic(api, errors)
})

test('R2 New release with every field: the POST carries the version, status, date and description', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  await page.locator('[data-page-header]').getByRole('button', { name: 'New release' }).click()
  const form = opened(page, 'New Release')
  await form.getByLabel('Release Name *').fill('2027.01')
  await form.getByLabel('Version').fill('2027.1.0')
  await form.getByLabel('Status').selectOption('in_progress')
  await form.getByLabel('Planned Release Date').fill('2026-10-30')
  await form.getByLabel('Description').fill('Spring wallet')
  await form.getByRole('button', { name: 'Create Release' }).click()
  await expect(form).toHaveCount(0)
  await expect(card(page, '2027.01')).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([
    {
      line: 'POST /api/v1/releases',
      body: {
        project_id: PROJECT_ID,
        name: '2027.01',
        version: '2027.1.0',
        description: 'Spring wallet',
        status: 'in_progress',
        planned_date: '2026-10-30T00:00:00.000Z',
      },
    },
  ])
  assertHermetic(api, errors)
})

test('R3 Edit → Save Changes: the PUT body; the list and the release re-read; the release stays open with the new text', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  const release = await openRelease(page, '2026.11')
  await expect(release.getByText('Checkout revamp', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  const lists = listReads(api)
  const details = detailReads(api, RELEASE.active)

  await release.getByRole('button', { name: 'Edit', exact: true }).click()
  const editor = opened(page, 'Edit Release')
  await expect(editor).toBeVisible()
  await expect(editor.getByLabel('Release Name *')).toHaveValue('2026.11')
  await expect(editor.getByLabel('Version')).toHaveValue('2026.11')
  await expect(editor.getByLabel('Status')).toHaveValue('in_progress')
  await expect(editor.getByLabel('Planned Release Date')).toHaveValue('2026-09-23')
  await expect(editor.getByLabel('Description')).toHaveValue('Checkout revamp')
  await editor.getByLabel('Description').fill('Checkout revamp, phase 2')
  await editor.getByRole('button', { name: 'Save Changes' }).click()

  await expect(editor).toHaveCount(0)
  await expect(page.getByText('Release updated')).toBeVisible()
  await expect(release, 'the release stays open').toBeVisible()
  await expect(release.getByText('Checkout revamp, phase 2', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([
    {
      line: `PUT /api/v1/releases/${RELEASE.active}`,
      body: {
        name: '2026.11',
        version: '2026.11',
        description: 'Checkout revamp, phase 2',
        status: 'in_progress',
        planned_date: '2026-09-23T00:00:00.000Z',
      },
    },
  ])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)
  expect(detailReads(api, RELEASE.active), 'the release re-reads once').toBe(details + 1)
  assertHermetic(api, errors)
})

test('R4 Edit → Cancel, and Escape in the editor: nothing is sent and the release stays open', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  const release = await openRelease(page, '2026.11')

  await release.getByRole('button', { name: 'Edit', exact: true }).click()
  let editor = opened(page, 'Edit Release')
  await editor.getByLabel('Description').fill('never saved')
  await editor.getByRole('button', { name: 'Cancel' }).click()
  await expect(editor).toHaveCount(0)
  await expect(release).toBeVisible()

  await release.getByRole('button', { name: 'Edit', exact: true }).click()
  editor = opened(page, 'Edit Release')
  await expect(editor.getByLabel('Description'), 'a reopened editor starts from the release').toHaveValue('Checkout revamp')
  await editor.getByLabel('Release Name *').focus()
  await page.keyboard.press('Escape')
  await expect(editor).toHaveCount(0)
  await expect(release, 'Escape closed the editor only').toBeVisible()
  await expect(release.getByText('Checkout revamp', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([])
  assertHermetic(api, errors)
})

test('R5 Link Run: the POST with the run and the phase, then without a phase; the release re-reads and lists them; Cancel sends nothing', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  const release = await openRelease(page, '2026.11')
  await expect(release.getByRole('heading', { name: 'Linked Test Runs (2)' })).toBeVisible()

  // Cancel: nothing.
  await release.getByRole('button', { name: 'Link Run', exact: true }).click()
  let form = opened(page, 'Link Test Run')
  await expect(form.getByRole('button', { name: 'Link Run', exact: true }), 'nothing to link until a run is chosen').toBeDisabled()
  await form.getByLabel('Test Run', { exact: true }).selectOption(LINKABLE_RUN_ID)
  await form.getByRole('button', { name: 'Cancel' }).click()
  await expect(form).toHaveCount(0)
  await networkQuiet(page, api)
  expect(writes).toEqual([])

  // A run and a phase.
  const details = detailReads(api, RELEASE.active)
  await release.getByRole('button', { name: 'Link Run', exact: true }).click()
  form = opened(page, 'Link Test Run')
  await expect(form.getByLabel('Phase (optional)').locator('option')).toHaveText(['— No phase —', 'Build verification', 'Payments soak', 'Customer acceptance'])
  await form.getByLabel('Test Run', { exact: true }).selectOption(LINKABLE_RUN_ID)
  await form.getByLabel('Phase (optional)').selectOption({ label: 'Payments soak' })
  await form.getByRole('button', { name: 'Link Run', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByText('Test run linked to release')).toBeVisible()
  await expect(release.getByRole('heading', { name: 'Linked Test Runs (3)' })).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([
    { line: `POST /api/v1/releases/${RELEASE.active}/test-runs`, body: { test_run_id: LINKABLE_RUN_ID, phase_id: 'ph-soak' } },
  ])
  expect(detailReads(api, RELEASE.active), 'the release re-reads once').toBe(details + 1)

  // A run, no phase: `phase_id` is not in the body at all.
  await release.getByRole('button', { name: 'Link Run', exact: true }).click()
  form = opened(page, 'Link Test Run')
  await form.getByLabel('Test Run', { exact: true }).selectOption(SECOND_RUN_ID)
  await form.getByRole('button', { name: 'Link Run', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(release.getByRole('heading', { name: 'Linked Test Runs (4)' })).toBeVisible()
  await networkQuiet(page, api)
  expect(writes.slice(1)).toEqual([{ line: `POST /api/v1/releases/${RELEASE.active}/test-runs`, body: { test_run_id: SECOND_RUN_ID } }])
  assertHermetic(api, errors)
})

test('R8 Remove phase (admin): dismissed sends nothing; confirmed DELETEs the phase and the release re-reads without it', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready, user: ADMIN })
  const release = await openRelease(page, '2026.11')
  const remove = release.getByRole('button', { name: 'Remove phase Payments soak' })
  await expect(remove).toBeEnabled()

  const dismissed = await answerConfirm(page, remove, 'dismiss', { confirmLabel: 'Remove' })
  expect(dismissed.message).toBe('Remove phase "Payments soak"?')
  await networkQuiet(page, api)
  expect(writes).toEqual([])
  await expect(release.getByLabel('Status of phase Payments soak')).toBeVisible()

  const details = detailReads(api, RELEASE.active)
  const confirmed = await answerConfirm(page, remove, 'accept', { confirmLabel: 'Remove' })
  expect(confirmed.message).toBe('Remove phase "Payments soak"?')
  await expect(release.getByLabel('Status of phase Payments soak')).toHaveCount(0)
  await expect(release.getByLabel('Status of phase Build verification')).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: `DELETE /api/v1/releases/${RELEASE.active}/phases/ph-soak`, body: null }])
  expect(detailReads(api, RELEASE.active), 'the release re-reads once').toBe(details + 1)
  assertHermetic(api, errors)
})

test('R9 Delete release (admin), from the open release: dismissed sends nothing; confirmed DELETEs it, closes it, and the list loses its card', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready, user: ADMIN })
  const release = await openRelease(page, '2026.12')
  const del = release.getByRole('button', { name: 'Delete', exact: true })
  await expect(del).toBeEnabled()

  const dismissed = await answerConfirm(page, del, 'dismiss', { confirmLabel: 'Delete' })
  expect(dismissed.message).toBe('Delete this release and all its phases? This cannot be undone.')
  await networkQuiet(page, api)
  expect(writes).toEqual([])
  await expect(release).toBeVisible()

  const lists = listReads(api)
  await answerConfirm(page, del, 'accept', { confirmLabel: 'Delete' })
  await expect(page.getByText('Release deleted')).toBeVisible()
  await expect(release, 'the deleted release closes').toHaveCount(0)
  await expect(card(page, '2026.12')).toHaveCount(0)
  await expect(card(page, '2026.11')).toBeVisible()
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: `DELETE /api/v1/releases/${RELEASE.planning}`, body: null }])
  expect(listReads(api), 'the list re-reads').toBeGreaterThan(lists)
  await expectPath(page, '/releases')
  assertHermetic(api, errors)
})

test('R9 Delete release (admin) on its own page: confirmed DELETEs it and lands on /releases', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, `/releases/${RELEASE.planning}`, {
    handlers,
    ready: (p) => p.locator('[data-release-page]').getByRole('heading', { name: /^Linked Test Runs/ }),
    user: ADMIN,
  })
  const del = page.locator('[data-release-page]').getByRole('button', { name: 'Delete', exact: true })
  const confirmed = await answerConfirm(page, del, 'accept', { confirmLabel: 'Delete' })
  expect(confirmed.message).toBe('Delete this release and all its phases? This cannot be undone.')
  await expect(page.getByText('Release deleted')).toBeVisible()
  await expectPath(page, '/releases')
  await expect(ready(page)).toBeVisible()
  // (Not pinned: the list is NOT re-read after this delete today, so the deleted card stays until the 30 s
  // poll — a pre-existing defect reported to G-B, not an expected output.)
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: `DELETE /api/v1/releases/${RELEASE.planning}`, body: null }])
  assertHermetic(api, errors)
})

test('role gating: a QA lead may edit and link, but Delete and Remove phase are disabled with "Admin role required"', async ({ page }) => {
  const { handlers, writes } = releasesInPageWorld()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  const release = await openRelease(page, '2026.11')
  await expect(release.getByRole('button', { name: 'Edit', exact: true })).toBeEnabled()
  await expect(release.getByRole('button', { name: 'Link Run', exact: true })).toBeEnabled()
  const del = release.getByRole('button', { name: 'Delete', exact: true })
  await expect(del).toBeDisabled()
  await expect(del).toHaveAttribute('title', ADMIN_REQUIRED)
  const remove = release.getByRole('button', { name: 'Remove phase Payments soak' })
  await expect(remove).toBeDisabled()
  await expect(remove).toHaveAttribute('title', ADMIN_REQUIRED)

  // The release's own page gates its Delete the same way.
  await page.goto(`/releases/${RELEASE.active}`)
  const own = page.locator('[data-release-page]')
  await expect(own.getByRole('heading', { name: /^Linked Test Runs/ })).toBeVisible()
  await expect(own.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled()
  await expect(own.getByRole('button', { name: 'Delete', exact: true })).toHaveAttribute('title', ADMIN_REQUIRED)
  await networkQuiet(page, api)
  expect(writes).toEqual([])
  assertHermetic(api, errors)
})
