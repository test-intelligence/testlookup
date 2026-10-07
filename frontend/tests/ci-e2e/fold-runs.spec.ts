/**
 * UX redesign P3 (`02-design-spec.md` §2, the above-the-fold contract), on
 * /runs, hermetic: at 1440 x 900 the page's primary content (the runs table,
 * `[data-primary]`) starts at most 300 px below the top of `#main-content`
 * (the shell's scroller: its top padding and the P1 section tabs count).
 *
 * The scroller's `scrollHeight` is printed (FOLD line) so the page's height
 * before / after the template can be compared: measured on the unchanged page
 * first (P3 agent B's report, `docs/viz-work/p3-agent-B.md`: 1748 px, the
 * table at 652 px).
 *
 * Also held here, in a browser: the failure signatures open in the side panel
 * (beside the page, not under the table), and the two Disclosures below the
 * table are closed on arrival.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the visual specs: 13 builds in 30 days, the newest two red).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { RUNS_PAGE } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const ready = (p: Page) => p.locator('[data-status-banner]')

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

test('the runs table starts within the fold budget at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /runs ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // It is the runs table, and its first build is on screen.
  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('table')).toBeVisible()
  await expect(primary.getByRole('row').nth(1)).toBeInViewport()
  // Above it: the verdict in one line, not a card.
  await expect(page.locator('[data-status-banner]')).toBeInViewport()
  await expect(page.locator('section[aria-label="Pipeline verdict"]')).toHaveCount(0)
  // Below it: the gauge and the build history, collapsed.
  await expect(page.locator('[data-disclosure][data-open="false"]')).toHaveCount(2)
  await expect(page.getByRole('meter', { name: 'Pipeline health' })).toHaveCount(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the failure signatures open beside the page, from the header and from a row', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/runs', { handlers: RUNS_PAGE, ready })
  const panel = page.getByRole('complementary', { name: 'Failure signatures' })
  await expect(panel).toHaveCount(0)
  await page.getByRole('button', { name: /^Failure signatures/ }).click()
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('meter', { name: 'Failed builds by signature' })).toBeVisible()
  // Non-modal: the table stays usable beside it (the panel reserves its width, never covers it).
  const [panelBox, primaryBox] = [await panel.boundingBox(), await page.locator('[data-primary]').boundingBox()]
  expect(panelBox && primaryBox && primaryBox.x + primaryBox.width <= panelBox.x + 1, 'the table ends where the panel begins').toBe(true)
  await panel.getByRole('button', { name: 'Close panel' }).click()
  await expect(panel).toHaveCount(0)
  // A row's signature chip opens it too.
  await page.locator('[data-signature-chip]').first().click()
  await expect(panel).toBeVisible()
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
