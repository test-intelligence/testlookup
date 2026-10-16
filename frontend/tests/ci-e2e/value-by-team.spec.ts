/**
 * W08 TRND-08 (contract §2.4), hermetic, on `/value-metrics`: the header's
 * **Overview · By team** switch (`?view=team`).
 *
 *   - Overview (the default) asks no by-team read; By team asks it ONCE (the
 *     page's project and window) and draws one row per team, **Unassigned**
 *     last, then the footer's two totals: the hours no team owns and the
 *     window headline they add up to (27.5 + 4 = 31.5 h);
 *   - a QA lead's team names link to `/ownership?team=<team>`, where the rules
 *     list keeps that team's only, under a `Team: <team>` chip that clears it;
 *   - a viewer reads the team names as text; a hostile team name is text for
 *     everyone (no element made from it).
 *
 * `fold-value-metrics.spec.ts` and `fold-budget.spec.ts` (850 px) hold the
 * Overview's height UNEDITED: the switch sits in the existing action row.
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures:
 * `VALUE_METRICS` (`teamValueMetricsBody`), `OWNERSHIP_PAGE`.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, expectHostileAsText, networkQuiet, openRollout, queryOf, requestsTo } from '../lib/rollout'
import { HOSTILE_NAME, PROJECT_ID, VALUE_BY_TEAM_PATH, VALUE_METRICS } from '../visual/production/fixtures'
import { OWNERSHIP_PAGE, VIEWER } from '../visual/production/fixtures-trends-coverage'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Value Metrics' })
const HANDLERS = [...OWNERSHIP_PAGE, ...VALUE_METRICS]
const table = (page: Page) => page.getByRole('region', { name: 'Value by team' })
const viewSwitch = (page: Page, name: 'Overview' | 'By team') => page.getByRole('radio', { name, exact: true })
const TEAMS_IN_ORDER = ['Payments', 'Checkout', 'Identity', HOSTILE_NAME, 'Unassigned']

test('By team: one read, a row per team with Unassigned last, and the totals that add up to the headline', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/value-metrics', { handlers: HANDLERS, ready })
  await expect(viewSwitch(page, 'Overview')).toBeChecked()
  await networkQuiet(page, api)
  expect(requestsTo(api, VALUE_BY_TEAM_PATH), 'Overview asks no by-team read').toEqual([])

  // The switch is bound to the URL: a click writes `?view=team` and the page re-renders checked.
  await viewSwitch(page, 'By team').click()
  await expect(page).toHaveURL(/[?&]view=team(&|$)/)
  await expect(viewSwitch(page, 'By team')).toBeChecked()
  await expect(table(page)).toHaveAttribute('data-value-by-team', 'drawn')
  const rows = table(page).locator('[data-team-row]')
  await expect(rows).toHaveCount(TEAMS_IN_ORDER.length)
  expect(await rows.evaluateAll((trs) => trs.map((tr) => tr.getAttribute('data-team-row')))).toEqual(TEAMS_IN_ORDER)
  // Payments: 960 of 1,190 evaluated, 12.5 h.
  await expect(rows.first()).toContainText('80.7%')
  await expect(rows.first()).toContainText('12.5')
  // Identity resolved no defect: its MTTR is not measured, never 0 h.
  await expect(table(page).locator('[data-team-row="Identity"] td').nth(5)).toHaveText('—')
  const totals = table(page).locator('[data-value-by-team-totals]')
  await expect(totals).toContainText(/Not attributable to a team: 4(\.0)? ?h/)
  await expect(totals).toContainText(/Total 31\.5 ?h — the headline for these 30 days/)
  await expectHostileAsText(page, table(page), 'value by team')
  await networkQuiet(page, api)
  const reads = queryOf(api, VALUE_BY_TEAM_PATH)
  expect(reads, 'one by-team read').toHaveLength(1)
  expect(reads[0].get('project_id')).toBe(PROJECT_ID)
  expect(reads[0].get('days')).toBe('30')
  assertHermetic(api, errors)
})

test('a QA lead\'s team name opens its ownership rules, filtered, with a chip that clears it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/value-metrics?view=team', { handlers: HANDLERS, ready })
  const link = table(page).locator('[data-team-row="Payments"]').getByRole('link', { name: 'Payments', exact: true })
  await expect(link).toHaveAttribute('href', '/ownership?team=Payments')
  // Unassigned never links.
  await expect(table(page).locator('[data-team-row="Unassigned"]').getByRole('link')).toHaveCount(0)
  await link.click()
  await expect(page).toHaveURL(/\/ownership\?team=Payments$/)
  await expect(page.getByText('Team: Payments')).toBeVisible()
  await expect(page.getByText('payments*', { exact: true })).toBeVisible()
  await expect(page.getByText('checkout*', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Show all' }).click()
  await expect(page).not.toHaveURL(/[?&]team=/)
  await expect(page.getByText('checkout*', { exact: true })).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('a viewer reads the team names as text', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/value-metrics?view=team', { handlers: HANDLERS, ready, user: VIEWER })
  await expect(table(page)).toHaveAttribute('data-value-by-team', 'drawn')
  await expect(table(page).locator('[data-team-row]')).toHaveCount(TEAMS_IN_ORDER.length)
  await expect(table(page).getByRole('link')).toHaveCount(0)
  await expect(table(page).locator('[data-team-row="Payments"]')).toContainText('Payments')
  await expectHostileAsText(page, table(page), 'value by team, viewer')
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
