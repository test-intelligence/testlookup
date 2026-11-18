/**
 * D-0011 (GPT-6 exploratory, E2E-0012 step 3), hermetic: on `/defects` as a Viewer, every count of one defect queue
 * agrees: the verdict banner's P0 count, the status tabs, the table rows and the "P0 / P1 open" KPI tile. GPT-6 saw
 * one OPEN P0 listed twice ("2 P0 blocking") while the server's total and the KPI said 1: `list_defects` joined the
 * many-to-many release link table, so a run linked to two releases listed its defect twice.
 *
 * What a browser cannot prove: the SQL fan-out itself. The authoritative checks are the Postgres test
 * `backend/tests/integration/test_defects_list_fields_postgres.py` (a run with two release links lists its defect
 * once) and the SQL-shape regression `backend/tests/regression/test_defects_list_counts_match_rows.py`. This spec
 * guards the reader's view: when the server lists each defect once, every count on the page says the same.
 * Negative control (run by hand): a list answer that repeats the P0 row with `total` unchanged makes this spec fail.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-defects.ts` (`defectsWorld`).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout } from '../lib/rollout'
import { defectRows } from '../visual/production/fixtures'
import { defectsWorld, VIEWER } from '../visual/production/fixtures-defects'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => landmark(p, 'Defect KPIs')
const table = (page: Page) => page.getByRole('table', { name: 'Defects' })
const statusTab = (page: Page, label: string) =>
  page.getByRole('tablist', { name: 'Status filter' }).getByRole('tab', { name: new RegExp(`^${label}`) })
const tabCount = (page: Page, label: string) => statusTab(page, label).locator('[data-tab-count]')

/** One OPEN P0 (checkout total includes tax), one OPEN P2 (refund webhook retried), one RESOLVED P0 (apply coupon). */
function threeDistinct() {
  const all = defectRows()
  return [all[0], all[4], all[6]]
}

test('one open P0: the banner, the tabs, the rows and the KPI tile all count it once', async ({ page }) => {
  const world = defectsWorld({ rows: threeDistinct() })
  const { api, errors } = await openRollout(page, '/defects', { handlers: world.handlers, ready, user: VIEWER })
  await expect(page.locator('[data-verdict]')).toContainText('1 P0 blocking release')
  await expect(tabCount(page, 'All')).toHaveText('3')
  await expect(tabCount(page, 'Open')).toHaveText('2')
  await expect(tabCount(page, 'Resolved')).toHaveText('1')
  await expect(table(page).locator('tbody tr').filter({ hasText: 'checkout total includes tax' })).toHaveCount(1)
  await expect(table(page).locator('tbody tr')).toHaveCount(3)
  const kpis = page.getByRole('region', { name: 'Defect KPIs' })
  await expect(kpis.locator('[data-metric-card]').filter({ hasText: 'P0 / P1 open' })).toContainText('1')
  await networkQuiet(page, api)
  expect(world.writes, 'a Viewer writes nothing').toEqual([])
  assertHermetic(api, errors)
})
