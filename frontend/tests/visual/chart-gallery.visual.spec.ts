/**
 * Pixel baselines: one screenshot per gallery item per theme.
 *
 * Two themes — `signal` (the default, dark) and `lab` (the only light one) —
 * because the status hues differ between the dark set and the light theme by
 * design (see `scripts/check-theme-tokens.mjs`), so a regression can hide in
 * either. The item list comes from the gallery's own fixtures module, so a
 * new gallery item gets a test (and needs a baseline) without editing this
 * file. A REMOVED item's baseline is not detected: Playwright has no
 * orphaned-snapshot check, so the old PNG simply stays on disk, unused, and
 * nothing fails. Delete it by hand in the change that removes the item.
 *
 * A missing baseline FAILS (Playwright writes the actual and reports it); a
 * new one is admitted only through `npm run test:visual:update`.
 */
import { expect, test } from '@playwright/test'
import {
  GALLERY_DRAWN_SVG_ITEMS,
  GALLERY_ITEM_IDS,
  GALLERY_ITEMS,
  galleryEngine,
} from '../../src/pages/dev/chartGalleryFixtures'

const GALLERY_CANVAS_ITEM_IDS = GALLERY_ITEMS.filter((item) => galleryEngine(item) === 'echarts').map(
  (item) => item.id,
)

const THEMES = ['signal', 'lab'] as const

for (const theme of THEMES) {
  test.describe(`theme: ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.goto(`/__charts?theme=${theme}`)
      expect(new URL(page.url()).pathname, 'the gallery route redirected').toBe('/__charts')
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.locator('[data-gallery-item]')).toHaveCount(GALLERY_ITEM_IDS.length)
      // Every chart has measured and drawn before any item is captured, so a
      // screenshot never races ResponsiveContainer's first layout pass.
      for (const item of GALLERY_DRAWN_SVG_ITEMS) {
        await expect(
          page.locator(`[data-gallery-item="${item.id}"] .recharts-wrapper > svg.recharts-surface path`).first(),
        ).toBeVisible()
      }
      // ECharts items (canvas) report ready once the engine has drawn — and
      // every one does, empty ones included, so none is captured mid-load.
      for (const id of GALLERY_CANVAS_ITEM_IDS) {
        await expect(
          page.locator(`[data-gallery-item="${id}"] [data-chart-engine="echarts"]`),
        ).toHaveAttribute('data-chart-status', 'ready')
      }
    })

    for (const id of GALLERY_ITEM_IDS) {
      test(id, async ({ page }) => {
        const section = page.locator(`[data-gallery-item="${id}"]`)
        await section.scrollIntoViewIfNeeded()
        await expect(section).toHaveScreenshot(`${id}--${theme}.png`)
      })
    }
  })
}
