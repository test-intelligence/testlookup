/**
 * W23a TRND-03 on Trends › Volume, hermetic (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §0.10, §4.1,
 * §6.3), the run-gap callout (`SchedulePausedCallout`, region `schedpaused`) over the shared set's gap:
 *
 *  - no cadence + a gap: `Run gap in this window`, `Silent days`, NO "Expected runs missed" tile, no `~`, no
 *    `Action needed`; `No expected cadence configured — Configure` linking to `/settings/expected-runs` for a QA lead,
 *    the plain sentence for a QA engineer;
 *  - a configured cadence with two misses: `Expected runs missed`, the tile `2` (exact), the scoped
 *    `nightly-regression: 2 expected runs missed (Tue, Wed)` and `Action needed`;
 *  - a paused cadence: `Expected runs paused`, `… Paused until Fri Sep 25 (release freeze) — set by Alice Lead`;
 *  - the status is read on the Volume tab only (`days=14`, the page's window).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock: Fri 18 Sep 2026 12:00 UTC). World:
 * `fixtures-insights-cadence.ts` `insightsCadenceWorld` on `TRENDS_ON` (`page: 'trends'`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  insightsCadenceWorld,
  pausedCadence,
  worldCadence,
  worldCadenceStatus,
  worldMissedWindow,
  worldStatusItem,
  type InsightsCadenceWorldOptions,
} from '../visual/production/fixtures-insights-cadence'

test.use({ viewport: { width: 1280, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const STATUS = `/api/v1/projects/${PROJECT_ID}/run-cadences/status`
const ready = (p: Page) => landmark(p, 'Trend metrics')
const callout = (p: Page) => p.locator('section[aria-labelledby="schedpaused"]')
const tile = (p: Page, label: string) => callout(p).locator('[data-metric-card]').filter({ hasText: label })

async function openTrends(page: Page, options: InsightsCadenceWorldOptions = {}, path = '/trends') {
  const world = insightsCadenceWorld({ page: 'trends', ...options })
  const opened = await openRollout(page, path, { handlers: world.handlers, ready, user: world.user })
  await networkQuiet(page, opened.api)
  return { ...opened, world }
}

/** Two misses (Tue 15 and Wed 16 Sep, Berlin) on the Mon–Fri 06:00 nightly-regression cadence. */
const twoMisses = () => worldCadenceStatus([worldStatusItem(worldCadence(), {
  expected: 5, met: 3, missed: 2, missed_windows: [worldMissedWindow(2), worldMissedWindow(3)],
})])

test('no cadence, a QA lead: "Run gap in this window", Silent days only, the Configure link; nothing claims a schedule', async ({ page }) => {
  const { api, errors } = await openTrends(page, { role: 'QA_LEAD' })
  await expect(callout(page).getByRole('heading', { name: 'Run gap in this window' })).toBeVisible()
  await expect(tile(page, 'Silent days')).toHaveCount(1)
  await expect(tile(page, 'Expected runs missed')).toHaveCount(0)
  await expect(callout(page)).not.toContainText('~')
  await expect(callout(page)).not.toContainText('Action needed')
  await expect(page.getByText('Schedule appears paused')).toHaveCount(0)
  await expect(callout(page)).toContainText('No expected cadence configured — Configure')
  await expect(callout(page).getByRole('link', { name: 'Configure' })).toHaveAttribute('href', '/settings/expected-runs')
  expect(requestsTo(api, STATUS).map((line) => line.split('?')[1])).toEqual(['days=14'])
  await expectMainFits(page, 'Trends callout, no cadence')
  assertHermetic(api, errors)
})

test('no cadence, a QA engineer: the plain sentence, no link', async ({ page }) => {
  const { api, errors } = await openTrends(page, { role: 'QA_ENGINEER' })
  await expect(callout(page)).toContainText('No expected cadence configured — a QA lead can set one in Settings')
  await expect(callout(page).getByRole('link', { name: 'Configure' })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('a configured cadence with two misses: the exact tile, the scoped miss line, Action needed', async ({ page }) => {
  const { api, errors } = await openTrends(page, { role: 'QA_LEAD', status: twoMisses() })
  await expect(callout(page).getByRole('heading', { name: 'Expected runs missed' })).toBeVisible()
  await expect(tile(page, 'Expected runs missed').locator('p.tabular-nums')).toHaveText('2')
  await expect(callout(page)).not.toContainText('~')
  await expect(callout(page)).toContainText('nightly-regression: 2 expected runs missed (Tue, Wed)')
  await expect(callout(page).locator('[data-callout-action-needed]')).toHaveText(/Action needed/i)
  await expect(callout(page)).not.toContainText('No expected cadence configured')
  await expectMainFits(page, 'Trends callout, two misses')
  assertHermetic(api, errors)
})

test('a paused cadence: "Expected runs paused", until when, why and who; no Action needed', async ({ page }) => {
  const cadence = pausedCadence(worldCadence(), { paused_reason: 'release freeze', paused_by_name: 'Alice Lead' })
  const status = worldCadenceStatus([worldStatusItem(cadence, { expected: 0, met: 0, missed: 0, paused: 3 })])
  const { api, errors } = await openTrends(page, { role: 'QA_LEAD', cadences: [cadence], status })
  await expect(callout(page).getByRole('heading', { name: 'Expected runs paused' })).toBeVisible()
  await expect(callout(page)).toContainText('nightly-regression: Paused until Fri Sep 25 (release freeze) — set by Alice Lead')
  await expect(callout(page).locator('[data-callout-action-needed]')).toHaveCount(0)
  assertHermetic(api, errors)
})

test('the status is read on the Volume tab only', async ({ page }) => {
  const { api, errors } = await openTrends(page, { role: 'QA_LEAD' }, '/trends?tab=by-suite')
  expect(requestsTo(api, STATUS), 'By suite asks no cadence status').toEqual([])
  await page.getByRole('tablist', { name: 'Trend views' }).getByRole('tab', { name: 'Volume', exact: true }).click()
  await expect(callout(page)).toBeVisible()
  await expect.poll(() => requestsTo(api, STATUS).length).toBe(1)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
