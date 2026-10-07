/**
 * The `/settings` index (UX redesign P5): one line per page, its name and what
 * it is for. Both cut with an ellipsis when they run out of room, which JSDOM
 * cannot measure. The P5 baselines showed "Jira, Splunk, OCP, Slack, Te…" in
 * CI's DejaVu Sans: the name column was 192 px, the name 201.
 *
 * At 1280 and 1440 px, as a QA lead (every group), no name and no description
 * is cut, and the index never scrolls sideways.
 *
 * Hermetic and fail closed (`production-pages.ts`): every request is mocked.
 */
import { expect, test, type Page } from '@playwright/test'
import { assertHermetic, expectNoHorizontalOverflow, networkQuiet, openRollout } from '../lib/rollout'
import { LAYOUT } from '../visual/production/fixtures'

/** Every truncated text in the index whose content is wider than its box. */
async function cutTexts(page: Page): Promise<string[]> {
  return page.locator('[data-settings-index] a > span').evaluateAll((spans) =>
    spans.filter((s) => s.scrollWidth > s.clientWidth).map((s) => `${s.textContent} (${s.scrollWidth} > ${s.clientWidth})`),
  )
}

for (const width of [1280, 1440]) {
  test(`at ${width} px, every settings page's name and purpose fit on their line`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { api, errors } = await openRollout(page, '/settings', {
      handlers: LAYOUT,
      ready: (p) => p.locator('[data-settings-index]'),
    })
    await networkQuiet(page, api)
    // The fonts the cut is measured in, not the fallback before they load.
    await page.evaluate(() => document.fonts.ready)
    // Not vacuous: all seven groups, every page's two texts.
    const items = page.locator('[data-settings-index-item]')
    expect(await items.count()).toBeGreaterThan(20)
    expect(await page.locator('[data-settings-index] a > span').count()).toBe((await items.count()) * 2)
    expect(await cutTexts(page)).toEqual([])
    await expectNoHorizontalOverflow(page, `/settings at ${width} px`)
    assertHermetic(api, errors)
  })
}
