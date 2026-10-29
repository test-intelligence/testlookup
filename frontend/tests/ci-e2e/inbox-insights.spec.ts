/**
 * W23a FAIL-11 in the Inbox, hermetic (`docs/deferred-scope-2026-10-13/work/W23a-contract.md` §2.1 D-2, §4.1,
 * §6.3), `/my-failures`, its FOURTH tab:
 *
 *  - four tabs, Insights LAST, with no count; an Inbox load asks for NO insights (the Activity precedent);
 *  - opened: one read (`range=7d`, `include_dismissed=false`, `limit=100`), the rows, and `Showing n of total` from
 *    the server's `total`;
 *  - `Show dismissed (1)` re-reads with `include_dismissed=true` and shows the dismissed row with
 *    `Dismissed by Alice Lead`;
 *  - All Projects (an ADMIN's view): the project prompt, and no insights request;
 *  - nothing scrolls sideways at 1280.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-insights-cadence.ts`
 * `insightsCadenceWorld` on the Inbox handlers (`page: 'inbox'`).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectMainFits } from '../lib/inPageGeometry'
import { openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import { ADMIN } from '../visual/production/fixtures-releases'
import { insightsCadenceWorld, worldInsight, worldInsightRows } from '../visual/production/fixtures-insights-cadence'

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const INSIGHTS = `/api/v1/projects/${PROJECT_ID}/insights`
const ready = (p: Page) => p.locator('[data-page-header] h1')
const insightsTab = (p: Page) => p.getByRole('tab', { name: /^Insights/ })
const panel = (p: Page) => p.locator('[data-inbox-insights]')
const rows = (p: Page) => panel(p).locator('[data-insight-row]')
const insightQueries = (api: Parameters<typeof requestsTo>[0]) =>
  requestsTo(api, INSIGHTS).map((line) => {
    const q = new URL(line.slice(line.indexOf(' ') + 1), 'http://inbox.test').searchParams
    return [q.get('range'), q.get('include_dismissed'), q.get('limit')]
  })
const pathAndSearch = (p: Page) => {
  const url = new URL(p.url())
  return `${url.pathname}${url.search}`
}

/** The world's three rows and one row Alice Lead dismissed an hour ago. */
function rowsWithOneDismissed() {
  const dismissed = worldInsight({
    kind: 'cost_budget', severity: 'medium', run_path: null, title: 'AI spend is at 85 % of the $10 monthly cap',
    subject_label: 'budget', link: { path: '/settings/billing', label: 'LLM cost budget' },
    dismissed_at: new Date(NOW.getTime() - 3_600_000).toISOString(), dismissed_by_name: 'Alice Lead',
  })
  return [...worldInsightRows(), dismissed]
}

test('four tabs, Insights last with no count; an Inbox load asks no insights; opened: one read and Showing n of total', async ({ page }) => {
  const world = insightsCadenceWorld({ page: 'inbox', insights: rowsWithOneDismissed() })
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: world.handlers, ready, user: world.user })
  await networkQuiet(page, api)
  const tabs = page.getByRole('tablist', { name: 'Inbox' }).getByRole('tab')
  await expect(tabs).toHaveCount(4)
  await expect(tabs.nth(3)).toHaveText('Insights')
  await expect(tabs.nth(2)).toHaveText('Activity')
  expect(requestsTo(api, INSIGHTS), 'the default tab reads no insights').toEqual([])

  await insightsTab(page).click()
  await expect.poll(() => pathAndSearch(page)).toBe('/my-failures?tab=insights')
  await expect(rows(page)).toHaveCount(3)
  await expect(panel(page).locator('[data-insights-showing]')).toHaveText('Showing 3 of 3')
  await expect(page.locator('[data-page-header]')).toContainText('Patterns across runs in Checkout:')
  await expect(insightsTab(page)).toHaveText('Insights')
  await networkQuiet(page, api)
  expect(insightQueries(api)).toEqual([['7d', 'false', '100']])
  await expectMainFits(page, 'Inbox › Insights at 1280')
  assertHermetic(api, errors)
})

test('Show dismissed (1) re-reads with include_dismissed and shows who dismissed it', async ({ page }) => {
  const world = insightsCadenceWorld({ page: 'inbox', insights: rowsWithOneDismissed() })
  const { api, errors } = await openRollout(page, '/my-failures?tab=insights', { handlers: world.handlers, ready, user: world.user })
  await expect(rows(page)).toHaveCount(3)
  await expect(panel(page).getByText('Dismissed by Alice Lead', { exact: false })).toHaveCount(0)
  await panel(page).getByRole('button', { name: 'Show dismissed (1)' }).click()
  await expect(rows(page)).toHaveCount(4)
  const dismissed = panel(page).locator('[data-insight-row]', { hasText: 'AI spend is at 85 % of the $10 monthly cap' })
  await expect(dismissed.locator('[data-insight-meta]')).toHaveText(/^Dismissed by Alice Lead · /)
  await expect(dismissed.getByRole('button', { name: 'Dismiss' })).toHaveCount(0)
  await expect(panel(page).locator('[data-insights-showing]')).toHaveText('Showing 4 of 4')
  await networkQuiet(page, api)
  expect(insightQueries(api)).toEqual([['7d', 'false', '100'], ['7d', 'true', '100']])
  assertHermetic(api, errors)
})

test('All Projects: the project prompt and no insights request', async ({ page }) => {
  const world = insightsCadenceWorld({ page: 'inbox', role: 'ADMIN', insights: rowsWithOneDismissed() })
  const { api, errors } = await openProductionPage(page, '/my-failures?tab=insights', {
    theme: 'signal', now: NOW, me: ADMIN, user: ADMIN, projectId: 'all', handlers: world.handlers, ready,
  })
  await expect(page.getByRole('heading', { name: 'Select a project' })).toBeVisible()
  await expect(page.getByText('Insights belong to one project: pick it to see them.')).toHaveCount(2)
  await networkQuiet(page, api)
  expect(requestsTo(api, /\/insights$/)).toEqual([])
  assertHermetic(api, errors)
})
