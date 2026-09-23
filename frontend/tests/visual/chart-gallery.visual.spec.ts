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
  GALLERY_DRAWN_DOM_ITEMS,
  GALLERY_DRAWN_SVG_ITEMS,
  GALLERY_ITEM_IDS,
  GALLERY_ITEMS,
  galleryEngine,
} from '../../src/pages/dev/chartGalleryFixtures'

const GALLERY_CANVAS_ITEM_IDS = GALLERY_ITEMS.filter((item) => galleryEngine(item) === 'echarts').map(
  (item) => item.id,
)

/**
 * The Wave-2 baselines (VIZ-401 / VIZ-402). The per-item tests below are
 * generated from the fixtures, so these get a screenshot each without being
 * named here — but a dropped edge case would then silently lose its baseline
 * and nothing would fail. So they ARE named, once, and asserted to still be in
 * the gallery. Their PNGs are generated on CI (`npm run test:visual:update`);
 * none is committed from a developer machine, where fonts and DPI differ.
 */
const WAVE_2_ITEM_IDS = [
  'donut-status',
  'donut-status-unknown',
  'donut-single-status',
  'donut-tiny-slice',
  'donut-all-zero',
  'bar-ranked',
  'bar-ranked-ties',
  'bar-long-names',
  'bar-diverging',
  'bar-paginated',
  'bar-hostile-label',
  'bar-stacked',
  'bar-stacked-100',
  'bar-grouped',
  'breakdown-four-categories',
  'breakdown-six-categories',
  // VIZ-403 — the time series. Every pixel here is fixed by `GALLERY_NOW` and
  // `GALLERY_TIME_ZONE`: without them the partial-day note and the tooltip's
  // local equivalent would differ by machine and by the day the baseline ran.
  'timeseries-trend-releases',
  'timeseries-single-point',
  'timeseries-zoomed-axis',
  // VIZ-406 — the duration charts.
  'duration-histogram',
  'duration-histogram-empty',
  'duration-band',
  'slowest-tests',
] as const

test('every Wave-2 edge case still has a gallery item, and so a baseline', () => {
  for (const id of WAVE_2_ITEM_IDS) expect(GALLERY_ITEM_IDS, id).toContain(id)
})

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
      // A `dom` item (the ranked slowest-tests list) has no engine to report
      // ready, so wait on its own first bar: a screenshot must not race React's
      // first paint any more than it may race ResponsiveContainer's.
      for (const item of GALLERY_DRAWN_DOM_ITEMS) {
        await expect(
          page.locator(`[data-gallery-item="${item.id}"] [data-testid="ranked-bar"]`).first(),
        ).toBeVisible()
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
