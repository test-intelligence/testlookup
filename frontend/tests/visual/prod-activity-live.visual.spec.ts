/**
 * W21d ACT-02 (`docs/deferred-scope-2026-10-13/work/W21d-contract.md` §4.1, §6.5): three NEW regions, every theme
 * (`THEMES_WITH_PAPER`):
 *
 *  - `activity-live-on` — /activity as a QA lead with Live ON (remembered in the browser) and the stream live: the
 *    page header, the filter row ending in the Live toggle (accent dot), and the feed's three rows, the streamed one
 *    on top (the world answers every reconnect the same way, so the status stays live while it is shot);
 *  - `activity-live-unavailable` — the same page when the stream answers 503: the filter row and, under it, "Live
 *    updates are unavailable right now; the feed still refreshes every 30 seconds.";
 *  - `inbox-activity` — Inbox › Activity in All Projects (an admin's: the top bar pins anyone else to a project):
 *    the toolbar (window, Show chips, the count line) and one row of each involvement with its chip and project.
 *
 * /activity sits in the settings column (732 px at 1280), so the regions are the page's own root (header + filter
 * row + feed): the toggle and the status line are not wrapped in anything new.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`. World: `fixtures-activity-live.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  assertHermetic,
  landmark,
  openProductionPage,
  PINNED,
  THEMES_WITH_PAPER,
  visualRegion,
} from '../lib/production-pages'
import { NOW, PROJECT_ID, USER } from './production/fixtures'
import { inboxHandlers } from './production/fixtures-pages'
import { activityLiveWorld, defaultFeed, defaultStreamedRow } from './production/fixtures-activity-live'
import { ADMIN } from './production/fixtures-releases'
import { STREAM_ERROR_SENTENCES } from '../../src/types/activityLive'

test.use(PINNED)

const activityReady = (p: Page) => p.getByRole('heading', { level: 1, name: 'Activity' })
/** The page's own root: the parent of its header (header, filter row, status line, feed). */
const pageRoot = (p: Page) => p.locator('[data-page-header]').locator('xpath=..')
const feedRows = (p: Page) => p.getByRole('feed').getByTestId('activity-row')

/** Live ON before the first script runs, as a viewer who turned it on earlier has it. */
async function rememberLiveOn(page: Page) {
  await page.addInitScript(() => localStorage.setItem('testlookup.activity.live', 'on'))
}

for (const theme of THEMES_WITH_PAPER) {
  test(`activity live on — ${theme}`, async ({ page }) => {
    await rememberLiveOn(page)
    const world = activityLiveWorld({ feed: defaultFeed().slice(0, 2), stream: { after: 'repeat' } })
    const { api, errors } = await openProductionPage(page, '/activity', {
      theme, now: NOW, me: USER, user: USER, projectId: PROJECT_ID, handlers: world.handlers, ready: activityReady,
    })
    await expect(page.locator('[data-activity-live]')).toHaveAttribute('data-live-status', 'live')
    await expect(feedRows(page)).toHaveCount(3)
    await expect(feedRows(page).first()).toContainText(defaultStreamedRow().summary)
    await expect(page.locator('[data-activity-live-status]')).toHaveCount(0)
    await visualRegion(page, 'activity-live-on', theme, pageRoot(page))
    assertHermetic(api, errors)
  })

  test(`activity live unavailable — ${theme}`, async ({ page }) => {
    await rememberLiveOn(page)
    const world = activityLiveWorld({
      feed: defaultFeed().slice(0, 2),
      stream: { refuse: { status: 503, code: 'live_unavailable' } },
    })
    const { api, errors } = await openProductionPage(page, '/activity', {
      theme, now: NOW, me: USER, user: USER, projectId: PROJECT_ID, handlers: world.handlers, ready: activityReady,
    })
    await expect(page.locator('[data-activity-live-status]')).toHaveText(STREAM_ERROR_SENTENCES.live_unavailable)
    await expect(page.locator('[data-activity-live]')).toHaveAttribute('data-live-status', 'unavailable')
    await expect(feedRows(page)).toHaveCount(2)
    await visualRegion(page, 'activity-live-unavailable', theme, pageRoot(page))
    assertHermetic(api, errors)
  })

  test(`inbox activity — ${theme}`, async ({ page }) => {
    const world = activityLiveWorld({ role: ADMIN, base: inboxHandlers() })
    const { api, errors } = await openProductionPage(page, '/my-failures?tab=activity', {
      theme, now: NOW, me: ADMIN, user: ADMIN, projectId: 'all', handlers: world.handlers,
      ready: (p) => p.locator('[data-inbox-activity]'),
    })
    const rows = page.locator('[data-inbox-activity]').getByTestId('activity-row')
    await expect(rows).toHaveCount(4)
    await expect(rows.locator('[data-involvement]')).toHaveText(['Mentioned you', 'You', 'Assigned to you', 'Your test case'])
    await expect(page.locator('[data-activity-count]')).toHaveText('4 events in the last 30 days')
    await visualRegion(page, 'inbox-activity', theme, landmark(page, 'Your activity').locator('xpath=..'))
    assertHermetic(api, errors)
  })
}
