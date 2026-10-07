/**
 * UX redesign P3 (`02-design-spec.md` §2, the above-the-fold contract), on
 * /reports/summary, hermetic: at 1440 x 900 the page's primary content
 * (results by suite and the per-suite table under it, `[data-primary]`)
 * starts at most 300 px below the top of `#main-content` (the shell's
 * scroller: its top padding and the P1 section tabs count), and the "Results
 * by suite" chart is drawn on the first screen.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after the template can be compared: measured on the unchanged page
 * first (P3 agent B's report, `docs/viz-work/p3-agent-B.md`: 2533 px, the
 * suite chart at 484 px, the per-suite table at 1318 px). After P3: 1954 px,
 * table at 1132; after the P3 cleanup (no donut, the trend collapsed): 1599 px,
 * the suite chart at 298 px, the table at 716 px.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the rollout specs).
 */
import { expect, test, type Page } from '@playwright/test'
import { expectDrawn, MAIN, networkQuiet, openRollout, sectionFrame } from '../lib/rollout'
import { SUMMARY_REPORT_ON } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.getByText('Total tests', { exact: true })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page) {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    const at = (el: Element | null) => (el ? Math.round(el.getBoundingClientRect().top - top) : null)
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: at(primary ?? null),
      suitesChartTop: at(document.querySelector('[data-catalogue-section="summary-suites"]')),
      suiteTableTop: at(document.querySelector('#main-content table')),
      primaries: primaries.length,
    }
  }, MAIN)
}

test('results by suite start within the fold budget at 1440 x 900, under one row of KPIs', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/reports/summary', { handlers: SUMMARY_REPORT_ON, ready })
  const suites = sectionFrame(page, 'summary-suites', 'Results by suite')
  await expectDrawn(suites, 'suites')
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /reports/summary ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // The suite chart and the per-suite table are the primary content; the chart is on the first screen.
  const primary = page.locator('[data-primary]')
  await expect(primary.locator('[data-catalogue-section="summary-suites"]')).toHaveCount(1)
  await expect(primary.getByRole('region', { name: 'Per-suite breakdown table' })).toHaveCount(1)
  await expect(suites).toBeInViewport()
  // ONE KPI row of at most five tiles (the counts strip merged into it); the
  // strip's footer facts are the header's subtitle, on screen in full.
  const kpis = page.locator('section[aria-label="Summary KPIs"]')
  await expect(kpis.locator('[data-metric-card]')).toHaveCount(5)
  await expect(page.getByText('Evaluated', { exact: true })).toHaveCount(0)
  const subtitle = page.locator('[data-page-header] p').first()
  await expect(subtitle).toHaveText(/^Runs in window: 14 · Avg duration: 8m 32s · Latest run: /)
  expect(await subtitle.evaluate((el) => el.scrollWidth <= el.clientWidth), 'the subtitle is not truncated').toBe(true)
  // The duplicate failing-tests chart is gone; its rows are the table's.
  await expect(page.locator('[data-catalogue-section="summary-top-failing"]')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Top failing tests table' })).toHaveCount(1)
  // P3 cleanup: no status donut (the KPI row has its counts); the trend is a
  // collapsed disclosure after the primary content.
  await expect(page.locator('[data-catalogue-section="summary-donut"]')).toHaveCount(0)
  const trend = page.getByRole('button', { name: /^Trend/ })
  await expect(trend).toHaveAttribute('aria-expanded', 'false')
  expect(
    await trend.evaluate((el) =>
      Boolean((document.querySelector('[data-primary]') as Element).compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING),
    ),
    'the trend disclosure follows the primary content',
  ).toBe(true)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('every export is in the header ⋯; Views is the one action beside it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/reports/summary', { handlers: SUMMARY_REPORT_ON, ready })
  const header = page.locator('[data-page-header]')
  await expect(header.getByRole('button', { name: /Export/ })).toHaveCount(0)
  await header.getByRole('button', { name: 'More actions' }).click()
  const items = page.getByRole('menuitem')
  await expect(items).toHaveText([
    'Export PDF',
    'Export Excel',
    'Export PDF in background',
    'Export Excel in background',
    'Analysis report (1d)',
    'Analysis report (7d)',
  ])
  await page.keyboard.press('Escape')
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
