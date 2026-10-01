/**
 * AFTER baseline of /coverage/suite with the catalogue ON (Wave 2.6,
 * VIZ-408, plan 5.2): the pass-rate frame with trend analysis (the overlay
 * row) and the range brush, at the 240 px floor.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, PINNED, THEMES, visualRegion, waitForCharts } from '../lib/production-pages'
import { expectDrawn, openRollout, sectionFrame } from '../lib/rollout'
import { CATALOGUE_ON, SUITE, SUITE_DETAIL_ON } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`suite detail region, catalogue on — ${theme}`, async ({ page }) => {
    const { api, errors } = await openRollout(page, `/coverage/suite?name=${SUITE}&days=30`, {
      theme,
      handlers: SUITE_DETAIL_ON,
      flags: CATALOGUE_ON,
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
