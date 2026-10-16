/**
 * W08 TRND-13 (contract §2.7), hermetic, on `/explore`: small multiples by
 * the five dimensions `chart-data` filters with `facet_dimension` +
 * `facet_value`.
 *
 *   - **Panels: One per branch** → ONE discovery (`executions` by branch x
 *     environment), then as many panels as the fixture has branches (six; the
 *     `(none)` bucket is not a panel and the notice says so), each panel's
 *     request carrying `facet_dimension=branch&facet_value=<its branch>` and no
 *     suite or release filter. No branch request ever names a suite (the
 *     discovery of the previous facet is not read under the new one);
 *   - **Runs** + **Status** is refused beside the picker (the disabled option
 *     carries the reason) and, from a link, reset with the reason: no
 *     status-faceted request is made;
 *   - status panels read as statuses (`Passed`, `Failed`, …).
 *
 * The fixture's `chart-data` refuses what the server refuses (`facet_pair`,
 * `facet_dimension_enum`, `facet_metric_grain`), so a wrong request fails
 * loudly as an error frame. Harness: `tests/lib/rollout.ts` (fail closed).
 * Fixtures: `EXPLORE` (`fixtures-trends-coverage.ts`).
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, expectNoErrorFrame, networkQuiet, openRollout, queryOf } from '../lib/rollout'
import { CHART_DATA_PATH, PROJECT_ID } from '../visual/production/fixtures'
import { EXPLORE, EXPLORE_BRANCHES } from '../visual/production/fixtures-trends-coverage'

// Tall enough that every panel of a 6-panel grid (two rows of three) is near at once.
test.use({ viewport: { width: 1440, height: 1800 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Explorer' })
const panels = (page: Page) => page.locator('[data-explore-panel]')
const chartData = (api: Parameters<typeof queryOf>[0], keep: (q: URLSearchParams) => boolean = () => true) => queryOf(api, CHART_DATA_PATH, keep)
const isDiscovery = (q: URLSearchParams) => !['day', 'week'].includes(q.getAll('group_by')[0] ?? '')

test('One per branch: one discovery, then one request per branch carrying the facet pair; (none) is not a panel', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/explore', { handlers: EXPLORE, ready })
  // The default view first (one panel per suite). Its one refused facet is the series (environment):
  // the option is disabled with the reason as its title, and no refusal line is added under the pickers.
  await expect(panels(page).first()).toBeVisible()
  const environment = page.getByLabel('Panels').locator('option[value="environment"]')
  await expect(environment).toHaveJSProperty('disabled', true)
  await expect(environment).toHaveAttribute('title', 'each panel would show one line')
  await expect(page.locator('[data-explore-refusals]')).toHaveCount(0)
  await networkQuiet(page, api)
  const before = api.seen.length

  await page.getByLabel('Panels').selectOption('branch')
  await expect(page).toHaveURL(/[?&]facet=branch(&|$)/)
  await expect(panels(page)).toHaveCount(EXPLORE_BRANCHES.length)
  await expect(panels(page).locator('[data-chart-state]')).toHaveCount(EXPLORE_BRANCHES.length)
  for (const branch of EXPLORE_BRANCHES) {
    await expect(page.locator(`[data-explore-panel="${branch}"]`).getByRole('heading', { name: branch, exact: true })).toBeVisible()
  }
  await expect(page.getByText('Runs with no branch are not shown.')).toBeVisible()
  await networkQuiet(page, api)
  await expectNoErrorFrame(page)

  const since = { ...api, seen: api.seen.slice(before) }
  const discovery = chartData(since, isDiscovery)
  expect(discovery.map((q) => q.toString())).toEqual([
    new URLSearchParams([['project_id', PROJECT_ID], ['days', '30'], ['metric', 'executions'], ['group_by', 'branch'], ['group_by', 'environment']]).toString(),
  ])
  const panelReads = chartData(since, (q) => !isDiscovery(q))
  expect(panelReads.map((q) => q.get('facet_value')).sort()).toEqual([...EXPLORE_BRANCHES].sort())
  for (const q of panelReads) {
    expect(q.get('facet_dimension')).toBe('branch')
    expect(q.getAll('group_by')).toEqual(['week', 'environment'])
    expect(q.get('metric')).toBe('failure_rate')
    expect(q.has('suite_name'), 'a branch panel is not filtered by suite').toBe(false)
    expect(q.has('release_id'), 'a branch panel is not filtered by release').toBe(false)
  }
  // `(none)` is never asked for.
  expect(panelReads.filter((q) => q.get('facet_value') === '(none)')).toEqual([])
  assertHermetic(api, errors)
})

test('Runs cannot be split by status: the option says why, and a link asking for it is reset with the reason', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/explore?metric=run_count&series=none&facet=status', { handlers: EXPLORE, ready })
  await expect(page.locator('[data-explore-notice]').filter({ hasText: 'Panels "Status" is not available' })).toContainText(
    'one run holds tests of several statuses (or failure categories), so it would be counted in more than one panel',
  )
  // An <option>'s own `disabled` (Playwright's enabled-state check reads the <select>).
  const option = (value: string) => page.getByLabel('Panels').locator(`option[value="${value}"]`)
  await expect(option('status')).toHaveJSProperty('disabled', true)
  await expect(option('status')).toHaveText('One per status')
  await expect(option('status')).toHaveAttribute('title', /so it would be counted in more than one panel/)
  // The reason, in words, beside the picker.
  await expect(page.locator('[data-explore-refusal]')).toHaveText([
    'Panels by status or failure category are not offered: one run holds tests of several statuses (or failure categories), so it would be counted in more than one panel.',
  ])
  await expect(option('failure_category')).toHaveJSProperty('disabled', true)
  await expect(option('branch')).toHaveJSProperty('disabled', false)
  // Choosing Status by hand is not possible either: the picker keeps "One panel".
  await expect(page.getByLabel('Panels')).toHaveValue('none')
  await expect(panels(page)).toHaveCount(1)
  await networkQuiet(page, api)
  expect(chartData(api, (q) => q.has('facet_dimension')), 'no faceted request').toEqual([])
  expect(chartData(api, isDiscovery), 'no discovery for one panel').toEqual([])
  await expectNoErrorFrame(page)
  assertHermetic(api, errors)
})

test('One per status: each panel is a status, titled as one, asked as one', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/explore?metric=executions&series=none&facet=status', { handlers: EXPLORE, ready })
  await expect(panels(page)).toHaveCount(4)
  for (const [key, title] of [['passed', 'Passed'], ['failed', 'Failed'], ['skipped', 'Skipped'], ['broken', 'Broken']]) {
    await expect(page.locator(`[data-explore-panel="${key}"]`).getByRole('heading', { name: title, exact: true })).toBeVisible()
  }
  await networkQuiet(page, api)
  const reads = chartData(api, (q) => !isDiscovery(q))
  expect(reads.map((q) => [q.get('facet_dimension'), q.get('facet_value')]).sort()).toEqual(
    [['status', 'broken'], ['status', 'failed'], ['status', 'passed'], ['status', 'skipped']],
  )
  await expectNoErrorFrame(page)
  assertHermetic(api, errors)
})
