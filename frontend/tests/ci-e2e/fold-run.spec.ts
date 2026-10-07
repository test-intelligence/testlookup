/**
 * UX redesign P4 (`03-implementation-plan.md` § P4 item 1, `02-design-spec.md`
 * §5 "Run"), on `/runs/:runId`, hermetic: one Run page with four tabs —
 * Tests (default) · Analysis · Changes · Evidence.
 *
 * The fold (P3 template, §2): at 1440 x 900 the primary content — the test
 * table, `[data-primary]` — starts at most 300 px below the top of
 * `#main-content` (the shell's scroller: its padding and the section tabs
 * count). The scroller's `scrollHeight` is printed (FOLD line) to compare the
 * page's height before / after: measured on the unchanged page first
 * (`docs/viz-work/p4-agent-A.md`: 1977 px, the test table at 319 px, no
 * `[data-primary]`).
 *
 * Also held here, in a browser:
 *  - the table lists failures first, and the counts are status chips;
 *  - a tab that is not open asks for nothing: at load there is no request
 *    for the run's AI analysis, deep investigation, regression diff or
 *    pipelines; opening each tab makes exactly its requests;
 *  - the old URLs of the merged pages land on their tab.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock); the run page's
 * fixtures are `tests/visual/production/fixtures-run.ts` (`RUN_PAGE`).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY, RUN_PAGE } from '../visual/production/fixtures-run'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.locator('[data-primary] table')

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

const RUN = `/api/v1/runs/${RUN_ID}`
/** What only the non-default tabs ask for. */
const TAB_ONLY = {
  analysis: [new RegExp(`^${RUN}/intelligence$`), /^\/api\/v1\/deep-investigate\//],
  changes: [new RegExp(`^${RUN}/regression-diff$`)],
  evidence: [/^\/api\/v1\/agents\/pipelines/, /^\/api\/v1\/agents\/runs\/[^/]+\/summary$/],
}

test('the test table starts within the fold budget at 1440 x 900, failures first, and no closed tab asks for anything', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: RUN_PAGE, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /runs/:id ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)

  // One header, its tab bar, then the chips and the table.
  await expect(page.locator('[data-page-header]')).toHaveCount(1)
  await expect(page.getByRole('heading', { level: 1, name: 'Run #240' })).toBeVisible()
  const tabs = page.getByRole('tablist', { name: 'Run sections' })
  await expect(tabs.getByRole('tab', { name: /Tests/ })).toHaveAttribute('aria-selected', 'true')
  const chips = page.getByRole('group', { name: 'Filter by status' }).getByRole('button')
  await expect(chips).toHaveText(['All 197', '12 failed', '3 broken', '180 passed', '2 skipped'])

  // Failures first: the first rows of the first page are the broken and failed tests, on screen.
  const primary = page.locator('[data-primary]')
  const firstRows = primary.getByRole('row')
  await expect(firstRows.nth(1)).toBeInViewport()
  for (let i = 1; i <= 15; i++) await expect(firstRows.nth(i)).toContainText(/Broken|Failed/i)
  await expect(firstRows.nth(16)).toContainText(/Passed/i)

  // Nothing of the other tabs was asked for.
  for (const path of Object.values(TAB_ONLY).flat()) expect(requestsTo(api, path), String(path)).toEqual([])
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a status chip filters the table and stays in the URL', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: RUN_PAGE, ready })
  await page.getByRole('button', { name: '3 broken' }).click()
  await expect(page.getByRole('button', { name: '3 broken' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[data-primary]').getByRole('row')).toHaveCount(4)
  await expect(page).toHaveURL(/status=BROKEN/)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('each tab renders its section and makes its own requests only when opened', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: RUN_PAGE, ready })
  await networkQuiet(page, api)
  const tabs = page.getByRole('tablist', { name: 'Run sections' })

  // Analysis: the verdict in one line, What failed as the primary content, then the clusters.
  await tabs.getByRole('tab', { name: 'Analysis' }).click()
  await expect(page).toHaveURL(/[?&]tab=analysis/)
  await expect(page.locator('[data-status-banner]')).toHaveAttribute('data-status-banner', 'conditional')
  await expect(page.locator('[data-primary]')).toHaveAttribute('aria-label', 'What failed')
  await expect(page.locator('[data-primary]')).toContainText('Gateway timeouts')
  const deep = page.getByRole('region', { name: 'Deep investigation' })
  await expect(deep).toContainText('Payment gateway timeout')
  await expect(deep.getByRole('button', { name: /Analyze failures/ })).toBeVisible()
  await expect(page.locator('[data-disclosure][data-open="false"]')).toHaveCount(2)
  await networkQuiet(page, api)
  for (const path of TAB_ONLY.analysis) expect(requestsTo(api, path).length, String(path)).toBeGreaterThan(0)
  expect(requestsTo(api, TAB_ONLY.changes[0])).toEqual([])

  // Changes: the regression diff, and the way into Compare.
  await tabs.getByRole('tab', { name: 'Changes' }).click()
  await expect(page).toHaveURL(/[?&]tab=changes/)
  await expect(page.locator('[data-primary]')).toContainText('Baseline: Build #238')
  await expect(page.getByRole('link', { name: 'Open Compare' })).toHaveAttribute('href', `/runs/compare?left=${RUN_ID}`)
  expect(requestsTo(api, TAB_ONLY.changes[0])).toHaveLength(1)

  // Evidence: the AI report of the run's pipeline.
  await tabs.getByRole('tab', { name: 'Evidence' }).click()
  await expect(page).toHaveURL(/[?&]tab=evidence/)
  await expect(page.getByRole('region', { name: 'AI report' })).toContainText(AI_REPORT_SUMMARY)
  await networkQuiet(page, api)
  for (const path of TAB_ONLY.evidence) expect(requestsTo(api, path).length, String(path)).toBeGreaterThan(0)

  // Back to Tests: the clean URL.
  await tabs.getByRole('tab', { name: /Tests/ }).click()
  await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}$`))
  await expect(ready(page)).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the old URLs of the merged pages land on their tab', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/runs/${RUN_ID}`, { handlers: RUN_PAGE, ready })
  for (const [old, tab, sentinel] of [
    [`/runs/${RUN_ID}/intelligence`, 'analysis', page.locator('[data-status-banner]')],
    [`/deep-investigate/${RUN_ID}`, 'analysis', page.getByRole('region', { name: 'Deep investigation' })],
    [`/agents/run/${RUN_ID}`, 'evidence', page.getByRole('region', { name: 'AI report' })],
  ] as const) {
    await page.goto(old)
    await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}\\?tab=${tab}$`))
    await expect(page.getByRole('tablist', { name: 'Run sections' }).getByRole('tab', { selected: true })).toHaveAttribute('data-tab', tab)
    await expect(sentinel).toBeVisible()
  }
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
