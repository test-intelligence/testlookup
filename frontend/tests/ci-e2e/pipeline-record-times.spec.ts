/**
 * HON-06, hermetic: the settings pages' "Pipeline" disclosures show only what
 * their records say.
 *
 * - /settings/audit: each event in the feed is an audit row at that row's own
 *   `created_at` (the time the table above shows), not one of a ladder of
 *   45-second steps ending at "now". Reloading does not move them.
 * - /settings/integration-health: no "95% / 65% / 25% confidence" badges (a
 *   literal per probe status; a probe measures no confidence).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT, PROJECT_ID } from '../visual/production/fixtures'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const AUDIT_ROWS = [
  { source: 'settings', action: 'update_ai_config', actor_name: 'priya', actor_id: 'u-1', project_id: PROJECT_ID, detail: null, created_at: '2026-09-17T14:32:00Z', success: true },
  { source: 'access', action: 'login', actor_name: 'priya', actor_id: 'u-1', project_id: PROJECT_ID, detail: null, created_at: '2026-09-16T09:05:00Z', success: true },
]

const AUDIT_PAGE = [
  ...LAYOUT,
  ['/api/v1/audit-dashboard/categories', () => [{ key: 'access', description: 'Sign-ins' }, { key: 'settings', description: 'Settings changes' }]],
  ['/api/v1/audit-dashboard/events', () => ({ total: AUDIT_ROWS.length, items: AUDIT_ROWS })],
] as const

async function openPipeline(page: Page) {
  const toggle = page.getByRole('button', { name: 'Pipeline', exact: true })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
}

test('/settings/audit: each pipeline event is an audit row at its own time, stable across a reload', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/settings/audit', {
    handlers: AUDIT_PAGE,
    ready: (p) => p.getByRole('heading', { name: 'Audit Dashboard', level: 1 }),
  })
  // The table lists the rows at their times (compact, UTC).
  await expect(page.getByText('Sep 17, 14:32')).toBeVisible()
  await expect(page.getByText('Sep 16, 09:05')).toBeVisible()

  await openPipeline(page)
  const times = page.locator('[data-event-time]')
  await expect(times).toHaveCount(2)
  const read = () => times.evaluateAll((els) => els.map((el) => [el.getAttribute('data-event-time'), el.textContent]))
  const expected = [
    [new Date(AUDIT_ROWS[0].created_at).toISOString(), 'Sep 17, 14:32'],
    [new Date(AUDIT_ROWS[1].created_at).toISOString(), 'Sep 16, 09:05'],
  ]
  expect(await read()).toEqual(expected)

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Audit Dashboard', level: 1 })).toBeVisible()
  await openPipeline(page)
  await expect(times).toHaveCount(2)
  expect(await read()).toEqual(expected)

  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('/settings/integration-health: the pipeline shows no invented confidence', async ({ page }) => {
  const statuses = [
    { provider: 'jira', status: 'healthy', last_checked_at: '2026-09-18T11:50:00Z', message: null, response_ms: 210, consecutive_failures: 0, last_success_at: '2026-09-18T11:50:00Z' },
    { provider: 'smtp', status: 'down', last_checked_at: '2026-09-18T11:52:00Z', message: 'Timeout', response_ms: 900, consecutive_failures: 3, last_success_at: null },
    { provider: 'slack', status: 'degraded', last_checked_at: '2026-09-18T11:51:00Z', message: 'Slow', response_ms: 1800, consecutive_failures: 0, last_success_at: '2026-09-18T11:51:00Z' },
  ]
  const { api, errors } = await openRollout(page, '/settings/integration-health', {
    handlers: [...LAYOUT, ['/api/v1/integration-health/status', () => statuses]],
    ready: (p) => p.getByRole('heading', { name: 'Integration Health', level: 1 }),
  })
  await openPipeline(page)
  await expect(page.getByText('Health workflow')).toBeVisible()
  // The stages render (not vacuous), with no confidence badge on any.
  await expect(page.getByRole('button', { name: /jira/ }).first()).toBeVisible()
  await expect(page.getByText(/% confidence/)).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
