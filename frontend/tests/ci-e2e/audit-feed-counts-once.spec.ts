/**
 * D-0018 (GPT-6): one anonymous report-share view appeared twice under "All categories" on the audit dashboard
 * (once as access, once as report), and the event count counted it twice. Each stored row is now in exactly one
 * category (owner decision Q2): All is the disjoint union, and the count is the real number of rows.
 *
 * The `/audit-dashboard/events` handler answers like the fixed server (`query_unified_audit`): it partitions a
 * two-row ledger by `category`. Like D-0011's spec, this pins the page contract; the backend regression and its
 * Postgres twin are the guards for the server half.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers, ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { LAYOUT, PROJECT_ID } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The server's seven categories (`AUDIT_CATEGORIES`). */
const CATEGORIES = [
  { key: 'access', description: 'Role and membership changes' },
  { key: 'settings', description: 'Configuration and secret changes' },
  { key: 'test_management', description: 'Test case lifecycle actions' },
  { key: 'identity', description: 'SSO, SCIM, and authentication events' },
  { key: 'notification', description: 'Notification dispatch events' },
  { key: 'release', description: 'Release decision overrides' },
  { key: 'report', description: 'Report export and share events' },
]

/** Two stored rows: one anonymous share view (a report event) and one membership change (an access event). */
const LEDGER = [
  { source: 'report', action: 'report_share_accessed', actor_name: 'anonymous_share', actor_id: null, project_id: PROJECT_ID, detail: null, created_at: '2026-09-17T22:19:00Z' },
  { source: 'access', action: 'membership_added', actor_name: 'priya', actor_id: 'u-1', project_id: PROJECT_ID, detail: null, created_at: '2026-09-17T21:05:00Z' },
]

/** Each row under exactly one category; no category is All, their union. */
function events({ url }: ApiRequest) {
  const category = url.searchParams.get('category')
  const items = category ? LEDGER.filter((row) => row.source === category) : LEDGER
  return { total: items.length, items }
}

const WORLD: ApiHandlers = [
  ['/api/v1/audit-dashboard/categories', () => CATEGORIES],
  ['/api/v1/audit-dashboard/events', events],
  ...LAYOUT,
]

const ready = (p: Page) => p.getByRole('heading', { name: 'Audit Dashboard', level: 1 })
const shareViews = (p: Page) => p.getByText('report_share_accessed', { exact: true })
const count = (p: Page) => p.getByText(/^\d+ events$/)
/** The page's category filter (the top bar has comboboxes too). */
const category = (p: Page) => p.locator('select').filter({ has: p.locator('option', { hasText: 'All categories' }) })

test('D-0018: a share view is listed once under All, and All counts each event once', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/audit', { handlers: WORLD, ready })
  await expect(shareViews(page)).toHaveCount(1)
  await expect(count(page)).toHaveText('2 events')

  await category(page).selectOption('report')
  await expect(shareViews(page)).toHaveCount(1)
  await expect(count(page)).toHaveText('1 events')
  await expect(page.getByText('membership_added', { exact: true })).toHaveCount(0)

  await category(page).selectOption('access')
  await expect(shareViews(page)).toHaveCount(0)
  await expect(page.getByText('membership_added', { exact: true })).toHaveCount(1)

  await category(page).selectOption('')
  await expect(shareViews(page)).toHaveCount(1)
  await page.reload()
  await expect(ready(page)).toBeVisible()
  await expect(shareViews(page)).toHaveCount(1)
  await expect(count(page)).toHaveText('2 events')

  await networkQuiet(page, api)
  const asked = requestsTo(api, '/api/v1/audit-dashboard/events').map((line) => new URL(line.split(' ')[1], 'http://x').searchParams.get('category'))
  expect(asked).toEqual(expect.arrayContaining([null, 'report', 'access']))
  assertHermetic(api, errors)
})
