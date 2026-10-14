/**
 * EXEC-04a: baselines of the run's Timing tab (`/runs/<run>?tab=timing`) for a
 * timed run (`fixtures-timing.ts` `TIMED`: two workers, twelve tests, one
 * failed at 2x its baseline):
 *
 *   run-timing-figures    the "Execution time" card: wall clock 9.0s, summed
 *                         17.4s, parallelism 1.9x, setup / teardown share,
 *                         the timing source and counts, Export CSV;
 *   run-timing-timeline   the waterfall: one row per worker, the failed bar
 *                         in the failed pattern, the time axis;
 *   run-timing-slowest    the slowest tests with today's baseline (the tick
 *                         on the bar), the setup / test / teardown split and
 *                         the worker.
 *
 * New with W02: their baselines come from CI. The same answers drive the
 * hermetic spec `tests/ci-e2e/run-timing-tab.spec.ts`.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { TEST_CASE_ID } from './production/fixtures-pages'
import { TIMED, withTiming } from './production/fixtures-timing'

// The tab is three cards tall, then the suites, workers and phases below them.
test.use({ ...PINNED, viewport: { width: 1280, height: 2400 } })

for (const theme of THEMES) {
  test(`run timing regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}?tab=timing`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: withTiming(TIMED),
      ready: (p) => p.locator('[data-chart="execution-timeline"] [data-timeline-bar]').first(),
    })

    const figures = page.getByRole('region', { name: 'Execution time' })
    await expect(figures.locator('[data-timing-kpi="wall"] [data-timing-kpi-value]')).toHaveText('9.0s')
    await expect(figures.locator('[data-timing-kpi="parallelism"] [data-timing-kpi-value]')).toHaveText('1.9×')
    await visualRegion(page, 'run-timing-figures', theme, figures)

    const timeline = page.getByRole('region', { name: 'Timeline' })
    await expect(timeline.locator('[data-timeline-lane]')).toHaveCount(2)
    await expect(timeline.locator('[data-timeline-bar]')).toHaveCount(12)
    await visualRegion(page, 'run-timing-timeline', theme, timeline)

    const slowest = page.getByRole('region', { name: 'Slowest tests' })
    await expect(slowest.locator(`[data-timing-slow-test="${TEST_CASE_ID}"]`)).toContainText('2× baseline')
    await visualRegion(page, 'run-timing-slowest', theme, slowest)
    assertHermetic(api, errors)
  })
}
