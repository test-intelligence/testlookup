/**
 * The UX redesign P1 shell, on a real page (`/failures`), hermetic.
 *
 * - The sidebar is a list of places (`data-nav-id`): 12 for a QA lead (with
 *   Admin, without the flag-gated Ask AI), 10 for a viewer, and the place that
 *   owns the page is the active one.
 * - A multi-page place shows its pages as tabs above the page.
 * - **?** opens the page's documentation topic beside it, without leaving it;
 *   "Open in full docs" goes to the same topic on `/docs`.
 * - The rail collapses to 64 px, the page widens, and the choice survives a
 *   reload.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, openProductionPage } from '../lib/production-pages'
import { networkQuiet, openRollout } from '../lib/rollout'
import { FAILURES_ON, NOW, PROJECT_ID, USER } from '../visual/production/fixtures'

const ready = (p: Page) => landmark(p, 'Failure verdict')
const navIds = (p: Page) => p.locator('[data-nav-id]').evaluateAll((els) => els.map((el) => el.getAttribute('data-nav-id')))

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('a QA lead: 12 places, Failures active, and the section tabs above the page', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  expect(await navIds(page)).toEqual([
    'home', 'inbox', 'runs', 'failures', 'flaky', 'trends', 'suites', 'test-cases', 'reports',
    'release-gate', 'releases', 'admin',
  ])
  await expect(page.locator('[data-nav-id="failures"]')).toHaveAttribute('aria-current', 'page')
  const tabs = page.locator('[data-section-tabs="failures"] [data-route-tab]')
  await expect(tabs).toHaveText(['Failures', 'Defects', 'Root cause (AI)'])
  await expect(page.locator('[data-route-tab="/failures"]')).toHaveAttribute('aria-current', 'page')
  // A whole-pixel bar: at 37.5 px every page under it sat on a half pixel, and
  // 94 unchanged regions re-rasterised in the P1 baselines.
  const barHeight = await page.locator('[data-section-tabs]').evaluate((el) => el.getBoundingClientRect().height)
  expect(barHeight).toBe(38)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a viewer: 10 places, no Releases, no Admin', async ({ page }) => {
  const viewer = { ...USER, username: 'viewer', full_name: 'Viewer', role: 'VIEWER' }
  const { api, errors } = await openProductionPage(page, '/failures', {
    theme: 'signal',
    now: NOW,
    me: viewer,
    user: viewer,
    projectId: PROJECT_ID,
    handlers: FAILURES_ON,
    ready,
  })
  expect(await navIds(page)).toEqual([
    'home', 'inbox', 'runs', 'failures', 'flaky', 'trends', 'suites', 'test-cases', 'reports', 'release-gate',
  ])
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('? > Help for this page opens the Failures topic beside the page, and Open in full docs goes to it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  await page.getByRole('button', { name: 'Help' }).click()
  await page.getByRole('menuitem', { name: 'Help for this page' }).click()
  const help = page.locator('[data-help-topic="failure-analysis"]')
  await expect(help).toBeVisible()
  await expect(help.getByRole('heading', { name: 'The path a failure takes' })).toBeVisible()
  // Beside the page, not over it: the page is still Failures, still on screen.
  await expect(page).toHaveURL(/\/failures$/)
  await expect(ready(page)).toBeInViewport()

  await page.getByRole('link', { name: /Open in full docs/ }).click()
  await expect(page).toHaveURL(/\/docs\/failure-analysis$/)
  await expect(help).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the rail collapses to 64 px, the page widens, and it stays collapsed after a reload', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  const aside = page.locator('aside[data-sidebar]')
  const main = page.locator('#main-content')
  const wide = (await aside.boundingBox())?.width ?? 0
  const before = (await main.boundingBox())?.width ?? 0
  expect(wide).toBe(224)

  await page.getByRole('button', { name: 'Collapse sidebar' }).click()
  await expect(aside).toHaveAttribute('data-sidebar', 'collapsed')
  await expect.poll(async () => (await aside.boundingBox())?.width).toBe(64)
  expect((await main.boundingBox())?.width ?? 0).toBeGreaterThan(before + 150)
  await expect(page.locator('[data-nav-id="failures"]')).toHaveAttribute('title', 'Failures')

  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(aside).toHaveAttribute('data-sidebar', 'collapsed')
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
