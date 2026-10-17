/**
 * W05 REL-04, hermetic (`docs/deferred-scope-2026-10-13/work/W05-contract.md` §2.4), the `/releases` rail:
 *
 *  - "Compliance packs" lists the pack that EXISTS (`GET /api/v1/compliance-packs`): `412.3 KB ·
 *    sha256:ab12cd34`, the same size and digest the release's own pack panel shows; Download fetches the
 *    authenticated `/compliance-packs/{id}/download` and saves the ZIP;
 *  - "Recent activity" lists the project's release events from the ledger (`category=release&limit=8`), in
 *    their own words, with the person who did it; a row opens `/activity?category=release&event={id}` with
 *    the event's drawer open;
 *  - in All projects, the ledger (per project) is not asked: the panel says so; the packs are every
 *    accessible project's (no project id).
 *
 * The "View full audit log" link is pinned by `shell.spec.ts`. Harness: `tests/lib/rollout.ts` (fail
 * closed, pinned clock). Fixtures: `fixtures-releases.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { openProductionPage } from '../lib/production-pages'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import { ADMIN, RELEASE, RELEASE_PACK, releasesHandlers } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const panel = (page: Page, title: string) => page.locator('section').filter({ has: page.getByRole('heading', { name: title, level: 4 }) })

test('the rail lists the real pack, with the release panel’s size and digest, and Download saves the ZIP', async ({ page }) => {
  const { handlers, writes } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  const packs = panel(page, 'Compliance packs')
  const row = packs.locator(`[data-rail-pack="${RELEASE_PACK.id}"]`)
  await expect(row).toContainText('2026.10 2026.10')
  await expect(row).toContainText('412.3 KB · sha256:ab12cd34 · 9d ago')
  await expect(packs.locator('header')).toContainText('1')
  expect(requestsTo(api, '/api/v1/compliance-packs')).toEqual([`GET /api/v1/compliance-packs?project_id=${PROJECT_ID}&limit=10`])

  const download = page.waitForEvent('download')
  await row.getByRole('button', { name: 'Download' }).click()
  expect((await download).suggestedFilename()).toBe('testlookup-compliance-2026.10-2026-09-09.zip')
  expect(writes).toEqual([{ line: `GET /api/v1/compliance-packs/${RELEASE_PACK.id}/download`, body: null }])

  // The release's own pack panel: the same pack, the same size text, the full digest the rail abbreviates.
  await page.getByRole('article', { name: /^2026\.10 / }).click()
  const detail = opened(page, '2026.10')
  await expect(detail.getByText('412.3 KB · 9 files')).toBeVisible()
  await expect(detail.getByText(`sha256: ${RELEASE_PACK.manifest_sha256}`)).toBeVisible()
  expect(RELEASE_PACK.manifest_sha256.startsWith('ab12cd34')).toBe(true)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('Recent activity is the ledger’s release events, and a row opens the event in Activity', async ({ page }) => {
  const { handlers } = releasesHandlers()
  const { api, errors } = await openRollout(page, '/releases', { handlers, ready })

  const activity = panel(page, 'Recent activity')
  const rows = activity.locator('[data-rail-activity-event]')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toHaveText(/^1h ago\s*Release 2026\.11 was activated · by Priya Nair$/)
  await expect(rows.nth(1)).toContainText('Phase gate Build verification overridden on release 2026.11: Customer waiver CAB-142 · by Dana Whitfield')
  // An API key is not a person: the summary alone.
  await expect(rows.nth(2)).toHaveText(/^3d ago\s*Release 2026\.12 created$/)
  expect(requestsTo(api, /\/activity$/)).toEqual([`GET /api/v1/projects/${PROJECT_ID}/activity?category=release&limit=8`])

  await rows.nth(0).click()
  await expect(page).toHaveURL(/\/activity\?category=release&event=ev-rel-1$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Activity' })).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('Release 2026.11 was activated')
  await networkQuiet(page, api)
  expect(requestsTo(api, `/api/v1/projects/${PROJECT_ID}/activity/ev-rel-1`)).toHaveLength(1)
  assertHermetic(api, errors)
})

test('All projects: the ledger is per project (no request, a prompt); the packs are every accessible project’s', async ({ page }) => {
  const { handlers } = releasesHandlers()
  const { api, errors } = await openProductionPage(page, '/releases', {
    theme: 'signal',
    now: NOW,
    // All projects is an ADMIN's view (every other role is moved onto its first project).
    me: ADMIN,
    user: ADMIN,
    projectId: 'all',
    handlers,
    ready,
    mock: { flags: {} },
  })

  await expect(panel(page, 'Recent activity')).toContainText('Release activity is recorded per project — pick a project to see it.')
  await expect(panel(page, 'Compliance packs').locator(`[data-rail-pack="${RELEASE_PACK.id}"]`)).toContainText('412.3 KB · sha256:ab12cd34')
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/activity$/), 'no ledger request in All projects').toEqual([])
  expect(requestsTo(api, '/api/v1/compliance-packs')).toEqual(['GET /api/v1/compliance-packs?limit=10'])
  expect(RELEASE.shipped).toBe(RELEASE_PACK.release_id)
  assertHermetic(api, errors)
})
