/**
 * W05 REL-08, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.6), on `/releases` as a QA lead:
 *
 *  - a planned release's "Release actions" -> "Set as active release" -> the confirm -> `POST .../activate`;
 *    the ACTIVE pill moves to it from the previously active release; declining the confirm sends nothing;
 *    the active release and a finished one offer no such item;
 *  - "Sync releases" -> GitHub milestones -> Sync: offline, the server's 503 sentence verbatim
 *    (`AI_OFFLINE_MODE is enabled`, once: no global toast repeats it) with "Open Settings › Integrations",
 *    and the list does not change; a 502 shows its sentence with no Settings link; a success toasts the
 *    counts and re-reads the list.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-releases.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond } from '../lib/production-pages'
import { PROJECT_ID } from '../visual/production/fixtures'
import { RELEASE, releasesHandlers, SYNC_OFFLINE } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const card = (page: Page, name: string) => page.getByRole('article', { name: new RegExp(`^${name.replace(/\./g, '\\.')} `) })
const activePill = (page: Page, name: string) => card(page, name).locator('[data-release-active]')
const listReads = (api: Parameters<typeof requestsTo>[0]) => requestsTo(api, '/api/v1/releases').length

test('Set as active release: the confirm, the POST, and the ACTIVE pill moves', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  await expect(activePill(page, '2026.11')).toHaveText('ACTIVE')
  await expect(activePill(page, '2026.12')).toHaveCount(0)
  // The active release and a finished one offer no "Set as active release".
  await expect(card(page, '2026.11').getByRole('button', { name: 'Release actions' })).toHaveCount(0)
  await expect(card(page, '2026.10').getByRole('button', { name: 'Release actions' })).toHaveCount(0)

  const dialogs: string[] = []
  page.on('dialog', (dialog) => {
    dialogs.push(dialog.message())
    void dialog.accept()
  })
  await card(page, '2026.12').getByRole('button', { name: 'Release actions' }).click()
  await page.getByRole('menuitem', { name: 'Set as active release' }).click()

  await expect(page.getByText('"2026.12" is now the active release')).toBeVisible()
  await expect(activePill(page, '2026.12')).toHaveText('ACTIVE')
  await expect(activePill(page, '2026.11')).toHaveCount(0)
  // Clicking the menu did not open the card.
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(dialogs).toEqual(['Make "2026.12" the active release? Runs that no other rule matches will land here.'])
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: `POST /api/v1/releases/${RELEASE.planning}/activate`, body: null }])
  assertHermetic(api, errors)
})

test('declining the confirm sends nothing', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  page.on('dialog', (dialog) => void dialog.dismiss())
  await card(page, '2026.12').getByRole('button', { name: 'Release actions' }).click()
  await page.getByRole('menuitem', { name: 'Set as active release' }).click()
  await networkQuiet(page, api)
  expect(writes).toEqual([])
  await expect(activePill(page, '2026.11')).toHaveText('ACTIVE')
  assertHermetic(api, errors)
})

test('Sync releases offline: the server sentence verbatim, the Settings link, and the list unchanged', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  await networkQuiet(page, api)
  const before = listReads(api)

  await page.locator('[data-page-header]').getByRole('button', { name: 'Sync releases' }).click()
  const dialog = page.getByRole('dialog', { name: 'Sync releases' })
  await expect(dialog).toContainText('Pulls release names and links from the source. Phases, gate policy and run attribution stay in TestLookup.')
  await expect(dialog.getByLabel('GitHub milestones')).toBeChecked()
  await dialog.getByRole('button', { name: 'Sync', exact: true }).click()

  const failure = dialog.locator('[data-sync-error]')
  await expect(failure).toContainText(SYNC_OFFLINE)
  await expect(failure).toHaveAttribute('data-status', '503')
  await expect(failure.getByRole('link', { name: 'Open Settings › Integrations' })).toHaveAttribute('href', '/settings/integrations')
  // Once: the dialog renders it, the global toast does not repeat it.
  await expect(page.getByText(SYNC_OFFLINE)).toHaveCount(1)
  await networkQuiet(page, api)
  expect(writes).toEqual([{ line: 'POST /api/v1/releases/sync', body: { project_id: PROJECT_ID, source: 'github' } }])
  expect(listReads(api), 'nothing changed, nothing re-read').toBe(before)
  await expect(page.getByRole('article')).toHaveCount(3)
  assertHermetic(api, errors)
})

test('a sync that cannot reach GitHub (502) shows its sentence with no Settings link', async ({ page }) => {
  const { handlers } = releasesHandlers({ sync: () => respond(502, { detail: 'Could not reach GitHub: ConnectError' }) })
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  await page.locator('[data-page-header]').getByRole('button', { name: 'Sync releases' }).click()
  const dialog = page.getByRole('dialog', { name: 'Sync releases' })
  await dialog.getByRole('button', { name: 'Sync', exact: true }).click()
  const failure = dialog.locator('[data-sync-error]')
  await expect(failure).toContainText('Could not reach GitHub: ConnectError')
  await expect(failure.getByRole('link')).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a successful sync toasts the counts and re-reads the list', async ({ page }) => {
  const { handlers, writes } = releasesHandlers({ sync: () => ({ source: 'github', created: 1, updated: 2, skipped: 0 }) })
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })
  await networkQuiet(page, api)
  const before = listReads(api)
  await page.locator('[data-page-header]').getByRole('button', { name: 'Sync releases' }).click()
  await page.getByRole('dialog', { name: 'Sync releases' }).getByRole('button', { name: 'Sync', exact: true }).click()
  await expect(page.getByText('Synced from GitHub: 1 created, 2 updated, 0 skipped.')).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Sync releases' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(listReads(api)).toBeGreaterThan(before)
  expect(writes).toHaveLength(1)
  assertHermetic(api, errors)
})
