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
 * - HON-09: an unknown path is "Page not found" inside the shell (it used to
 *   redirect to /overview), and the /releases rail's audit link (and the old
 *   `/audit` it pointed at) opens Activity on its Release category.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { FAILURES_ON, LAYOUT, NOW, PROJECT_ID, USER } from '../visual/production/fixtures'
import { RELEASES_PAGE } from '../visual/production/fixtures-pages'

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
  await page.getByRole('button', { name: 'Help', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Help for this page' }).click()
  const help = page.locator('div[data-help-topic="failure-analysis"]')
  await expect(help).toBeVisible()
  await expect(help.getByRole('heading', { name: 'The path a failure takes' })).toBeVisible()
  // Beside the page, not over it: the page is still Failures, still on screen.
  await expect(page).toHaveURL(/\/failures$/)
  await expect(ready(page)).toBeInViewport()

  await page.getByRole('link', { name: /Open in full docs/ }).click()
  // Straight to the Failures section of the topic (helpTopics.ts anchors /failures).
  await expect(page).toHaveURL(/\/docs\/failure-analysis#the-failures-page$/)
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

// ── HON-09: no silent catch-all; /releases' audit link lands somewhere real ──

/** The Activity feed's registry and one release event (`/activity` reads both). */
const ACTIVITY: ApiHandlers = [
  [
    '/api/v1/activity/event-types',
    () => ({ categories: ['runs', 'release', 'configuration'], actor_types: ['user', 'system'], entity_types: ['release'], events: {} }),
  ],
  [
    /^\/api\/v1\/projects\/[^/]+\/activity$/,
    () => ({
      items: [
        {
          id: 'ev-1',
          project_id: PROJECT_ID,
          release_id: null,
          occurred_at: NOW.toISOString(),
          category: 'release',
          event_type: 'release.created',
          actor: { type: 'user', id: null, name: 'Dana Lead', ref: 'dana' },
          entity: { type: 'release', id: 'rel-1', label: '2026.10', href: null },
          target: null,
          summary: 'Release 2026.10 created',
          context: null,
          has_diff: false,
          source: null,
          group_key: null,
        },
      ],
      next_cursor: null,
      ledger_started_at: '2026-09-01T00:00:00Z',
      window: { since: null, until: null },
    }),
  ],
]

test('an unknown path is "Page not found" inside the shell, and the URL stays as typed', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/this-route-does-not-exist', {
    handlers: LAYOUT,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Page not found' }),
  })
  await expect(page).toHaveURL(/\/this-route-does-not-exist$/)
  await expect(page.getByText('This page does not exist.')).toBeVisible()
  await expect(page.getByTestId('not-found-path')).toHaveText('/this-route-does-not-exist')
  // The shell stays: the sidebar's places and the top bar's help button.
  await expect(page.locator('[data-nav-id="home"]')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Help', exact: true })).toBeVisible()
  await expect(page).toHaveTitle('Page not found · TestLookup')
  const links = page.getByRole('navigation', { name: 'Where to go instead' }).getByRole('link')
  await expect(links).toHaveText(['Overview', 'Search', 'Activity'])
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('/releases "View full audit log" opens Activity on its Release category, and so does the old /audit', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/releases', {
    handlers: [...ACTIVITY, ...RELEASES_PAGE],
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Releases' }),
  })
  await page.getByRole('link', { name: /View full audit log/ }).click()
  await expect(page).toHaveURL(/\/activity\?category=release$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Activity' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Filter by category' })).toHaveValue('release')
  await expect(page.getByText('Release 2026.10 created')).toBeVisible()
  await expect.poll(() => requestsTo(api, /\/activity$/).some((line) => line.includes('category=release'))).toBe(true)
  // The feed was never asked for unfiltered on the way.
  expect(requestsTo(api, /\/activity$/).every((line) => line.includes('category=release'))).toBe(true)

  // A bookmark of the old href (it was linked, never routed) forwards the same way.
  await page.goto('/audit')
  await expect(page).toHaveURL(/\/activity\?category=release$/)
  await expect(page.getByRole('combobox', { name: 'Filter by category' })).toHaveValue('release')
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
