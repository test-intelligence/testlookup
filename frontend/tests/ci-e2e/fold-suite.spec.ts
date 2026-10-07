/**
 * UX redesign P4 (`03-implementation-plan.md` § P4 item 3, `02-design-spec.md`
 * §2), hermetic: the suite page `/suites/:id` — one page with the tabs Tests ·
 * Runs · Charts, where the catalog and the `/coverage/suite` analytics were
 * two — and the suites list `/suites` with its run columns.
 *
 * At 1440 x 900 each tab's primary content (`[data-primary]`: the enriched
 * catalog, the recent runs, the run history) starts at most 300 px below the
 * top of `#main-content`. The scroller's `scrollHeight` is printed (FOLD line)
 * so the height before / after can be compared: measured on the unchanged
 * pages first (`docs/viz-work/p4-agent-B.md`: `/suites/:id` 844, its table at
 * 130; `/suites` 844, its table at 99).
 *
 * Also held here, in a browser: the Tests tab is the catalog joined with the
 * window's analytics (a test that did not run reads "—"); the Charts tab loads
 * its chart reads only when opened; `/coverage/suite?name=` lands on the
 * Charts tab with its `?days=` adopted as the window; the list's run columns.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the suite
 * detail fixtures of the visual specs: suite "Auth", four tests in the
 * window, a fifth that never ran, eight recent runs).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, primaryOverflow, requestsTo } from '../lib/rollout'
import {
  HEATMAP_PATH,
  SUITE,
  SUITE_DETAIL_ON,
  SUITE_ID,
  TEST_SCATTER_PATH,
} from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const SUITE_PATH = `/suites/${SUITE_ID}`
const TREND_PATH = `/api/v1/test-management/suites/${SUITE}/trend`

const tablist = (p: Page) => p.getByRole('tablist', { name: 'Suite sections' })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<{ scrollHeight: number; primaryTop: number | null; primaries: number }> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
    }
  }, MAIN)
}

async function expectFold(page: Page, label: string) {
  const geometry = await foldGeometry(page)
  console.log(`FOLD ${label} ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  await expect(page.locator('[data-primary]')).toBeInViewport()
}

test('Tests (default): the enriched catalog starts within the fold budget, and the charts are not asked for', async ({ page }) => {
  const { api, errors } = await openRollout(page, SUITE_PATH, {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-suite-tests]'),
  })
  await networkQuiet(page, api)
  await expectFold(page, SUITE_PATH)
  await expect(tablist(page).getByRole('tab', { name: /^Tests/ })).toHaveAttribute('aria-selected', 'true')
  // Above it: the window and the KPI strip, nothing else.
  await expect(page.getByRole('radiogroup', { name: 'Time window' })).toBeInViewport()
  await expect(page.locator('[data-kpi-strip] [data-metric-card]')).toHaveCount(5)
  // No tile label cut short (the strip's labels are its one truncated line).
  const cut = await page
    .locator('[data-kpi-strip] [data-metric-card] > p:first-child')
    .evaluateAll((labels) => labels.filter((l) => l.scrollWidth > l.clientWidth).map((l) => l.textContent))
  expect(cut, 'KPI labels cut short').toEqual([])
  // The catalog, joined with the window's analytics by fingerprint.
  const primary = page.locator('[data-primary]')
  const failing = primary.locator('[data-test-row="fp-auth-3"]')
  await expect(failing).toContainText('logout clears session')
  await expect(failing).toContainText('FAILED')
  await expect(failing).toContainText('AssertionError: session cookie still present')
  await expect(primary.locator('[data-test-row="fp-auth-2"]')).toContainText('Flaky')
  const neverRan = primary.locator('[data-test-row="fp-auth-catalog-only"]')
  await expect(neverRan).toContainText('password reset email')
  await expect(neverRan.locator('[data-no-run]')).toHaveCount(4)
  // The Charts tab's reads wait for the tab.
  expect(requestsTo(api, TREND_PATH), 'the trend asked before Charts was opened').toEqual([])
  expect(requestsTo(api, HEATMAP_PATH)).toEqual([])
  expect(requestsTo(api, TEST_SCATTER_PATH)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Runs: the recent runs start within the fold budget, each a link to its run', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${SUITE_PATH}?tab=runs`, {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-suite-recent-runs]'),
  })
  await networkQuiet(page, api)
  await expectFold(page, `${SUITE_PATH}?tab=runs`)
  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('heading', { name: 'Recent Runs (8)' })).toBeVisible()
  await expect(primary.getByRole('link', { name: 'View run →' }).first()).toHaveAttribute('href', /^\/runs\/55555555-/)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Charts: opening the tab asks for the trend; the run history starts within the fold budget', async ({ page }) => {
  const { api, errors } = await openRollout(page, SUITE_PATH, {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-suite-tests]'),
  })
  await networkQuiet(page, api)
  expect(requestsTo(api, TREND_PATH)).toEqual([])
  await tablist(page).getByRole('tab', { name: 'Charts' }).click()
  await expect(page).toHaveURL(new RegExp(`${SUITE_PATH}\\?tab=charts$`))
  await expect(page.getByRole('heading', { name: /^Run history — last 30 days/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /^Pass rate trend — last 30 days/ })).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, TREND_PATH), 'opening Charts asked for the trend, once').toHaveLength(1)
  await expectFold(page, `${SUITE_PATH}?tab=charts`)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test("an old /coverage/suite link lands on the suite's Charts tab, its ?days= the window", async ({ page }) => {
  const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=14`, {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
  })
  await expect(page).toHaveURL(new RegExp(`${SUITE_PATH}\\?tab=charts$`))
  await expect(page.getByRole('heading', { name: 'Run history — last 14 days' })).toBeVisible()
  await expect(page.getByRole('radio', { name: '14d' })).toHaveAttribute('aria-checked', 'true')
  await networkQuiet(page, api)
  expect(requestsTo(api, TREND_PATH).every((line) => line.includes('days=14')), 'every trend read at 14 days').toBe(true)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('/suites: the run columns, and the table within the fold budget', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/suites', {
    handlers: SUITE_DETAIL_ON,
    ready: (p) => p.locator('[data-primary] table'),
  })
  await networkQuiet(page, api)
  await expectFold(page, '/suites')
  const headers = await page.locator('[data-primary] thead th').allTextContents()
  expect(headers.map((h) => h.trim())).toEqual(['Name', 'Description', 'Test cases', 'Pass rate', 'Last run', 'Executions', 'Failing', 'Owner', ''])
  const auth = page.locator('[data-primary] tbody tr', { hasText: SUITE })
  await expect(auth.locator('[data-col="pass-rate"]')).toHaveText('75.0%')
  await expect(auth.locator('[data-col="executions"]')).toHaveText('80')
  await expect(auth.locator('[data-col="failing"]')).toHaveText('1')
  await expect(auth.locator('[data-col="owner"]')).toHaveText('QA Lead')
  await expect(auth.locator('[data-col="last-run"]').getByRole('link')).toHaveText('2h ago')
  // The default suite no run reports by name: dashes, not zeros.
  const all = page.locator('[data-primary] tbody tr', { hasText: 'All Tests' })
  await expect(all.getByTitle('No run has reported this suite')).toHaveCount(4)
  // A row opens the suite page.
  await auth.getByText(SUITE, { exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`${SUITE_PATH}$`))
  await expect(page.locator('[data-suite-tests]')).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

// The P4 baselines showed both tables wider than their cards at 1280 px on CI's
// font: the suite's tests lost "Latest run" and the Move action off the right
// edge, and the list broke "All Tests" and "QA Lead" mid-name.
for (const width of [1280, 1440]) {
  test(`at ${width} px the suite's tests and the suites list fit their cards, names on one line`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const tests = await openRollout(page, SUITE_PATH, { handlers: SUITE_DETAIL_ON, ready: (p) => p.locator('[data-suite-tests]') })
    await networkQuiet(page, tests.api)
    const catalog = await primaryOverflow(page)
    console.log(`OVERFLOW ${SUITE_PATH} @${width} ${JSON.stringify(catalog)}`)
    expect(catalog.scrollWidth, "the suite's tests are no wider than their card").toBeLessThanOrEqual(catalog.clientWidth)
    await expect(page.locator('[data-suite-tests]').getByRole('button', { name: /Move/ }).first()).toBeInViewport()

    await page.goto('/suites')
    await expect(page.locator('[data-primary] table')).toBeVisible()
    const list = await primaryOverflow(page)
    console.log(`OVERFLOW /suites @${width} ${JSON.stringify(list)}`)
    expect(list.scrollWidth, 'the suites list is no wider than its card').toBeLessThanOrEqual(list.clientWidth)
    // A name or an owner is one line: an inline box broken across lines has one rect per line.
    const broken = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[data-primary] [data-suite-name], [data-primary] [data-col="owner"] > span'))
        .filter((el) => el.getClientRects().length > 1)
        .map((el) => el.textContent),
    )
    expect(broken, 'names or owners broken across lines').toEqual([])
    expect(tests.errors).toEqual([])
  })
}
