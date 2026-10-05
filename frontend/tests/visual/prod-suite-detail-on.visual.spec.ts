/**
 * AFTER baseline of /coverage/suite with the catalogue ON (Wave 2.6,
 * VIZ-408, plan 5.2): the pass-rate frame with trend analysis (the overlay
 * row) and the range brush, at the 240 px floor.
 * Wave 3 (both flags; lands WITH its PNGs): the test x run status heatmap
 * (`suite-on-test-run`) and the test scatter (`suite-on-scatter`, then with
 * "Select slow and flaky" applied: `suite-on-scatter-selected`). The
 * catalogue-only region above keeps its PNG.
 * Phase D S3: the page asks no flag; the pass-rate region is captured with
 * every flag off (the same frame the catalogue flag used to add). S4: the
 * test x run heatmap asks no flag either (it now mounts below the pass-rate
 * region with every flag off). S5: the scatter asks no flag either (the
 * Wave 3 test sets none), and its toolbar now always offers "View in 3D", so
 * `suite-on-scatter` and `suite-on-scatter-selected` gain that button.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, expectNoErrorFrame, mountEverySection, openRollout, section, sectionFrame } from '../lib/rollout'
import { SUITE, SUITE_DETAIL_ON } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`suite detail region, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=30`, {
      theme,
      handlers: SUITE_DETAIL_ON,
      ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
    })
    const passRate = sectionFrame(page, 'suite-pass-rate', /^Pass rate trend/)
    await expectDrawn(passRate, 'pass rate')
    await waitForCharts(passRate)
    await expect(passRate.locator('[data-trend-controls]')).toBeVisible()
    await expect(passRate.locator('[data-chart-brush]')).toBeVisible()

    await visualRegion(page, 'suite-on-pass-rate', theme, passRate)
    assertHermetic(api, errors)
  })
}

test.describe('Wave 3 sections', () => {
  // The page with both sections is taller than PINNED's 2400 px (only the height changes).
  test.use({ viewport: { width: 1280, height: 4000 } })

  for (const theme of THEMES) {
    test(`suite detail Wave 3 regions — ${theme}`, async ({ page }) => {
      const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=30`, {
        theme,
        handlers: SUITE_DETAIL_ON,
        ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
      })
      await mountEverySection(page, api)
      const heatmap = sectionFrame(page, 'heatmap-test_run', 'Test results by run')
      const scatter = sectionFrame(page, 'scatter-suite', 'Test duration vs failure rate')
      await expectDrawn(heatmap, 'test x run')
      await expectDrawn(scatter, 'scatter')
      await expect(heatmap.locator('canvas').first()).toBeVisible()
      await expect(scatter.locator('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready')
      await expectNoErrorFrame(page)
      await visualRegion(page, 'suite-on-test-run', theme, heatmap)
      await visualRegion(page, 'suite-on-scatter', theme, section(page, 'scatter-suite'))
      await section(page, 'scatter-suite').getByRole('button', { name: 'Select slow and flaky' }).click()
      await expect(section(page, 'scatter-suite').locator('[data-scatter-selection-count]')).toBeVisible()
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
      await visualRegion(page, 'suite-on-scatter-selected', theme, section(page, 'scatter-suite'))
      assertHermetic(api, errors)
    })
  }
})
