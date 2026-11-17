/**
 * D-0017 (GPT-6): Settings offered a QA lead **MFA & lockout** and **Retention & purge**, two pages that show a
 * non-admin nothing but "Admin access required". Both items are an administrator's now, like SSO, Feature flags and
 * Review gate; the direct URLs keep their refusals, and the MFA page no longer reads the policy for a non-admin.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). One open per test.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { LAYOUT } from '../visual/production/fixtures'
import { teamChannelsWorld } from '../visual/production/fixtures-in-page-b'
import { ADMIN } from '../visual/production/fixtures-releases'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ADMIN_ONLY = ['retention', 'mfa-policy']
const indexItem = (p: Page, id: string) => p.locator(`[data-settings-index-item="${id}"]`)
const navItem = (p: Page, id: string) => p.locator(`[data-settings-item="${id}"]`)
const writes = (api: Parameters<typeof observed>[0]) => observed(api).filter((line) => !line.startsWith('GET '))

test("D-0017: a QA lead's settings index lists neither MFA & lockout nor Retention & purge", async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings', { handlers: LAYOUT, ready: (p) => p.locator('[data-settings-index]') })
  for (const id of ADMIN_ONLY) await expect(indexItem(page, id)).toHaveCount(0)
  await expect(indexItem(page, 'team-channels')).toHaveCount(1)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test("D-0017: a QA lead's settings sub-nav lists neither", async ({ page }) => {
  const world = teamChannelsWorld()
  const { api, errors } = await openRollout(page, '/settings/team-channels', { handlers: world.handlers, ready: (p) => navItem(p, 'team-channels') })
  for (const id of ADMIN_ONLY) await expect(navItem(page, id)).toHaveCount(0)
  await expect(navItem(page, 'team-channels')).toHaveAttribute('aria-current', 'page')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('D-0017: a QA lead opening MFA & lockout by URL gets its refusal, and the page reads no policy', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/mfa-policy', { handlers: LAYOUT, ready: (p) => p.getByText('Admin access required') })
  await expect(page.getByText('Workspace MFA and lockout policy is restricted to platform administrators.')).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/settings/mfa-policy')).toEqual([])
  expect(writes(api)).toEqual([])
  assertHermetic(api, errors)
})

test('a QA lead opening Retention & purge by URL gets its refusal, and the page reads no policy', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/retention', { handlers: LAYOUT, ready: (p) => p.getByText('Admin access required') })
  await expect(page.getByText('Retention and purge configuration is restricted to platform administrators.')).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, /retention-polic/)).toEqual([])
  expect(writes(api)).toEqual([])
  assertHermetic(api, errors)
})

test('an admin has both items in the index', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings', { handlers: LAYOUT, user: ADMIN, ready: (p) => p.locator('[data-settings-index]') })
  for (const id of ADMIN_ONLY) await expect(indexItem(page, id)).toHaveCount(1)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('an admin has both items in their sub-nav groups', async ({ page }) => {
  const world = teamChannelsWorld()
  const { api, errors } = await openRollout(page, '/settings/team-channels', { handlers: world.handlers, user: ADMIN, ready: (p) => navItem(p, 'team-channels') })
  await expect(page.locator('[data-settings-group="project"] [data-settings-item="retention"]')).toHaveAttribute('href', '/settings/retention')
  await expect(page.locator('[data-settings-group="security"] [data-settings-item="mfa-policy"]')).toHaveAttribute('href', '/settings/mfa-policy')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
