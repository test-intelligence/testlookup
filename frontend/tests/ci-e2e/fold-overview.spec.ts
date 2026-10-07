/**
 * Home's fold budget (UX redesign P3, design spec §2): at 1440 x 900 the
 * primary content (`[data-primary]`: the pass-rate trend beside the status
 * donut) starts at most 300 px below the top of `#main-content` — measured
 * from the scroller's own top, so its padding and anything above the page
 * count. Above it: the header, the one-line verdict banner and the five KPI
 * tiles, nothing else.
 *
 * `#main-content`'s scrollHeight is recorded (an annotation and the log):
 * 1734 px before P3 on this fixture, with the trend's top at 570 px; 1215 px
 * after P3; 1204 px after the P3 cleanup (the lazy row in the "Top failing"
 * tab, "Recent activity" the "Activity" tab), the primary still at 260 px.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { expectDrawn, MAIN, networkQuiet, openRollout, sectionFrame } from '../lib/rollout'
import { OVERVIEW_ON } from '../visual/production/fixtures'

/** Spec §2: the primary content's top, px below `#main-content`'s top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.getByRole('heading', { level: 1, name: 'Dashboard' })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('Home: the primary content starts within 300 px of the content top at 1440 x 900', async ({ page }) => {
  const { api } = await openRollout(page, '/overview', { handlers: OVERVIEW_ON, ready })
  await expectDrawn(sectionFrame(page, 'overview-trend', 'Pass rate trend'), 'trend')
  await expectDrawn(sectionFrame(page, 'overview-donut', 'Status breakdown'), 'donut')
  await networkQuiet(page, api)

  const primary = page.locator('[data-primary]')
  await expect(primary).toHaveCount(1)
  // The primary block is the trend row alone: the catalogue's lazy row is the Top failing tab's.
  await expect(primary.locator('[data-catalogue-section="overview-trend"]')).toHaveCount(1)
  await expect(primary.locator('[data-catalogue-section="overview-donut"]')).toHaveCount(1)
  await expect(primary.locator('[data-catalogue-section="overview-top-failing"], [data-lazy-section]')).toHaveCount(0)
  // Above it: one banner and one strip of five tiles (spec §5, Home).
  await expect(page.locator('[data-status-banner]')).toHaveCount(1)
  await expect(page.locator('[data-kpi-strip] [data-metric-card="compact"]')).toHaveCount(5)

  const m = await page.evaluate((sel) => {
    const main = document.querySelector(sel) as HTMLElement
    const top = main.getBoundingClientRect().top
    const first = document.querySelector('[data-primary]') as HTMLElement
    return {
      primaryTop: Math.round(first.getBoundingClientRect().top - top),
      scrollHeight: main.scrollHeight,
      clientHeight: main.clientHeight,
    }
  }, MAIN)
  test.info().annotations.push({ type: 'fold', description: `/overview ${JSON.stringify(m)}` })
  console.log(`[fold] /overview ${JSON.stringify(m)}`)
  expect(m.primaryTop, 'the primary content top, px below #main-content').toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // Nothing in-page that is secondary (a tab bar, a disclosure) comes before it.
  const order = await page.evaluate(() => {
    const first = document.querySelector('[data-primary]') as HTMLElement
    const secondary = Array.from(document.querySelectorAll('#main-content [data-disclosure], #main-content [data-tabs]'))
    return secondary.map((el) => Boolean(first.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING))
  })
  expect(order.length, 'the page has secondary sections').toBeGreaterThan(0)
  expect(order.every(Boolean), 'every disclosure/tab bar follows the primary content').toBe(true)
})
