/**
 * W23a TRND-03 on Coverage, hermetic (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §0.11, §4.1, §6.3),
 * the score disclosure ("How this score is computed") opened:
 *
 *  - no cadence: the run dimension is `Days with runs` (its days), the gaps say `No expected cadence configured —
 *    Configure` (a link to `/settings/expected-runs` for a QA lead), and neither "No nightly schedule firing" nor
 *    "scheduled runs may be paused" is anywhere on the page;
 *  - a configured cadence: the dimension is `Run cadence` with `4 of 5 expected runs`, and the miss row
 *    `nightly-regression: 1 expected run missed (Wed)`;
 *  - the status is read on load with the page's window (`days=30`); nothing scrolls sideways at 1280.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock: Fri 18 Sep 2026 12:00 UTC). World:
 * `fixtures-insights-cadence.ts` `insightsCadenceWorld` on `COVERAGE_ON` (`page: 'coverage'`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import {
  insightsCadenceWorld,
  worldCadence,
  worldCadenceStatus,
  worldMissedWindow,
  worldStatusItem,
  type InsightsCadenceWorldOptions,
} from '../visual/production/fixtures-insights-cadence'

test.use({ viewport: { width: 1280, height: 1400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const STATUS = `/api/v1/projects/${PROJECT_ID}/run-cadences/status`
const ready = (p: Page) => landmark(p, 'Run cadence')
const verdict = (p: Page) => p.getByRole('region', { name: 'Coverage verdict' })
const gaps = (p: Page) => verdict(p).getByRole('region', { name: 'Coverage gaps' })
const dimension = (p: Page, label: string) =>
  verdict(p).locator('[data-metric-card]').filter({ has: p.getByText(label, { exact: true }) })

async function openScore(page: Page, options: InsightsCadenceWorldOptions = {}) {
  const world = insightsCadenceWorld({ page: 'coverage', ...options })
  const opened = await openRollout(page, '/coverage', { handlers: world.handlers, ready, user: world.user })
  await networkQuiet(page, opened.api)
  await page.getByRole('button', { name: /How this score is computed/ }).click()
  await expect(verdict(page)).toBeVisible()
  return opened
}

test('no cadence: "Days with runs", the Configure link, and no inferred schedule anywhere', async ({ page }) => {
  const { api, errors } = await openScore(page, { role: 'QA_LEAD' })
  await expect(dimension(page, 'Days with runs')).toHaveCount(1)
  await expect(dimension(page, 'Days with runs')).toContainText(/\d+ of 30 days/)
  await expect(dimension(page, 'Run cadence')).toHaveCount(0)
  await expect(gaps(page)).toContainText('No expected cadence configured — Configure')
  await expect(gaps(page).getByRole('link', { name: 'Configure' })).toHaveAttribute('href', '/settings/expected-runs')
  await expect(page.getByText('No nightly schedule firing')).toHaveCount(0)
  await expect(page.getByText('scheduled runs may be paused')).toHaveCount(0)
  expect(requestsTo(api, STATUS).map((line) => line.split('?')[1])).toEqual(['days=30'])
  await expectMainFits(page, 'Coverage score, no cadence')
  assertHermetic(api, errors)
})

test('a configured cadence: "Run cadence" 4 of 5 expected runs, and the miss row', async ({ page }) => {
  const status = worldCadenceStatus([worldStatusItem(worldCadence(), {
    expected: 5, met: 4, missed: 1, missed_windows: [worldMissedWindow(2)],
  })], 30)
  const { api, errors } = await openScore(page, { role: 'QA_ENGINEER', status })
  await expect(dimension(page, 'Run cadence')).toContainText('4 of 5 expected runs')
  await expect(dimension(page, 'Run cadence')).toContainText('80')
  await expect(dimension(page, 'Days with runs')).toHaveCount(0)
  await expect(gaps(page)).toContainText('nightly-regression: 1 expected run missed (Wed)')
  await expect(gaps(page)).not.toContainText('No expected cadence configured')
  await expectMainFits(page, 'Coverage score, configured')
  assertHermetic(api, errors)
})
