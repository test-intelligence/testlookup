/**
 * W09 NTF-03 (contract §2.3, §4.4), hermetic, on `/settings/digests` › Digest Subscriptions: a Per Release
 * digest names a release, and a subscription that cannot deliver says so with its fix in the row.
 *
 *  - Per Release shows a **Release** select of the top bar project's releases (the active / in-progress one
 *    first: 2026.11, then 2026.12, 2026.10), read from the top bar's own cached list (no new request); Subscribe
 *    is disabled until one is chosen; Subscribe POSTs `scope_type: 'release', scope_value: <id>`; the new row
 *    reads `Per Release · 2026.12 via email`;
 *  - a legacy Per Release row saved with no release reads `Not delivering — choose a release`; choosing one in
 *    the row and **Save** PATCHes `{ scope_type, scope_value }` and the badge is gone; a row whose release no
 *    longer exists reads `Not delivering — its release no longer exists`; a refused fix shows the server's
 *    sentence in the row;
 *  - every new form is in the page (no dialog), and the page asks nothing new on load;
 *  - All Projects (an admin): `Pick a project in the top bar to choose a release.` and no Per Release subscribe.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `digestsWorld()` (`fixtures-notify-flaky.ts`).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { openProductionPage, respond } from '../lib/production-pages'
import { assertHermetic, expectInventory, networkQuiet, openRollout, requestsTo, SHELL_BASE } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  BAD_RELEASE,
  DIGEST_SUBSCRIPTIONS_PATH,
  digestsWorld,
  digestSubscription,
  FOREIGN_RELEASE_IDS,
  PER_RELEASE_SUB,
  RELEASE_ACTIVE_ID,
  RELEASE_PLANNING_ID,
  SUB_IDS,
  UNSCOPED_PER_RELEASE_SUB,
} from '../visual/production/fixtures-notify-flaky'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const ready = (p: Page) => p.locator('[data-page-header] h1')
const form = (page: Page) => page.locator('[data-subscription-form]')
const subRow = (page: Page, id: string) => page.locator(`[data-subscription-row="${id}"]`)
const summary = (row: Locator) => row.locator('[data-subscription-summary]')

/** A Per Release row whose release is gone (another project's id): `release_missing`. */
const MISSING_RELEASE_SUB = digestSubscription('00000000-0000-4000-8000-000000000e05', 'Old release digest', 'PER_RELEASE', {
  scope_type: 'release',
  scope_value: FOREIGN_RELEASE_IDS[0],
})

/** The id `digestsWorld` gives its first created subscription. */
const CREATED_ID = '00000000-0000-4000-8000-000000000e11'

async function optionTexts(select: Locator): Promise<string[]> {
  return select.locator('option').evaluateAll((options) =>
    options.filter((o) => (o as HTMLOptionElement).value !== '').map((o) => (o.textContent ?? '').trim()),
  )
}

test('Per Release names a release: the select, Subscribe disabled until chosen, the POST body, the row\'s label', async ({ page }) => {
  const world = digestsWorld({ subscriptions: [PER_RELEASE_SUB] })
  const { api, errors } = await openRollout(page, '/settings/digests', { handlers: world.handlers, ready })
  await expect(summary(subRow(page, SUB_IDS.perRelease))).toHaveText(/^Per Release · 2026\.11 via slack · Delivered: 2 times/)
  await networkQuiet(page, api)
  // Nothing new on load: the subscriptions, the retro flag (pre-W09), and the shell (whose release list the
  // select reuses).
  expectInventory(
    api,
    errors,
    [...SHELL_BASE, `GET ${DIGEST_SUBSCRIPTIONS_PATH}`, `GET /api/v1/feature-flags/weekly_retro_digest/status?project_id=${P}`],
    '/settings/digests',
  )

  await form(page).getByLabel('Digest Name').fill('2026.12 sign-off digest')
  await form(page).getByLabel('Schedule').selectOption({ label: 'Per Release' })
  const release = form(page).getByLabel('Release', { exact: true })
  await expect(release).toBeVisible()
  // The active (in-progress) release first, then the rest in the list's order.
  expect(await optionTexts(release)).toEqual(['2026.11', '2026.12', '2026.10'])
  const subscribe = form(page).getByRole('button', { name: 'Subscribe' })
  await expect(subscribe).toBeDisabled()

  await release.selectOption({ label: '2026.12' })
  await expect(subscribe).toBeEnabled()
  const post = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === DIGEST_SUBSCRIPTIONS_PATH)
  await subscribe.click()
  expect((await post).postDataJSON()).toEqual({
    project_id: P,
    name: '2026.12 sign-off digest',
    schedule: 'PER_RELEASE',
    channel: 'email',
    scope_type: 'release',
    scope_value: RELEASE_PLANNING_ID,
  })
  await expect(page.getByText('Subscription created')).toBeVisible()
  const created = subRow(page, CREATED_ID)
  await expect(summary(created)).toHaveText(/^Per Release · 2026\.12 via email/)
  await expect(created.locator('[data-scope-problem]')).toHaveCount(0)
  await expect(page.getByRole('dialog'), 'the form is in the page').toHaveCount(0)

  await networkQuiet(page, api)
  // The select read the top bar's own cached release list: no request of its own.
  expect(requestsTo(api, '/api/v1/releases')).toEqual([`GET /api/v1/releases?project_id=${P}`])
  expect(world.state.writes.map((w) => w.line)).toEqual([`POST ${DIGEST_SUBSCRIPTIONS_PATH}`])
  assertHermetic(api, errors)
})

test('a legacy unscoped row says it is not delivering; choosing a release in the row PATCHes it and the badge goes', async ({ page }) => {
  const world = digestsWorld({ subscriptions: [UNSCOPED_PER_RELEASE_SUB, MISSING_RELEASE_SUB] })
  const { api, errors } = await openRollout(page, '/settings/digests', { handlers: world.handlers, ready })
  const legacy = subRow(page, SUB_IDS.unscoped)
  await expect(legacy.locator('[data-scope-problem="unscoped"]')).toHaveText('Not delivering — choose a release')
  // A Per Release row never prints a raw id: no label, no "Scope: project".
  await expect(summary(legacy)).toHaveText(/^Per Release via email · Delivered: 0 times/)
  const missing = subRow(page, MISSING_RELEASE_SUB.id)
  await expect(missing.locator('[data-scope-problem="release_missing"]')).toHaveText('Not delivering — its release no longer exists')
  await expect(missing).not.toContainText(FOREIGN_RELEASE_IDS[0])

  // The fix is in the row: its own release select and Save (disabled until one is chosen).
  const fix = legacy.locator(`[data-scope-fix="${SUB_IDS.unscoped}"]`)
  const save = fix.getByRole('button', { name: 'Save', exact: true })
  await expect(save).toBeDisabled()
  await fix.locator('[data-digest-release-select]').selectOption({ label: '2026.11' })
  const patch = page.waitForRequest(
    (r) => r.method() === 'PATCH' && new URL(r.url()).pathname === `${DIGEST_SUBSCRIPTIONS_PATH}/${SUB_IDS.unscoped}`,
  )
  await save.click()
  expect((await patch).postDataJSON()).toEqual({ scope_type: 'release', scope_value: RELEASE_ACTIVE_ID })
  await expect(legacy.locator('[data-scope-problem]')).toHaveCount(0)
  await expect(summary(legacy)).toHaveText(/^Per Release · 2026\.11 via email/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.state.writes.map((w) => w.line)).toEqual([`PATCH ${DIGEST_SUBSCRIPTIONS_PATH}/${SUB_IDS.unscoped}`])
  assertHermetic(api, errors)
})

test('a refused fix shows the server\'s sentence in the row', async ({ page }) => {
  const world = digestsWorld({ subscriptions: [UNSCOPED_PER_RELEASE_SUB] })
  // The release went to another project between the read and the save: the server's one 422 text.
  const refuse: (typeof world.handlers)[number] = [
    `${DIGEST_SUBSCRIPTIONS_PATH}/${SUB_IDS.unscoped}`,
    () => respond(422, { detail: BAD_RELEASE }),
    'PATCH',
  ]
  const { api, errors } = await openRollout(page, '/settings/digests', { handlers: [refuse, ...world.handlers], ready })
  const fix = subRow(page, SUB_IDS.unscoped).locator(`[data-scope-fix="${SUB_IDS.unscoped}"]`)
  await fix.locator('[data-digest-release-select]').selectOption({ label: '2026.10' })
  await fix.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(fix.locator('[data-scope-fix-error]')).toHaveText(BAD_RELEASE)
  await expect(fix.locator('[data-scope-problem="unscoped"]')).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('All Projects (an admin): Per Release asks for a project, and cannot be subscribed', async ({ page }) => {
  const world = digestsWorld({ subscriptions: [] })
  const { api, errors } = await openProductionPage(page, '/settings/digests', {
    theme: 'signal',
    now: NOW,
    // All projects is an ADMIN's view (every other role is moved onto its first project).
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers: world.handlers,
    ready,
    mock: { flags: {} },
  })
  await form(page).getByLabel('Digest Name').fill('Release digest')
  await form(page).getByLabel('Schedule').selectOption({ label: 'Per Release' })
  await expect(form(page).locator('[data-release-needs-project]')).toHaveText('Pick a project in the top bar to choose a release.')
  await expect(form(page).getByLabel('Release', { exact: true })).toHaveCount(0)
  await expect(form(page).getByRole('button', { name: 'Subscribe' })).toBeDisabled()
  await networkQuiet(page, api)
  expect(world.state.writes).toEqual([])
  assertHermetic(api, errors)
})
