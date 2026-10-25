/**
 * W13 TCL-03 "library health over the whole library on the server; strategy gaps; the review queue; Flaky and active
 * and No owner" (`docs/deferred-scope-2026-10-13/work/W13-contract.md` §2.3, §4.2), hermetic, 1440 × 900:
 *
 *  - as a QA LEAD with one project: the banner and Insights from ONE `GET /test-management/health?project_id=<P>`
 *    (no `size=200` roll any more); opening Insights sends NOTHING; the four component lines with both numbers;
 *  - Strategy gaps › Flaky and active: **Show 5** lists the answer's five (no request), **Show all 30** pages
 *    `GET /cases/evidence-gaps?kind=flaky_active&page=&size=25` (asked only then), a row opens its case (`?case=`);
 *  - **Change target** → 75 → ONE `PUT /projects/<P>` `{ automation_target_pct: 75 }`, the health re-read, the gap
 *    `… target 75 %`; a QA ENGINEER has no Change target;
 *  - All Projects (an admin): no health read; the banner says why.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `testLibraryHealthWorld()`
 * (`fixtures-test-library.ts`, over `TEST_CASES`).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened, watchNativeDialogs } from '../lib/inPage'
import { cardByHeading, openProductionPage } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID } from '../visual/production/fixtures'
import {
  ADMIN,
  CASES_API,
  EVIDENCE_GAPS_API,
  FLAKY_TOTAL,
  flakyCaseId,
  flakyCaseTitle,
  HEALTH_API,
  LIBRARY_TEXT,
  LIBRARY_USERS,
  PROJECT_API,
  testLibraryHealthWorld,
  type LibraryRole,
} from '../visual/production/fixtures-test-library'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const tmReady = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })
const banner = (p: Page) => p.locator('[data-status-banner]').first()
const insightsButton = (p: Page) => p.getByRole('button', { name: /^Insights/ })

async function open(page: Page, role: LibraryRole = 'QA_LEAD') {
  const world = testLibraryHealthWorld({ role })
  const opened_ = await openRollout(page, '/test-management', { handlers: world.handlers, ready: tmReady, user: LIBRARY_USERS[role] })
  await expect(banner(page)).toContainText('Library health')
  return { world, ...opened_ }
}

test('TCL-03 one health read per page view: the banner facts and Insights from it; no 200-row roll; Insights sends nothing', async ({ page }) => {
  watchNativeDialogs(page)
  const { api, errors } = await open(page)
  await networkQuiet(page, api)

  const healthReads = requestsTo(api, HEALTH_API)
  expect(healthReads, 'ONE health read on load, for the project').toEqual([`GET ${HEALTH_API}?project_id=${PROJECT_ID}`])
  expect(requestsTo(api, CASES_API).filter((l) => l.includes('size=200')), 'the 200-row roll is gone').toEqual([])
  for (const fact of ['Cases', 'Awaiting review', 'Automated', 'Stale drafts']) await expect(banner(page)).toContainText(fact)
  await expect(banner(page)).toContainText('Library health 68/100')
  await expect(banner(page)).toContainText('Cases 8')
  await expect(banner(page)).toContainText('Awaiting review 2')
  await expect(banner(page)).toContainText('Automated 38%')
  await expect(banner(page)).toContainText('Stale drafts 0')

  const from = observed(api).length
  await insightsButton(page).click()
  const insights = opened(page, 'Insights')
  await expect(insights.locator('[data-insights] > *')).toHaveCount(5)
  const components = insights.locator('[data-health-components] li')
  await expect(components).toHaveCount(4)
  await expect(components.first()).toContainText('3 of 8 cases automated (target 60 %)')
  await expect(cardByHeading(insights, /^Review queue/)).toContainText('Review queue · 2')
  await networkQuiet(page, api)
  expect(observed(api).slice(from), 'Insights sends nothing: every card reads the health answer or the audit roll').toEqual([])
  assertHermetic(api, errors)
})

test('TCL-03 Flaky and active: Show 5 from the answer, Show all pages the evidence-gap route (asked only then), a row opens its case', async ({ page }) => {
  const { world, api, errors } = await open(page)
  await insightsButton(page).click()
  const gaps = cardByHeading(opened(page, 'Insights'), 'Strategy gaps')
  const flaky = gaps.locator('[data-strategy-gap="flaky_active"]')
  await expect(flaky).toContainText('Flaky and active')
  await expect(flaky).toContainText(`${FLAKY_TOTAL} flaky`)
  await networkQuiet(page, api)

  await flaky.getByRole('button', { name: 'Show 5', exact: true }).click()
  await expect(flaky.locator('[data-library-case]')).toHaveCount(5)
  await networkQuiet(page, api)
  expect(requestsTo(api, EVIDENCE_GAPS_API).filter((l) => l.includes('kind=flaky_active')), 'Show 5 asks nothing').toEqual([])

  await flaky.getByRole('button', { name: `Show all ${FLAKY_TOTAL}`, exact: true }).click()
  await expect(flaky.locator('[data-library-case-page="1"] [data-library-case]')).toHaveCount(25)
  await expect(flaky.locator('[data-library-case-range]')).toHaveText(`1-25 of ${FLAKY_TOTAL}`)
  await flaky.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(flaky.locator('[data-library-case-page="2"] [data-library-case]')).toHaveCount(FLAKY_TOTAL - 25)
  await networkQuiet(page, api)
  const pages = world.reads.filter((r) => r.line.startsWith(`GET ${EVIDENCE_GAPS_API}?`))
    .map((r) => new URL(r.line.slice(4), 'http://x').searchParams)
    // The Cases tab's own evidence lists (never_executed / automation_vanished) read on load; this list's pages only.
    .filter((q) => q.get('kind') === 'flaky_active')
    .map((q) => [q.get('project_id'), q.get('kind'), q.get('page'), q.get('size')])
  expect(pages).toEqual([[PROJECT_ID, 'flaky_active', '1', '25'], [PROJECT_ID, 'flaky_active', '2', '25']])

  await flaky.getByRole('button', { name: flakyCaseTitle(26), exact: true }).click()
  await expect(page.locator('[data-case-panel]').getByRole('heading', { name: flakyCaseTitle(26) })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('case')).toBe(flakyCaseId(26))
  assertHermetic(api, errors)
})

test('TCL-03 Change target (a QA lead): ONE PUT of the field, the health re-read, the gap reads the new target; none for a QA engineer', async ({ page }) => {
  const { world, api, errors } = await open(page)
  await insightsButton(page).click()
  const insights = opened(page, 'Insights')
  await expect(insights).toContainText('target 60%')
  await networkQuiet(page, api)
  const readsBefore = requestsTo(api, HEALTH_API).length

  await insights.getByRole('button', { name: 'Change target', exact: true }).click()
  await insights.getByLabel('Automation target (%)').fill('75')
  await insights.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(cardByHeading(insights, 'Strategy gaps').locator('[data-strategy-gap="automation_below_target"]')).toContainText('38 % automated · target 75 %')
  await networkQuiet(page, api)
  expect(world.writes.filter((w) => w.line.startsWith('PUT '))).toEqual([{ line: `PUT ${PROJECT_API}`, body: { automation_target_pct: 75 } }])
  expect(requestsTo(api, HEALTH_API).length, 'the health is re-read after the save').toBeGreaterThan(readsBefore)
  assertHermetic(api, errors)
})

test('TCL-03 a QA engineer reads the target but has no Change target', async ({ page }) => {
  const { api, errors } = await open(page, 'QA_ENGINEER')
  await insightsButton(page).click()
  const insights = opened(page, 'Insights')
  await expect(insights).toContainText('target 60%')
  await expect(insights.getByRole('button', { name: 'Change target' })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('TCL-03 All Projects (an admin): no health read; the banner says library health is per project', async ({ page }) => {
  const world = testLibraryHealthWorld({ role: 'ADMIN' })
  const { api, errors } = await openProductionPage(page, '/test-management', {
    theme: 'signal', now: NOW, me: ADMIN, user: ADMIN, projectId: 'all', handlers: world.handlers, ready: tmReady,
  })
  await expect(banner(page)).toContainText(LIBRARY_TEXT.healthNeedsProject)
  await networkQuiet(page, api)
  expect(requestsTo(api, HEALTH_API), 'All Projects asks no health').toEqual([])
  assertHermetic(api, errors)
})
