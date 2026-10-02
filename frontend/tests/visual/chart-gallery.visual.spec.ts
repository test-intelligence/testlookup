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
  galleryDomMarks,
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
  // VIZ-404 — the multi-series comparison, one item per edge case. The chart
  // reads no clock and no zone: its days are the fixture's literal UTC days.
  'multi-series-three-suites',
  'multi-series-folded',
  'multi-series-gaps',
  'multi-series-not-comparable',
  'multi-series-release-aligned',
  'multi-series-hidden',
  // VIZ-405 — the trend overlays: both drawn (with the flagged day), and both
  // unavailable with the reason. Pinned to `GALLERY_NOW` like the time series.
  'timeseries-trend-analysis',
  'timeseries-trend-insufficient',
  // Wave 2.5 (VIZ-104) — the kit pieces every production chart now draws
  // with: the stacked columns, the rate target, the sparkline, the gauge
  // bar, the ring gauge and the day strip, one item per edge case their
  // builders named, a hostile name in each piece that prints one, and a
  // strip at phone width.
  'stacked-status-daily',
  'stacked-series-monthly',
  'stacked-hostile-labels',
  'stacked-single-bucket',
  'stacked-long-window',
  // FX-kit (R2's accepted design call): more categories than columns can name, drawn as bars.
  'stacked-many-categories',
  'timeseries-rate-target',
  'timeseries-rate-target-off-axis',
  'sparkline-pass-rate',
  'sparkline-gaps',
  'sparkline-flat',
  'sparkline-executions',
  'sparkline-too-few',
  'gauge-bar-marker',
  'gauge-bar-fill-risk',
  'gauge-bar-not-measured',
  'gauge-bar-segments',
  'gauge-bar-target',
  'gauge-bar-clamped',
  'gauge-bar-inline',
  'gauge-bar-hostile-label',
  'ring-gauge-risk',
  'ring-gauge-not-measured',
  'ring-gauge-hostile-label',
  'day-strip-presence',
  'day-strip-presence-narrow',
  'day-strip-intensity',
  'day-strip-severity',
  'day-strip-compact',
  'day-strip-builds',
  'day-strip-dense',
  'day-strip-hostile-label',
  // Wave 2.6 (VIZ-408) — the heatmap frame the Trends catalogue draws (the
  // 14-day default, the 90-day maximum, a hostile suite name), and ranked bars
  // whose names are Object members (`constructor`, `__proto__`...).
  'heatmap-frame',
  'heatmap-frame-90d',
  'heatmap-frame-hostile',
  'bar-ranked-prototype-names',
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
      // A `dom` item (the ranked slowest-tests list; since Wave 2.5 the
      // sparkline, the gauge bar and the day strip) has no engine to report
      // ready, so wait until its own marks are drawn: a screenshot must not
      // race React's first paint any more than it may race
      // ResponsiveContainer's. A mark with a width OR a height counts (a flat
      // sparkline's line has no height).
      for (const item of GALLERY_DRAWN_DOM_ITEMS) {
        const marks = page.locator(`[data-gallery-item="${item.id}"]`).locator(galleryDomMarks(item))
        await expect
          .poll(() =>
            marks.evaluateAll((nodes) =>
              nodes.filter((node) => {
                const box = node.getBoundingClientRect()
                return box.width > 0 || box.height > 0
              }).length,
            ),
          )
          .toBeGreaterThanOrEqual(Math.max(1, item.minMarks))
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
