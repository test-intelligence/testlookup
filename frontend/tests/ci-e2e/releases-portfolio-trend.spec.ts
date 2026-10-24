/**
 * W11 REL-03 on `/releases`, hermetic (`docs/deferred-scope-2026-10-13/work/W11-contract.md` §2.5, §4.2), at 1280 and
 * 1440:
 *
 *  - the page's load asks NOTHING new: no `GET /api/v1/releases/portfolio-trend` until a reader opens **Release health
 *    · this week** (the disclosure mounts its content only while open), then exactly ONE, for the project;
 *  - the four KPI cards (In progress · Ready to ship · Blocked · Released · 30d) each draw a sparkline with its
 *    accessible name (`role="img"`), each MEASURED point a native `<title>` naming its week (the approximation said
 *    for the three status series); Ready to ship's caption `GO on 1 of 2 evaluated (12 w)` and Released · 30d's
 *    `1 of 1 released with a pack (12 w)`; the cards keep their numbers;
 *  - a failed trend read leaves the cards (no sparkline) and says `Could not load the 12-week trend.`;
 *  - `#main-content` never scrolls sideways with the strip open.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-release-planning.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, watchNativeDialogs } from '../lib/inPage'
import { expectMainFits } from '../lib/inPageGeometry'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { PORTFOLIO_CAPTIONS, QA_LEAD, releasePlanningWorld } from '../visual/production/fixtures-release-planning'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const TREND = '/api/v1/releases/portfolio-trend'
const CARDS = ['In progress', 'Ready to ship', 'Blocked', 'Released · 30d'] as const
const ready = (p: Page) => p.getByRole('heading', { name: 'Releases', level: 1 })
const health = (page: Page): Locator =>
  page.locator('section[data-disclosure]').filter({ has: page.getByRole('button', { name: /^Release health · this week/ }) })

async function openHealth(page: Page) {
  const toggle = page.getByRole('button', { name: /^Release health · this week/ })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1000 } })

    test(`the trend is read once, only when the disclosure opens: four sparklines, their titles, the two captions — ${width}`, async ({ page }) => {
      watchNativeDialogs(page)
      const world = releasePlanningWorld()
      const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
      await networkQuiet(page, api)
      expect(requestsTo(api, TREND), 'nothing new on load').toEqual([])

      await openHealth(page)
      const region = health(page)
      const sparklines = region.getByRole('img')
      await expect(sparklines).toHaveCount(CARDS.length)
      await networkQuiet(page, api)
      expect(requestsTo(api, TREND), 'ONE read, for the project').toEqual([`GET ${TREND}?project_id=${PROJECT_ID}`])

      for (const label of CARDS) await expect(region.getByText(label, { exact: true }).first()).toBeVisible()
      for (const [i] of CARDS.entries()) {
        await expect(sparklines.nth(i)).toHaveAttribute('aria-label', /\S/)
      }
      await expect(region.getByText(PORTFOLIO_CAPTIONS.ready, { exact: true })).toBeVisible()
      await expect(region.getByText(PORTFOLIO_CAPTIONS.pack, { exact: true })).toBeVisible()

      // Every measured point has a hover text; the status series say they rest on today's status.
      const titles = await region.locator('svg title').allTextContents()
      expect(titles.length, 'a title per measured point (11 per series)').toBe(4 * 11)
      expect(titles.some((t) => /^Now/.test(t)), 'the last point is now').toBe(true)
      expect(titles.filter((t) => /approximation: today's status/.test(t)).length, 'the three status series say so').toBeGreaterThan(0)
      await expectMainFits(page, `strip open @${width}`)
      await expectNoOverlay(page)
      assertHermetic(api, errors)
    })
  })
}

test('a failed trend read: the cards stay, no sparkline, "Could not load the 12-week trend."', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  const world = releasePlanningWorld({ trend: 'error' })
  const { api, errors } = await openRollout(page, '/releases', { handlers: world.handlers, ready, user: QA_LEAD })
  await networkQuiet(page, api)
  await openHealth(page)
  const region = health(page)
  await expect(region.getByText('Could not load the 12-week trend.', { exact: true })).toBeVisible()
  await expect(region.getByRole('img')).toHaveCount(0)
  for (const label of CARDS) await expect(region.getByText(label, { exact: true }).first()).toBeVisible()
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
