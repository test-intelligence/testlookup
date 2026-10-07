/**
 * UX redesign P3 (the page template), /coverage at 1440 x 900: the page's one
 * primary content (`[data-primary]`, the suite coverage table) starts within
 * 300 px of the top of `#main-content` — the section tabs above the page, the
 * header and the KPI strip included. Measured on the scroller (the shell
 * never scrolls the window), at load, nothing scrolled.
 *
 * The page's height and the table's top are recorded as annotations
 * (`main-scroll-height`, `primary-top`) for the P3 report
 * (`docs/viz-work/p3-agent-D.md`): before P3 the page was 2,059 px tall and
 * the suite table started at 607 px (verdict card and KPI cells above it);
 * after, 1,350 px and 254 px.
 * Harness: `tests/lib/production-pages.ts` (fail-closed mocks).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { COVERAGE_ON } from '../visual/production/fixtures'

/** The template's fold budget, px below the top of `#main-content` (spec §2). */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => landmark(p, 'Run cadence')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test('Coverage: the suite table starts within 300 px of the content top, before every tab bar and disclosure', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/coverage', { handlers: COVERAGE_ON, ready })
  await networkQuiet(page, api)
  const main = page.locator(MAIN)
  test.info().annotations.push({ type: 'main-scroll-height', description: String(await main.evaluate((el) => el.scrollHeight)) })

  const primary = page.locator('[data-primary]')
  await expect(primary, 'one primary content').toHaveCount(1)
  await expect(primary.getByRole('table', { name: 'Suites' })).toBeVisible()
  const top = await primary.evaluate((el) => {
    const scroller = document.querySelector('#main-content') as HTMLElement
    return el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
  })
  test.info().annotations.push({ type: 'primary-top', description: String(Math.round(top)) })
  expect(top, '[data-primary] top, px below the top of #main-content').toBeLessThanOrEqual(FOLD_BUDGET_PX)

  // DOM order: the primary content comes before the page's own tab bar and its disclosure.
  const order = await page.evaluate(() => {
    const scroller = document.querySelector('#main-content') as HTMLElement
    const primaryEl = scroller.querySelector('[data-primary]') as HTMLElement
    const after = (el: Element) => Boolean(primaryEl.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
    const pageTabs = [...scroller.querySelectorAll('[role="tablist"][data-tabs]')]
    const disclosures = [...scroller.querySelectorAll('[data-disclosure]')]
    return { tabs: pageTabs.length, disclosures: disclosures.length, allAfter: [...pageTabs, ...disclosures].every(after) }
  })
  expect(order.tabs, 'the page has its tab bar').toBe(1)
  expect(order.disclosures, 'the score disclosure').toBe(1)
  expect(order.allAfter, 'the tab bar and the disclosure follow the primary content').toBe(true)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
