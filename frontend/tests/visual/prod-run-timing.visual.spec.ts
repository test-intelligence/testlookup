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
 *
 * W39 STRAT-07 adds ONE region below them (no existing region changes: the
 * card is the tab's LAST section, so the three above keep their frames), in
 * three themes (`THEMES_WITH_PAPER`) at `TALL_VIEWPORT` (1280 x 4000, the rest
 * of `PINNED` unchanged; the tab is taller than 2400 px with it):
 *
 *   run-observability-evidence  the "Observability evidence" card with
 *                         `fixtures-observability.ts` `OBSERVABILITY_EVIDENCE`:
 *                         one axis, the run band, a Prometheus series that
 *                         rose, two Splunk trace dots, an OpenShift bar and an
 *                         instant, a per-test Splunk dot and an untimed line,
 *                         a per-test OpenShift check without a time.
 *
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, TALL_VIEWPORT, THEMES, THEMES_WITH_PAPER, visualRegion } from '../lib/production-pages'
import { networkQuiet } from '../lib/rollout'
import { NOW, PROJECT_ID, RUN_ID, USER } from './production/fixtures'
import { TEST_CASE_ID } from './production/fixtures-pages'
import { OBSERVABILITY_EVIDENCE } from './production/fixtures-observability'
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

test.describe('W39 STRAT-07 — the Observability evidence card', () => {
  // Only the viewport changes (a describe may not change launch options): `PINNED` with `TALL_VIEWPORT`.
  test.use({ viewport: { ...TALL_VIEWPORT } })

  for (const theme of THEMES_WITH_PAPER) {
    test(`run observability evidence region — ${theme}`, async ({ page }) => {
      const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}?tab=timing`, {
        theme,
        now: NOW,
        me: USER,
        user: USER,
        projectId: PROJECT_ID,
        handlers: withTiming(TIMED, OBSERVABILITY_EVIDENCE),
        ready: (p) => p.locator('[data-observability-evidence] [data-observability-state="ready"]'),
      })
      const card = page.getByRole('region', { name: 'Observability evidence' })
      await expect(card).toHaveAttribute('data-axis-state', 'timed')
      await expect(card.locator('[data-evidence-series-plot]')).toHaveCount(1)
      await expect(card.locator('[data-evidence-mark="instant"]')).toHaveCount(4)
      await expect(card.locator('[data-evidence-mark="span"]')).toHaveCount(1)
      await expect(card.locator('[data-untimed-item]')).toHaveCount(2)
      await networkQuiet(page, api)
      await visualRegion(page, 'run-observability-evidence', theme, card)
      assertHermetic(api, errors)
    })
  }
})
