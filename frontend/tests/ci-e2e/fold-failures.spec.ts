/**
 * UX redesign P3 (`02-design-spec.md` §2, the above-the-fold contract), on
 * /failures, hermetic: at 1440 x 900 the page's primary content (the Top
 * failing table, `[data-primary]`) starts at most 300 px below the top of
 * `#main-content` (the shell's scroller: its top padding and the P1 section
 * tabs count).
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after the template can be compared: measured on the unchanged page
 * first (P3 agent C's report, `docs/viz-work/p3-agent-C.md`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the rollout specs).
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { FAILURES_ON } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => landmark(p, 'Failure verdict')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface FoldGeometry {
  scrollHeight: number
  primaryTop: number | null
  primaries: number
  /** Where each block above the primary content starts and ends, px below the scroller's top (for the report). */
  blocks: Record<string, [number, number] | null>
}

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<FoldGeometry> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const span = (css: string): [number, number] | null => {
      const box = document.querySelector(css)?.getBoundingClientRect()
      return box ? [Math.round(box.top - top), Math.round(box.bottom - top)] : null
    }
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
      blocks: {
        sectionTabs: span('[data-section-tabs]'),
        header: span('[data-page-header]'),
        banner: span('[data-status-banner]'),
        kpis: span('[data-kpi-strip]'),
      },
    }
  }, MAIN)
}

test('the Top failing table starts within the fold budget at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/failures', { handlers: FAILURES_ON, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /failures ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // It is the table of failing tests, and its first row is on screen.
  const table = page.locator('[data-primary]').getByRole('table', { name: 'Top failing tests' })
  await expect(table).toBeVisible()
  await expect(table.getByRole('row').nth(1)).toBeInViewport()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
