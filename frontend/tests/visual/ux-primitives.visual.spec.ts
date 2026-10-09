/**
 * Pixel baselines for the UX redesign's primitives (P0): one screenshot per
 * gallery section per theme, from `/__ux-primitives` (fixed data, no backend),
 * plus the header's **⋯** menu open and a tab chosen through `?tab=`.
 *
 * Two themes, `signal` (dark, the default) and `lab` (light), as the chart
 * gallery does. Baselines are generated on CI (`visual-baselines.yml`), never
 * committed from a developer machine.
 */
import { expect, test } from '@playwright/test'

const THEMES = ['signal', 'lab'] as const
const SECTIONS = ['page-header', 'route-tabs', 'status-banner', 'kpi-strip', 'window-picker', 'disclosure'] as const

test.use({ viewport: { width: 1280, height: 1600 } })

for (const theme of THEMES) {
  test.describe(`theme: ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.goto(`/__ux-primitives?theme=${theme}`)
      expect(new URL(page.url()).pathname, 'the gallery route redirected').toBe('/__ux-primitives')
      // A lazy route: its module graph loads after `goto` settles (see the
      // chart gallery's spec, where 3 of 298 timed out on the theme check).
      await expect(page.getByTestId('ux-primitives-gallery')).toBeVisible({ timeout: 30_000 })
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.locator('[data-ux-primitive]')).toHaveCount(SECTIONS.length)
    })

    test('every tab is 38 px, with a count or without (labels on one line)', async ({ page }) => {
      for (const bar of ['route-tabs', 'page-header']) {
        const heights = await page
          .locator(`[data-ux-primitive="${bar}"]`)
          .locator('[data-route-tab], [role="tab"]')
          .evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height))
        expect(heights.length, bar).toBeGreaterThan(2)
        expect(new Set(heights), `${bar} tab heights`).toEqual(new Set([38]))
      }
    })

    for (const id of SECTIONS) {
      test(id, async ({ page }) => {
        const section = page.locator(`[data-ux-primitive="${id}"]`)
        await section.scrollIntoViewIfNeeded()
        await expect(section).toHaveScreenshot(`ux-primitives/${id}--${theme}.png`)
      })
    }

    test('the overflow menu open', async ({ page }) => {
      await page.locator('[data-overflow-trigger]').click()
      const menu = page.getByRole('menu', { name: 'More actions' })
      await expect(menu.getByRole('menuitem')).toHaveCount(3)
      await page.mouse.move(0, 0)
      await expect(menu).toHaveScreenshot(`ux-primitives/overflow-open--${theme}.png`)
    })

    test('a tab chosen through ?tab= is selected after a reload', async ({ page }) => {
      await page.getByRole('tab', { name: /Failures/ }).click()
      await expect(page).toHaveURL(/[?&]tab=failures/)
      await page.reload()
      await expect(page.getByRole('tab', { name: /Failures/ })).toHaveAttribute('aria-selected', 'true')
      await page.mouse.move(0, 0)
      await expect(page.locator('[data-ux-primitive="page-header"]')).toHaveScreenshot(`ux-primitives/page-header-failures-tab--${theme}.png`)
    })
  })
}
